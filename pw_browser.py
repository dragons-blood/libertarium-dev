#!/usr/bin/env python3
"""
PLINY PLAYWRIGHT BROWSER
Background browser for agent computer use — no focus stealing.
Uses Playwright to control a Chromium instance via DevTools Protocol.
Imports cookies from the user's real Firefox profile.

The browser runs headless (or headed but unfocused) and the agent
interacts with it via HTTP API calls. No cliclick, no osascript,
no window switching.

API (called from agent's bash):
  curl localhost:8787/screenshot > /tmp/screen.png
  curl localhost:8787/navigate -d '{"url":"https://x.com"}'
  curl localhost:8787/click -d '{"x":100,"y":200}'
  curl localhost:8787/type -d '{"text":"hello world"}'
  curl localhost:8787/press -d '{"key":"Enter"}'
  curl localhost:8787/status
"""

import base64
import json
import os
import random
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import HTTPServer, ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

# Add playwright to path if needed
PW_BIN = Path.home() / "Library/Python/3.9/bin"
if str(PW_BIN) not in os.environ.get("PATH", ""):
    os.environ["PATH"] = str(PW_BIN) + ":" + os.environ.get("PATH", "")

FIREFOX_PROFILES = Path.home() / "Library/Application Support/Firefox/Profiles"
AGENT_FIREFOX_PROFILE = Path.home() / ".pliny-agent-pw-firefox"
PORT = 8787
SS_PATH = "/tmp/pliny_pw_screen.png"
# Off-screen window coords pre-seeded into xulstore so Firefox opens already
# hidden — no visible slide-to-corner. Negative on both axes puts it off any
# physical display. macOS WindowServer still renders normally (unlike when
# minimized), so Playwright navigations don't hang on throttled load events.
_OFFSCREEN_X = "-3000"
_OFFSCREEN_Y = "-3000"

# Global state
_browser = None
_page = None
_context = None
_lock = threading.Lock()
_paused = False  # When True, mutating endpoints return 423 (user has the wheel)

# Endpoints that perform user-style actions (click/type/navigate/etc.)
# Paused endpoints return 423 Locked so the agent can wait or back off.
PAUSE_GATED_PATHS = {
    "/navigate", "/click", "/type", "/text", "/paste", "/press", "/scroll",
    "/screenshot", "/click_element", "/fill", "/upload_files", "/tweet",
    "/thread", "/dragonfire",
}


def bring_firefox_to_front():
    """Best-effort focus the Playwright Firefox window (macOS)."""
    try:
        import subprocess
        subprocess.run(
            ["osascript", "-e", 'tell application "Firefox" to activate'],
            capture_output=True, timeout=2,
        )
    except Exception:
        pass


def find_firefox_profile() -> Path:
    """Find the user's default Firefox profile."""
    if not FIREFOX_PROFILES.exists():
        return None
    for p in FIREFOX_PROFILES.iterdir():
        if p.is_dir() and "default-release" in p.name:
            return p
    for p in FIREFOX_PROFILES.iterdir():
        if p.is_dir() and "default" in p.name:
            return p
    return None


def extract_firefox_cookies(profile_path: Path) -> list:
    """Extract cookies from Firefox's cookies.sqlite.
    Returns list of Playwright cookie dicts.

    Firefox moz_cookies.expiry is a Unix timestamp in SECONDS (not ms).
    We skip truly expired cookies and enforce Playwright's requirement that
    sameSite='None' cookies must also have secure=True.
    """
    db_path = profile_path / "cookies.sqlite"
    if not db_path.exists():
        return []

    import shutil
    import subprocess
    tmp_db = "/tmp/pliny_ff_cookies.sqlite"

    # Checkpoint WAL first so the backup includes pending writes (login sessions)
    try:
        subprocess.run(
            ["sqlite3", str(db_path), "PRAGMA wal_checkpoint(TRUNCATE);"],
            capture_output=True, timeout=10
        )
    except Exception:
        pass  # Non-fatal — backup will still get most data

    try:
        r = subprocess.run(
            ["sqlite3", str(db_path), f".backup '{tmp_db}'"],
            capture_output=True, timeout=10
        )
        if r.returncode != 0:
            shutil.copy2(db_path, tmp_db)
            # Also copy WAL/SHM if they exist (has uncommitted data)
            for ext in ("-wal", "-shm"):
                wal = Path(str(db_path) + ext)
                if wal.exists():
                    shutil.copy2(wal, tmp_db + ext)
    except Exception:
        try:
            shutil.copy2(db_path, tmp_db)
        except Exception:
            return []

    cookies = []
    now = time.time()
    skipped_expired = 0
    try:
        conn = sqlite3.connect(tmp_db)
        cursor = conn.execute(
            "SELECT host, name, value, path, expiry, isSecure, isHttpOnly, sameSite FROM moz_cookies"
        )
        for row in cursor:
            host, name, value, path, expiry, is_secure, is_httponly, same_site = row
            domain = host
            if not name or not value:
                continue

            # Skip Cloudflare cookies — they're bound to the original browser's
            # TLS fingerprint and will BREAK auth if imported into Playwright.
            # Without them, Cloudflare serves a solvable challenge page.
            # With mismatched ones, it serves a broken "Application Error" page.
            if name in ("cf_clearance", "__cf_bm", "cf_chl_2", "cf_chl_prog"):
                continue

            cookie = {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path or "/",
                "secure": bool(is_secure),
                "httpOnly": bool(is_httponly),
            }

            # Firefox moz_cookies stores expiry as Unix timestamps.
            # Newer Firefox versions may store MILLISECONDS (13-digit values > 1e12).
            # Values of 0 mean session cookie.
            if expiry and isinstance(expiry, (int, float)) and expiry > 0:
                exp_seconds = expiry / 1000 if expiry > 1e12 else expiry
                if exp_seconds > now:
                    cookie["expires"] = float(exp_seconds)
                else:
                    skipped_expired += 1
                    continue
            else:
                cookie["expires"] = -1  # genuine session cookie

            # sameSite mapping: Firefox 0=None, 1=Lax, 2=Strict, 256=unset
            # IMPORTANT: Playwright's Firefox engine often silently drops or errors
            # on sameSite="None" cookies during add_cookies(), even when secure=True.
            # Safest approach: use "Lax" for everything — the browser will still send
            # cookies on same-site navigations and top-level cross-site requests,
            # which covers login persistence. "Strict" stays Strict.
            ss_map = {2: "Strict"}
            same_site_val = ss_map.get(same_site, "Lax")
            cookie["sameSite"] = same_site_val

            cookies.append(cookie)
        conn.close()
    except Exception as e:
        print(f"Cookie extraction error: {e}", file=sys.stderr)

    if skipped_expired:
        print(f"Skipped {skipped_expired} expired cookies", file=sys.stderr)

    try:
        os.unlink(tmp_db)
        for ext in ("-wal", "-shm"):
            try:
                os.unlink(tmp_db + ext)
            except Exception:
                pass
    except Exception:
        pass

    return cookies


def _apply_stealth(page):
    """Apply stealth patches to hide automation signals from Cloudflare et al.

    Patches are designed to pass creepjs, fingerprint.com, and bot.sannysoft.com.
    Each patch addresses a specific detection vector with realistic values.
    """
    try:
        from playwright_stealth import stealth_sync
        stealth_sync(page)
        print("Stealth patches applied (playwright_stealth)", file=sys.stderr)
    except ImportError:
        print("playwright_stealth not installed, applying manual patches", file=sys.stderr)
    # Additional manual patches (applied regardless — belt and suspenders)
    try:
        page.add_init_script("""
            // ── 1. Webdriver flag (most basic check) ────────────────────────
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            try { delete navigator.__proto__.webdriver; } catch(e) {}

            // ── 2. Plugins (headless has 0; real Chrome has 3-5 DIFFERENT ones) ──
            // Bot detectors flag identical plugin entries — use realistic variety
            const _pluginData = [
                { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer', mimeType: 'application/x-google-chrome-pdf' },
                { name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', mimeType: 'application/pdf' },
                { name: 'Native Client', description: '', filename: 'internal-nacl-plugin', mimeType: 'application/x-nacl' },
            ];
            const _makePlugin = (d) => {
                const p = Object.create(Plugin.prototype);
                Object.defineProperties(p, {
                    name: { get: () => d.name },
                    description: { get: () => d.description },
                    filename: { get: () => d.filename },
                    length: { get: () => 1 },
                    0: { get: () => ({ type: d.mimeType, suffixes: '', description: d.description, enabledPlugin: p }) },
                });
                return p;
            };
            const _plugins = _pluginData.map(_makePlugin);
            Object.defineProperty(navigator, 'plugins', {
                get: () => {
                    const arr = Object.create(PluginArray.prototype);
                    _plugins.forEach((p, i) => { arr[i] = p; });
                    Object.defineProperty(arr, 'length', { get: () => _plugins.length });
                    arr.item = (i) => _plugins[i] || null;
                    arr.namedItem = (n) => _plugins.find(p => p.name === n) || null;
                    arr.refresh = () => {};
                    return arr;
                },
            });

            // ── 3. Languages ────────────────────────────────────────────────
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
            Object.defineProperty(navigator, 'language', { get: () => 'en-US' });

            // ── 4. Chrome object (must look like real Chrome, not a stub) ───
            window.chrome = {
                app: { isInstalled: false, InstallState: { DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed' }, RunningState: { CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running' } },
                runtime: { OnInstalledReason: { CHROME_UPDATE: 'chrome_update', INSTALL: 'install', SHARED_MODULE_UPDATE: 'shared_module_update', UPDATE: 'update' }, OnRestartRequiredReason: { APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic' }, PlatformArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' }, PlatformNaclArch: { ARM: 'arm', MIPS: 'mips', MIPS64: 'mips64', X86_32: 'x86-32', X86_64: 'x86-64' }, PlatformOs: { ANDROID: 'android', CROS: 'cros', LINUX: 'linux', MAC: 'mac', OPENBSD: 'openbsd', WIN: 'win' }, RequestUpdateCheckStatus: { NO_UPDATE: 'no_update', THROTTLED: 'throttled', UPDATE_AVAILABLE: 'update_available' }, connect: function() {}, sendMessage: function() {}, id: undefined },
                csi: function() { return { onloadT: Date.now(), startE: Date.now(), pageT: 3000 + Math.random() * 2000 }; },
                loadTimes: function() { return { commitLoadTime: Date.now() / 1000, connectionInfo: 'h2', finishDocumentLoadTime: Date.now() / 1000 + 0.3, finishLoadTime: Date.now() / 1000 + 0.5, firstPaintAfterLoadTime: 0, firstPaintTime: Date.now() / 1000 + 0.1, navigationType: 'Other', npnNegotiatedProtocol: 'h2', requestTime: Date.now() / 1000 - 0.5, startLoadTime: Date.now() / 1000 - 0.3, wasAlternateProtocolAvailable: false, wasFetchedViaSpdy: true, wasNpnNegotiated: true }; },
            };

            // ── 5. Permissions API (notifications check) ────────────────────
            const _origQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
            window.navigator.permissions.query = (params) => {
                if (params.name === 'notifications') {
                    return Promise.resolve({ state: Notification.permission, onchange: null });
                }
                return _origQuery(params);
            };

            // ── 6. Hardware fingerprint (headless often has 0/undefined) ─────
            Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
            Object.defineProperty(navigator, 'deviceMemory', { get: () => 8 });
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 }); // desktop

            // ── 7. WebGL renderer (headless leaks 'SwiftShader') ────────────
            const _getParam = WebGLRenderingContext.prototype.getParameter;
            WebGLRenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return 'Google Inc. (Apple)';          // UNMASKED_VENDOR
                if (param === 37446) return 'ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)'; // UNMASKED_RENDERER
                return _getParam.call(this, param);
            };
            const _getParam2 = WebGL2RenderingContext.prototype.getParameter;
            WebGL2RenderingContext.prototype.getParameter = function(param) {
                if (param === 37445) return 'Google Inc. (Apple)';
                if (param === 37446) return 'ANGLE (Apple, Apple M1 Pro, OpenGL 4.1)';
                return _getParam2.call(this, param);
            };

            // ── 8. Connection API (headless missing) ────────────────────────
            if (!navigator.connection) {
                Object.defineProperty(navigator, 'connection', {
                    get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }),
                });
            }

            // ── 9. Prevent iframe detection of automation ───────────────────
            // Some sites create an iframe and check its navigator.webdriver
            const _attachShadow = Element.prototype.attachShadow;
            Element.prototype.attachShadow = function() {
                return _attachShadow.apply(this, arguments);
            };
        """)
    except Exception as e:
        print(f"Manual stealth patch warning: {e}", file=sys.stderr)


def _get_firefox_version() -> str:
    """Detect installed Firefox version for matching user-agent."""
    import subprocess
    try:
        r = subprocess.run(
            ["/Applications/Firefox.app/Contents/MacOS/firefox", "--version"],
            capture_output=True, text=True, timeout=5
        )
        # Output: "Mozilla Firefox 149.0.2"
        parts = r.stdout.strip().split()
        if len(parts) >= 3:
            return parts[-1]  # "149.0.2"
    except Exception:
        pass
    return "149.0"  # Fallback


def _seed_offscreen_xulstore(profile_dir: Path) -> bool:
    """Pre-seed Firefox's xulstore.json with off-screen window coords so the
    browser opens already hidden. Returns True if the file was written.

    Why: previously we let Firefox open at default coords (screenX=3, screenY=33)
    and then ran osascript to slide it off-screen. That sliding was visible to
    the user. Pre-seeding xulstore makes the window open at -3000,-3000 directly.

    We preserve any other xulstore entries (sidebar state, etc.) so the agent's
    UI prefs stick across launches.
    """
    profile_dir.mkdir(parents=True, exist_ok=True)
    xulstore_path = profile_dir / "xulstore.json"
    try:
        data: dict = {}
        if xulstore_path.exists():
            try:
                data = json.loads(xulstore_path.read_text() or "{}")
            except Exception:
                data = {}
        browser_key = "chrome://browser/content/browser.xhtml"
        browser_node = data.setdefault(browser_key, {})
        main_window = browser_node.setdefault("main-window", {})
        main_window["screenX"] = _OFFSCREEN_X
        main_window["screenY"] = _OFFSCREEN_Y
        main_window.setdefault("width", "1280")
        main_window.setdefault("height", "775")
        main_window["sizemode"] = "normal"
        xulstore_path.write_text(json.dumps(data))
        return True
    except Exception as e:
        print(f"xulstore seed failed: {e}", file=sys.stderr)
        return False


def _find_playwright_firefox_pids() -> set[int]:
    """Return PIDs of Playwright-launched Firefox parent processes ONLY.

    Detection signal: `-juggler-pipe` in argv. That's Playwright's Gecko
    remote-protocol flag — never present in the user's normal Firefox.
    Using a unique argv flag (vs. binary path or process-tree diffing)
    is the cleanest, race-free way to distinguish.
    """
    pids: set[int] = set()
    try:
        out = subprocess.run(
            ["pgrep", "-fl", "juggler-pipe"],
            capture_output=True, text=True, timeout=2,
        )
        for line in out.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            # Skip child processes (have -contentproc or -parentPid in argv)
            argv = parts[1] if len(parts) > 1 else ""
            if "-contentproc" in argv or "-parentPid" in argv:
                continue
            pids.add(pid)
    except Exception:
        pass
    return pids


def launch_browser(headless: bool = True):
    """Launch Playwright browser with Firefox cookies and matching fingerprint.

    Uses Playwright's REAL Firefox engine (Gecko) — not Chromium pretending to be Firefox.
    This is critical for avoiding headless detection on X/Twitter and other sites that
    fingerprint the browser engine (WebGL renderer, canvas hash, JS engine behavior).
    Cookies came from Firefox, engine IS Firefox, UA matches Firefox = fully consistent.
    Falls back to stealth Chromium only if Firefox engine fails.
    """
    global _browser, _page, _context

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()


    ff_version = _get_firefox_version()
    ff_major = ff_version.split(".")[0]
    firefox_ua = f"Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:{ff_major}.0) Gecko/20100101 Firefox/{ff_major}.0"
    print(f"Firefox {ff_version} detected, UA: {firefox_ua}", file=sys.stderr)

    # Strategy 1: Real Firefox engine (Gecko) — best anti-detection
    # Engine fingerprint, JS behavior, WebGL renderer all match real Firefox
    use_firefox = True
    firefox_prefs = {
        "dom.webdriver.enabled": False,
        "useAutomationExtension": False,
        "privacy.trackingprotection.enabled": False,
        "network.http.referer.spoofSource": True,
        # These prefs control Gecko's native webdriver/automation exposure
        "toolkit.telemetry.reportingpolicy.firstRun": False,
        "datareporting.policy.dataSubmissionEnabled": False,
    }
    # If running headed (debug mode), pre-seed xulstore so the window at least
    # opens near a screen corner instead of dead-center. Headless skips this.
    if not headless:
        _seed_offscreen_xulstore(AGENT_FIREFOX_PROFILE)

    # Suppress Firefox's "older version" downgrade dialog. Playwright bundles
    # Firefox Nightly which can lag the user's installed Firefox version;
    # Firefox checks installs.ini and warns globally on any older instance.
    # MOZ_ALLOW_DOWNGRADE is Firefox's official env-var escape hatch.
    os.environ["MOZ_ALLOW_DOWNGRADE"] = "1"

    # Randomized but realistic desktop viewport — slight variation per launch
    # reduces static-fingerprint risk without straying from common sizes
    vp_choices = [
        (1280, 800), (1280, 900), (1366, 768),
        (1440, 900), (1512, 945), (1680, 1050),
    ]
    vp_w, vp_h = random.choice(vp_choices)

    # Best-effort system timezone detection (falls back to LA)
    try:
        import datetime as _dt
        tz_id = _dt.datetime.now().astimezone().tzinfo.key  # type: ignore[attr-defined]
    except Exception:
        tz_id = None
    if not tz_id:
        tz_id = os.environ.get("TZ") or "America/Los_Angeles"

    context_opts = {
        "viewport": {"width": vp_w, "height": vp_h},
        "user_agent": firefox_ua,
        "locale": "en-US",
        "timezone_id": tz_id,
        "color_scheme": "light",
        "device_scale_factor": 2,  # Retina — matches typical Mac
    }
    print(f"Browser context: viewport={vp_w}x{vp_h} tz={tz_id}", file=sys.stderr)

    # launch_persistent_context combines browser + context and uses our
    # profile dir, which lets Firefox read the pre-seeded xulstore on startup
    # so the window opens off-screen with no visible slide.
    try:
        _context = pw.firefox.launch_persistent_context(
            user_data_dir=str(AGENT_FIREFOX_PROFILE),
            headless=headless,
            firefox_user_prefs=firefox_prefs,
            **context_opts,
        )
        # No standalone _browser handle with persistent context — close via _context.
        _browser = None
        print("Launched Playwright Firefox (Gecko, persistent context)", file=sys.stderr)
    except Exception as e:
        print(f"Firefox engine failed ({e}), falling back to Chromium", file=sys.stderr)
        use_firefox = False
        # Strategy 2: Chromium with stealth flags (fallback)
        launch_kwargs = {
            "headless": headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-features=AutomationControlled",
                "--no-first-run",
                "--disable-notifications",
                "--disable-popup-blocking",
                "--disable-infobars",
                "--disable-component-update",
                "--no-default-browser-check",
                "--disable-background-timer-throttling",
                "--disable-backgrounding-occluded-windows",
                "--disable-renderer-backgrounding",
                "--disable-ipc-flooding-protection",
                "--password-store=basic",
                "--use-mock-keychain",
                "--enable-features=NetworkService,NetworkServiceInProcess",
            ],
        }
        try:
            _browser = pw.chromium.launch(channel="chrome", **launch_kwargs)
            print("Launched real Chrome (channel=chrome) [fallback]", file=sys.stderr)
        except Exception:
            _browser = pw.chromium.launch(**launch_kwargs)
            print("Launched bundled Chromium [fallback]", file=sys.stderr)
        # Chromium fallback needs extra headers Firefox engine handles natively
        context_opts["extra_http_headers"] = {
            "Accept-Language": "en-US,en;q=0.5",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        _context = _browser.new_context(**context_opts)

    # Persistent context retains cookies across launches, but we always
    # want fresh auth state from the user's real Firefox profile — clear
    # first, then re-import so we never serve stale sessions.
    try:
        _context.clear_cookies()
    except Exception:
        pass

    # Import Firefox cookies
    profile = find_firefox_profile()
    if profile:
        cookies = extract_firefox_cookies(profile)
        if cookies:
            valid = [c for c in cookies if c.get("domain")]
            if valid:
                imported = 0
                failed = 0
                failed_names = []
                for c in valid:
                    try:
                        _context.add_cookies([c])
                        imported += 1
                    except Exception as e:
                        failed += 1
                        failed_names.append(f"{c.get('domain')}:{c.get('name')} ({e})")
                print(f"Imported {imported}/{len(valid)} cookies from Firefox ({failed} failed)", file=sys.stderr)
                if failed_names:
                    # Show first 10 failures for debugging
                    for fn in failed_names[:10]:
                        print(f"  FAILED: {fn}", file=sys.stderr)
                # Log key auth cookies for debugging
                auth_domains = {'.x.com', 'x.com', '.twitter.com', '.chatgpt.com', '.meta.ai', '.google.com'}
                auth_cookies = [c for c in valid if any(c.get('domain', '').endswith(d.lstrip('.')) for d in auth_domains)]
                if auth_cookies:
                    print(f"Auth-relevant cookies imported: {len(auth_cookies)}", file=sys.stderr)
                    for ac in auth_cookies:
                        print(f"  {ac['domain']:25s} {ac['name']:25s} sameSite={ac.get('sameSite','?'):6s} secure={ac.get('secure')}", file=sys.stderr)

    # Persistent context launches with one blank page; reuse it instead of
    # creating a second window. Chromium fallback path needs a fresh page.
    if use_firefox and _context.pages:
        _page = _context.pages[0]
    else:
        _page = _context.new_page()

    # Window visibility is handled by the headless flag at launch time —
    # Playwright Firefox headless still uses real Gecko (verified rendering
    # x.com auth flow), so we don't need post-launch osascript tricks. The
    # earlier off-screen / hide-app paths are gone: macOS WindowServer clamps
    # both negative and large positive coords, and hidden apps get throttled.

    # Apply stealth patches
    if not use_firefox:
        _apply_stealth(_page)
    else:
        # Firefox-specific stealth — Playwright's Gecko driver sets navigator.webdriver
        # at the C++ level AFTER init scripts run. We need aggressive interception.
        try:
            _page.add_init_script("""
                // ── Webdriver: triple-layer override ───────────────────────────
                // Layer 1: Direct property override
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined,
                    configurable: true,
                });
                // Layer 2: Delete from prototype chain
                try { delete navigator.__proto__.webdriver; } catch(e) {}
                // Layer 3: Proxy the entire navigator to intercept future sets
                // Playwright's Gecko driver re-sets webdriver AFTER init scripts,
                // so we use a MutationObserver on document to re-apply on DOMContentLoaded
                const _patchWebdriver = () => {
                    try {
                        Object.defineProperty(navigator, 'webdriver', {
                            get: () => undefined,
                            configurable: true,
                        });
                    } catch(e) {}
                };
                // Re-patch at multiple lifecycle points to beat Playwright's late set
                if (document.readyState === 'loading') {
                    document.addEventListener('DOMContentLoaded', _patchWebdriver);
                }
                document.addEventListener('readystatechange', _patchWebdriver);
                window.addEventListener('load', _patchWebdriver);
                // Also poll briefly in case Playwright sets it between events
                let _wdPollCount = 0;
                const _wdPoller = setInterval(() => {
                    if (navigator.webdriver !== undefined) _patchWebdriver();
                    if (++_wdPollCount > 50) clearInterval(_wdPoller);
                }, 20);

                // ── Marionette: hide driver signal ─────────────────────────────
                try { delete window.navigator.wrappedJSObject; } catch(e) {}

                // ── Hardware: ensure realistic values ──────────────────────────
                if (!navigator.hardwareConcurrency || navigator.hardwareConcurrency < 2) {
                    Object.defineProperty(navigator, 'hardwareConcurrency', { get: () => 8 });
                }
                Object.defineProperty(navigator, 'deviceMemory', {
                    get: () => 8,
                    configurable: true,
                });

                // ── Connection API (Firefox headless may not have it) ──────────
                if (!navigator.connection) {
                    Object.defineProperty(navigator, 'connection', {
                        get: () => ({ effectiveType: '4g', rtt: 50, downlink: 10, saveData: false }),
                    });
                }

                // ── Permissions (Firefox may expose 'denied' which flags bots) ─
                try {
                    const _origQuery = navigator.permissions.query.bind(navigator.permissions);
                    navigator.permissions.query = (params) => {
                        if (params.name === 'notifications') {
                            return Promise.resolve({ state: 'default', onchange: null });
                        }
                        return _origQuery(params);
                    };
                } catch(e) {}
            """)
        except Exception:
            pass

    engine = "Firefox/Gecko" if use_firefox else "Chromium"
    print(f"Agent browser launched (engine={engine}, headless={headless})", file=sys.stderr)


def screenshot() -> bytes:
    """Take a screenshot of the current page."""
    with _lock:
        if not _page:
            return b""
        data = _page.screenshot(type="png")
        # Also save to disk for the agent to read
        with open(SS_PATH, "wb") as f:
            f.write(data)
        return data


def navigate(url: str) -> dict:
    """Navigate to a URL with human-like post-load behavior.
    Generates a few mouse movements and a small scroll after page load
    to look like a real user orienting on the page. Bot detectors flag
    sessions with zero mouse events between navigation and interaction."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            _page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # Simulate human page-load behavior: eyes scan → mouse drifts
            try:
                vp = _page.viewport_size or {"width": 1280, "height": 900}
                w, h = vp["width"], vp["height"]
                # 2-3 lazy mouse movements (reading the page)
                for _ in range(random.randint(2, 3)):
                    mx = random.randint(int(w * 0.2), int(w * 0.8))
                    my = random.randint(int(h * 0.15), int(h * 0.5))
                    _page.mouse.move(mx, my, steps=random.randint(3, 8))
                    time.sleep(random.uniform(0.2, 0.6))
                # Small scroll (reading below fold)
                if random.random() < 0.4:
                    _page.mouse.wheel(0, random.randint(80, 250))
                    time.sleep(random.uniform(0.3, 0.7))
            except Exception:
                pass  # Non-fatal — page is loaded regardless
            return {"ok": True, "url": _page.url, "title": _page.title()}
        except Exception as e:
            return {"error": str(e)}


def click(x: int, y: int) -> dict:
    """Click at coordinates with human-like cursor movement.
    Moves the mouse in small steps to the target before clicking,
    avoiding the teleporting-cursor signal that bot detectors catch."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            # Move to target with intermediate steps (Bezier-ish path)
            # Bot detectors flag instant teleport clicks
            _page.mouse.move(x, y, steps=random.randint(5, 15))
            # Brief settle time before click (humans don't click instantly on arrival)
            time.sleep(random.uniform(0.04, 0.12))
            _page.mouse.click(x, y)
            return {"ok": True, "x": x, "y": y}
        except Exception as e:
            return {"error": str(e)}


def type_text(text: str, delay: int = 50) -> dict:
    """Type text into whatever has focus."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            _page.keyboard.type(text, delay=delay)
            return {"ok": True, "length": len(text)}
        except Exception as e:
            return {"error": str(e)}


def press_key(key: str) -> dict:
    """Press a key or key combo (e.g. 'Enter', 'Meta+a', 'Meta+Enter')."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            _page.keyboard.press(key)
            return {"ok": True, "key": key}
        except Exception as e:
            return {"error": str(e)}


def scroll(x: int, y: int, delta_x: int = 0, delta_y: int = -300) -> dict:
    """Scroll at a position."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            _page.mouse.wheel(delta_x, delta_y)
            return {"ok": True}
        except Exception as e:
            return {"error": str(e)}


def get_elements() -> list:
    """Get all visible interactive elements with bounding boxes and text."""
    with _lock:
        if not _page:
            return []
        try:
            return _page.evaluate("""() => {
                const results = [];
                const interactiveSelectors = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"], [role="menuitem"], [role="tab"], [contenteditable="true"], [onclick], [tabindex]';
                const els = document.querySelectorAll(interactiveSelectors);
                let idx = 0;
                for (const el of els) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 2 || rect.height < 2) continue;
                    if (rect.top > window.innerHeight || rect.bottom < 0) continue;
                    if (rect.left > window.innerWidth || rect.right < 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                    const tag = el.tagName.toLowerCase();
                    const type = el.getAttribute('type') || '';
                    const text = (el.innerText || el.value || el.getAttribute('placeholder') || el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 80);
                    const name = el.getAttribute('name') || '';
                    const role = el.getAttribute('role') || '';
                    results.push({
                        idx: idx++,
                        tag: tag,
                        type: type,
                        role: role,
                        text: text,
                        name: name,
                        x: Math.round(rect.x + rect.width / 2),
                        y: Math.round(rect.y + rect.height / 2),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                    });
                    if (idx >= 80) break;
                }
                return results;
            }""")
        except Exception as e:
            return [{"error": str(e)}]


def click_element(selector: str = "", text: str = "", index: int = -1) -> dict:
    """Click an element by CSS selector, visible text, or element index from /elements."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            if index >= 0:
                # Click by index from get_elements()
                els = _page.evaluate("""(idx) => {
                    const interactiveSelectors = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"], [role="menuitem"], [role="tab"], [contenteditable="true"], [onclick], [tabindex]';
                    const all = document.querySelectorAll(interactiveSelectors);
                    let visible = [];
                    for (const el of all) {
                        const rect = el.getBoundingClientRect();
                        if (rect.width < 2 || rect.height < 2) continue;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden') continue;
                        visible.push(el);
                    }
                    if (idx < visible.length) {
                        const el = visible[idx];
                        const rect = el.getBoundingClientRect();
                        return {x: rect.x + rect.width/2, y: rect.y + rect.height/2};
                    }
                    return null;
                }""", index)
                if els:
                    _page.mouse.click(els["x"], els["y"])
                    return {"ok": True, "method": "index", "index": index}
                return {"error": f"element index {index} not found"}
            elif text:
                # Click by visible text content
                loc = _page.get_by_text(text, exact=False).first
                loc.click(timeout=5000)
                return {"ok": True, "method": "text", "text": text}
            elif selector:
                _page.click(selector, timeout=5000)
                return {"ok": True, "method": "selector", "selector": selector}
            return {"error": "provide selector, text, or index"}
        except Exception as e:
            return {"error": str(e)}


def fill_element(selector: str = "", text: str = "", value: str = "") -> dict:
    """Fill an input field by selector or placeholder/label text."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            if selector:
                _page.fill(selector, value, timeout=5000)
                return {"ok": True, "method": "selector", "selector": selector}
            elif text:
                loc = _page.get_by_placeholder(text).or_(_page.get_by_label(text)).first
                loc.fill(value, timeout=5000)
                return {"ok": True, "method": "text", "text": text}
            return {"error": "provide selector or text (placeholder/label)"}
        except Exception as e:
            return {"error": str(e)}


def screenshot_annotated() -> bytes:
    """Take a screenshot with numbered labels on interactive elements."""
    with _lock:
        if not _page:
            return b""
        try:
            # Inject annotation overlay
            _page.evaluate("""() => {
                // Remove old annotations
                document.querySelectorAll('.pliny-annotation').forEach(el => el.remove());
                const interactiveSelectors = 'a, button, input, textarea, select, [role="button"], [role="link"], [role="textbox"], [role="menuitem"], [role="tab"], [contenteditable="true"], [onclick], [tabindex]';
                const els = document.querySelectorAll(interactiveSelectors);
                let idx = 0;
                for (const el of els) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 2 || rect.height < 2) continue;
                    if (rect.top > window.innerHeight || rect.bottom < 0) continue;
                    if (rect.left > window.innerWidth || rect.right < 0) continue;
                    const style = window.getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                    const label = document.createElement('div');
                    label.className = 'pliny-annotation';
                    label.textContent = idx;
                    label.style.cssText = 'position:fixed;z-index:999999;background:#ff0040;color:white;font-size:11px;font-weight:bold;padding:1px 4px;border-radius:8px;pointer-events:none;line-height:16px;min-width:16px;text-align:center;font-family:monospace;';
                    label.style.left = Math.max(0, rect.left - 2) + 'px';
                    label.style.top = Math.max(0, rect.top - 2) + 'px';
                    document.body.appendChild(label);
                    idx++;
                    if (idx >= 80) break;
                }
            }""")
            # Take screenshot with annotations
            data = _page.screenshot(type="png")
            # Remove annotations
            _page.evaluate("() => document.querySelectorAll('.pliny-annotation').forEach(el => el.remove())")
            return data
        except Exception as e:
            # Fallback to regular screenshot
            return _page.screenshot(type="png")


def wait_for_cloudflare(timeout: int = 15) -> dict:
    """Detect and attempt to handle Cloudflare challenge pages.
    Returns status of the attempt."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            # Check if we're on a Cloudflare challenge page
            content = _page.content()
            is_cf = any(marker in content for marker in [
                "cf-turnstile", "challenge-platform", "Just a moment",
                "cf-challenge", "Checking your browser", "Verify you are human",
                "cdn-cgi/challenge-platform",
            ])
            if not is_cf:
                return {"ok": True, "cloudflare": False, "message": "No Cloudflare challenge detected"}

            # Strategy 1: Wait — sometimes Cloudflare auto-passes after JS execution
            print("[CF] Cloudflare detected, waiting for auto-pass...", file=sys.stderr)
            try:
                _page.wait_for_function(
                    "() => !document.querySelector('iframe[src*=\"challenge\"]') && !document.title.includes('Just a moment')",
                    timeout=timeout * 1000
                )
                return {"ok": True, "cloudflare": True, "method": "auto-pass", "message": "Cloudflare auto-resolved"}
            except Exception:
                pass

            # Strategy 2: Try to find and click the turnstile checkbox
            # The turnstile is in an iframe — try to access it
            print("[CF] Auto-pass failed, trying iframe click...", file=sys.stderr)
            frames = _page.frames
            for frame in frames:
                try:
                    if "challenge" in (frame.url or ""):
                        # Try clicking the checkbox area within the iframe
                        checkbox = frame.query_selector('input[type="checkbox"], .cb-i, #cf-turnstile-response, label')
                        if checkbox:
                            checkbox.click()
                            time.sleep(3)
                            # Check if it resolved
                            if "Just a moment" not in _page.title():
                                return {"ok": True, "cloudflare": True, "method": "iframe-click", "message": "Clicked turnstile checkbox"}
                except Exception:
                    continue

            # Strategy 3: Click in the general area where the checkbox usually is
            print("[CF] Trying coordinate click on turnstile area...", file=sys.stderr)
            # Turnstile is usually centered, ~300px from top
            for coords in [(310, 310), (340, 320), (280, 300), (320, 340)]:
                try:
                    _page.mouse.click(coords[0], coords[1])
                    time.sleep(2)
                    if "Just a moment" not in _page.title():
                        return {"ok": True, "cloudflare": True, "method": "coordinate-click", "message": "Resolved via coordinate click"}
                except Exception:
                    pass

            # Strategy 4: Reload and retry — sometimes a fresh load passes
            print("[CF] Trying reload...", file=sys.stderr)
            current_url = _page.url
            _page.reload(wait_until="domcontentloaded")
            time.sleep(5)
            if "Just a moment" not in _page.title():
                return {"ok": True, "cloudflare": True, "method": "reload", "message": "Passed after reload"}

            return {
                "ok": False,
                "cloudflare": True,
                "message": "Cloudflare challenge could not be resolved automatically. Try: (1) a different URL, (2) waiting and retrying, or (3) the site may block headless browsers entirely."
            }
        except Exception as e:
            return {"error": str(e)}


def upload_files(file_paths: list) -> dict:
    """Attach files via the hidden file input (useful for X image uploads).
    Uses Playwright's file chooser interception."""
    with _lock:
        if not _page:
            return {"error": "no page"}
        try:
            # Validate paths exist
            valid = [p for p in file_paths if os.path.exists(p)]
            if not valid:
                return {"error": "no valid file paths", "paths": file_paths}

            # X uses a hidden <input type="file"> — trigger it via the media button
            # Strategy 1: Use file chooser interception
            with _page.expect_file_chooser(timeout=5000) as fc_info:
                # Click the media (image) button — it's the first toolbar icon in compose
                media_btn = _page.query_selector('[data-testid="fileInput"]')
                if media_btn:
                    media_btn.set_input_files(valid)
                    return {"ok": True, "method": "direct_input", "files": len(valid)}
                # Fallback: click the photo icon button to trigger file chooser
                icon_btn = _page.query_selector('[aria-label="Add photos or video"], [data-testid="tweetMediaButton"]')
                if icon_btn:
                    icon_btn.click()
                else:
                    return {"error": "could not find media button"}
            file_chooser = fc_info.value
            file_chooser.set_files(valid)
            return {"ok": True, "method": "file_chooser", "files": len(valid)}
        except Exception as e:
            # Strategy 2: Direct input file set (works even when hidden)
            try:
                inp = _page.query_selector('input[type="file"][accept*="image"]')
                if inp:
                    inp.set_input_files(valid)
                    return {"ok": True, "method": "input_selector", "files": len(valid)}
            except Exception:
                pass
            return {"error": str(e)}


def _human_type(text: str):
    """Type text one character at a time with realistic human cadence.
    Includes per-keystroke jitter, word-boundary pauses, occasional thinking
    pauses, and rare typo-then-backspace sequences.
    Call only while holding _lock."""
    # Adjacent key map for realistic typo simulation
    _adjacent = {
        'a': 'sq', 'b': 'vn', 'c': 'xv', 'd': 'sf', 'e': 'wr', 'f': 'dg',
        'g': 'fh', 'h': 'gj', 'i': 'uo', 'j': 'hk', 'k': 'jl', 'l': 'k',
        'm': 'n', 'n': 'bm', 'o': 'ip', 'p': 'o', 'q': 'w', 'r': 'et',
        's': 'ad', 't': 'ry', 'u': 'yi', 'v': 'cb', 'w': 'qe', 'x': 'zc',
        'y': 'tu', 'z': 'x',
    }
    i = 0
    while i < len(text):
        ch = text[i]
        # ~2% chance of typo on letter chars (type adjacent key, pause, backspace, retype)
        if ch.isalpha() and random.random() < 0.02:
            adj = _adjacent.get(ch.lower(), "")
            if adj:
                typo = random.choice(adj)
                if ch.isupper():
                    typo = typo.upper()
                _page.keyboard.type(typo, delay=0)
                time.sleep(random.uniform(0.08, 0.18))
                # Realize mistake
                time.sleep(random.uniform(0.15, 0.45))
                _page.keyboard.press("Backspace")
                time.sleep(random.uniform(0.06, 0.15))

        _page.keyboard.type(ch, delay=0)
        # Per-keystroke delay with human-like distribution
        if random.random() < 0.025:
            time.sleep(random.uniform(0.3, 0.8))  # "thinking" pause
        elif ch == " " and random.random() < 0.15:
            time.sleep(random.uniform(0.12, 0.3))  # word boundary
        elif ch in ".,!?;:":
            time.sleep(random.uniform(0.08, 0.25))  # punctuation pause
        else:
            time.sleep(random.uniform(0.035, 0.12))
        i += 1


def _attach_images_via_filechooser(image_paths: list) -> int:
    """Strategy 1: Use Playwright's filechooser event to attach images.
    X's compose page has a media button that triggers a file picker.
    We intercept the filechooser event instead of hunting for hidden inputs.
    Must be called while holding _lock."""
    attached = 0
    try:
        # Click the media (photo) button to trigger file chooser
        # X uses an SVG icon inside a button — match by aria-label or data-testid
        media_btn = _page.query_selector(
            '[data-testid="fileInput"],'
            '[aria-label="Add photos or video"],'
            '[aria-label="Media"],'
            'input[accept*="image"]'
        )
        if media_btn and media_btn.evaluate("el => el.tagName") == "INPUT":
            # Direct file input found — use set_input_files
            media_btn.set_input_files(image_paths)
            attached = len(image_paths)
            print(f"[TWEET] Attached {attached} images via direct input", file=sys.stderr)
        elif media_btn:
            # It's a button — use filechooser event
            with _page.expect_file_chooser(timeout=5000) as fc_info:
                media_btn.click()
            file_chooser = fc_info.value
            file_chooser.set_files(image_paths)
            attached = len(image_paths)
            print(f"[TWEET] Attached {attached} images via filechooser", file=sys.stderr)
        else:
            print("[TWEET] No media button found for filechooser strategy", file=sys.stderr)
    except Exception as e:
        print(f"[TWEET] Filechooser strategy failed: {e}", file=sys.stderr)
    return attached


def _attach_images_via_drop(image_paths: list) -> int:
    """Strategy 2: Drag-and-drop images onto the compose area via JS.
    Creates synthetic File objects from base64 data and dispatches
    drop events. Works even when X changes their file input DOM."""
    attached = 0
    try:
        for img_path in image_paths:
            with open(img_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode("utf-8")
            fname = os.path.basename(img_path)
            mime = "image/png" if fname.endswith(".png") else "image/jpeg"

            result = _page.evaluate("""([b64, filename, mimeType]) => {
                // Find the compose area — the drop target
                const targets = [
                    document.querySelector('[data-testid="tweetTextarea_0"]'),
                    document.querySelector('[role="textbox"][contenteditable="true"]'),
                    document.querySelector('[data-testid="primaryColumn"]'),
                    document.querySelector('.DraftEditor-root'),
                ];
                const dropTarget = targets.find(t => t !== null);
                if (!dropTarget) return {ok: false, error: 'no drop target'};

                // Decode base64 to binary
                const binary = atob(b64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                const file = new File([bytes], filename, {type: mimeType});

                // Build DataTransfer
                const dt = new DataTransfer();
                dt.items.add(file);

                // Dispatch full drag-and-drop sequence
                const opts = {bubbles: true, cancelable: true, dataTransfer: dt};
                dropTarget.dispatchEvent(new DragEvent('dragenter', opts));
                dropTarget.dispatchEvent(new DragEvent('dragover', opts));
                dropTarget.dispatchEvent(new DragEvent('drop', opts));
                return {ok: true};
            }""", [img_data, fname, mime])

            if result and result.get("ok"):
                attached += 1
                print(f"[TWEET] Attached {fname} via drag-and-drop", file=sys.stderr)
                time.sleep(random.uniform(1.0, 2.0))
            else:
                print(f"[TWEET] Drop failed for {fname}: {result}", file=sys.stderr)
    except Exception as e:
        print(f"[TWEET] Drop strategy failed: {e}", file=sys.stderr)
    return attached


def _attach_images_via_clipboard(image_paths: list) -> int:
    """Strategy 3: Paste images from clipboard via JS ClipboardEvent.
    Simulates Cmd+V with an image on the clipboard. Works on most
    contenteditable areas including X's compose box."""
    attached = 0
    try:
        for img_path in image_paths:
            with open(img_path, "rb") as f:
                img_data = base64.b64encode(f.read()).decode("utf-8")
            fname = os.path.basename(img_path)
            mime = "image/png" if fname.endswith(".png") else "image/jpeg"

            result = _page.evaluate("""([b64, filename, mimeType]) => {
                const targets = [
                    document.querySelector('[data-testid="tweetTextarea_0"]'),
                    document.querySelector('[role="textbox"][contenteditable="true"]'),
                    document.querySelector('.DraftEditor-root'),
                ];
                const target = targets.find(t => t !== null);
                if (!target) return {ok: false, error: 'no paste target'};

                // Focus the target
                target.focus();

                // Build File + DataTransfer for clipboard
                const binary = atob(b64);
                const bytes = new Uint8Array(binary.length);
                for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
                const file = new File([bytes], filename, {type: mimeType});
                const dt = new DataTransfer();
                dt.items.add(file);

                // Dispatch paste event with the image
                const pasteEvent = new ClipboardEvent('paste', {
                    bubbles: true,
                    cancelable: true,
                    clipboardData: dt,
                });
                target.dispatchEvent(pasteEvent);
                return {ok: true};
            }""", [img_data, fname, mime])

            if result and result.get("ok"):
                attached += 1
                print(f"[TWEET] Attached {fname} via clipboard paste", file=sys.stderr)
                time.sleep(random.uniform(1.0, 2.0))
            else:
                print(f"[TWEET] Paste failed for {fname}: {result}", file=sys.stderr)
    except Exception as e:
        print(f"[TWEET] Clipboard strategy failed: {e}", file=sys.stderr)
    return attached


def _dismiss_x_modals():
    """Dismiss known X interstitial modals (e.g. 'You've unlocked more on X').
    These overlays steal focus and break Cmd+Enter / reply-button clicks."""
    if not _page:
        return
    selectors = [
        'div[role="dialog"] button:has-text("Got it")',
        'div[role="dialog"] button:has-text("Not now")',
        'div[role="dialog"] button:has-text("Skip for now")',
        'div[role="dialog"] button:has-text("Maybe later")',
        '[data-testid="app-bar-close"]',
        '[aria-label="Close"]',
    ]
    for sel in selectors:
        try:
            btn = _page.query_selector(sel)
            if btn and btn.is_visible():
                btn.click()
                print(f"[TWEET] Dismissed modal via {sel}", file=sys.stderr)
                time.sleep(random.uniform(0.6, 1.2))
        except Exception:
            continue
    # Final fallback: ESC twice
    try:
        _page.keyboard.press("Escape")
        time.sleep(0.2)
        _page.keyboard.press("Escape")
    except Exception:
        pass


def post_tweet(text: str, image_paths: list = None) -> dict:
    """Full tweet flow: navigate to compose, attach images, type text, submit.
    All within the Playwright browser — no window switching.
    Uses human-like delays throughout to reduce automation fingerprint.

    Image attachment uses a cascade of strategies:
    1. Playwright filechooser event (intercept native file picker)
    2. Synthetic drag-and-drop (JS DataTransfer)
    3. Synthetic clipboard paste (JS ClipboardEvent)
    """
    with _lock:
        if not _page:
            return {"error": "no page"}

    # Step 1: Navigate to compose
    result = navigate("https://x.com/compose/post")
    if "error" in result:
        return {"error": f"navigate failed: {result['error']}"}
    # Humans take 2-5s to orient on a new page
    time.sleep(random.uniform(2.5, 5.0))
    # Dismiss any onboarding/upsell modal that landed on top of compose
    with _lock:
        _dismiss_x_modals()

    # Step 2: Attach images — try multiple strategies
    images_attached = 0
    attach_method = "none"
    if image_paths:
        valid_images = [p for p in image_paths if os.path.exists(p) and os.path.getsize(p) > 500]
        if valid_images:
            # Strategy 1: Filechooser
            with _lock:
                images_attached = _attach_images_via_filechooser(valid_images)
            if images_attached:
                attach_method = "filechooser"
            else:
                # Strategy 2: Drag-and-drop
                with _lock:
                    images_attached = _attach_images_via_drop(valid_images)
                if images_attached:
                    attach_method = "drop"
                else:
                    # Strategy 3: Clipboard paste
                    with _lock:
                        images_attached = _attach_images_via_clipboard(valid_images)
                    if images_attached:
                        attach_method = "clipboard"

            if images_attached:
                print(f"[TWEET] {images_attached} images attached via {attach_method}", file=sys.stderr)
                # Wait for upload processing + preview to render
                time.sleep(random.uniform(3.0, 5.0))
                # Verify images appeared — check for thumbnail previews
                with _lock:
                    try:
                        previews = _page.query_selector_all(
                            '[data-testid="attachments"] img,'
                            '[data-testid="mediaPreview"],'
                            'img[src*="blob:"],'
                            '[aria-label*="media"]'
                        )
                        if previews:
                            print(f"[TWEET] Verified {len(previews)} image preview(s) visible", file=sys.stderr)
                        else:
                            print("[TWEET] WARNING: No image previews detected after attach", file=sys.stderr)
                    except Exception:
                        pass
            else:
                print(f"[TWEET] WARNING: All image strategies failed for {len(valid_images)} images", file=sys.stderr)

    # Brief pause before moving to compose box (eye travel time)
    time.sleep(random.uniform(0.4, 1.2))

    # Step 3: Type tweet text into compose box with human cadence
    with _lock:
        try:
            compose = _page.query_selector(
                '[data-testid="tweetTextarea_0"],'
                '[role="textbox"][contenteditable="true"]'
            )
            if compose:
                compose.click()
                time.sleep(random.uniform(0.3, 0.7))
            _human_type(text)
            # Post-type reading pause (review before submit)
            time.sleep(random.uniform(1.0, 2.5))
        except Exception as e:
            return {"error": f"type failed: {e}", "images_attached": images_attached}

    # Step 4: Submit with Cmd+Enter (X's native shortcut)
    with _lock:
        try:
            _page.keyboard.press("Meta+Enter")
            time.sleep(random.uniform(2.5, 4.0))
        except Exception as e:
            return {"error": f"submit failed: {e}", "images_attached": images_attached}

    # Step 5: Take a confirmation screenshot
    screenshot()

    return {
        "ok": True,
        "text": text[:100],
        "images_attached": images_attached,
        "attach_method": attach_method,
        "method": "playwright",
    }


def post_thread(tweets: list[str], first_images: list = None) -> dict:
    """Post a thread (list of tweets) as a reply chain on X.

    tweets: list of tweet texts (each <=280 chars)
    first_images: optional image paths to attach to the FIRST tweet only

    Flow:
      1. Post the first tweet (with optional images) via compose
      2. For each subsequent tweet, click the reply button on the just-posted tweet
         and post the next text as a reply
    """
    if not tweets:
        return {"error": "no tweets provided"}

    # Post the first tweet normally
    result = post_tweet(tweets[0], first_images)
    if not result.get("ok"):
        return {"error": f"first tweet failed: {result.get('error', '?')}", "posted": 0}

    posted = 1
    if len(tweets) == 1:
        return {"ok": True, "posted": posted, "method": "playwright"}

    # For replies, we need to find the tweet we just posted and reply to it.
    # After posting, X usually navigates to the tweet or we're still on compose.
    # Most reliable: navigate to our profile, find the latest tweet, reply to it.
    time.sleep(random.uniform(2.0, 3.5))

    for i, reply_text in enumerate(tweets[1:], start=2):
        try:
            with _lock:
                if not _page:
                    break

                # X often shows an upsell modal right after a successful post.
                # Dismiss it before hunting for the reply button.
                _dismiss_x_modals()

                # Click the reply icon on the most recent tweet visible
                # After posting, X shows the tweet — find the reply button
                reply_btn = _page.query_selector(
                    '[data-testid="reply"],'
                    '[aria-label*="Reply"],'
                    '[aria-label*="reply"]'
                )
                if not reply_btn:
                    # Try navigating to profile to find the tweet
                    # Get current user handle from page
                    _page.keyboard.press("Meta+Enter")  # Fallback: new tweet
                    time.sleep(random.uniform(1.5, 2.5))
                    # If we can't find reply, post as standalone (better than nothing)
                    compose = _page.query_selector(
                        '[data-testid="tweetTextarea_0"],'
                        '[role="textbox"][contenteditable="true"]'
                    )
                    if compose:
                        compose.click()
                        time.sleep(random.uniform(0.3, 0.6))
                        _human_type(reply_text)
                        time.sleep(random.uniform(0.8, 1.5))
                        _page.keyboard.press("Meta+Enter")
                        time.sleep(random.uniform(2.5, 4.0))
                        posted += 1
                    continue

                reply_btn.click()
                time.sleep(random.uniform(1.5, 3.0))

                # Type the reply text
                _human_type(reply_text)
                time.sleep(random.uniform(0.8, 2.0))

                # Submit reply
                # Look for the reply submit button
                reply_submit = _page.query_selector(
                    '[data-testid="tweetButtonInline"],'
                    '[data-testid="tweetButton"]'
                )
                if reply_submit:
                    reply_submit.click()
                else:
                    _page.keyboard.press("Meta+Enter")

                time.sleep(random.uniform(2.5, 4.0))
                posted += 1
                print(f"[THREAD] Posted tweet {i}/{len(tweets)}", file=sys.stderr)

        except Exception as e:
            print(f"[THREAD] Reply {i} failed: {e}", file=sys.stderr)
            break

    screenshot()
    return {"ok": True, "posted": posted, "total": len(tweets), "method": "playwright"}


def refresh_cookies() -> dict:
    """Re-extract cookies from Firefox and import new/updated ones.
    Call this when a session auth cookie expires during a long run."""
    with _lock:
        if not _context:
            return {"error": "no browser context"}
        profile = find_firefox_profile()
        if not profile:
            return {"error": "no Firefox profile found"}
        cookies = extract_firefox_cookies(profile)
        if not cookies:
            return {"error": "no cookies extracted"}
        valid = [c for c in cookies if c.get("domain")]
        if not valid:
            return {"error": "no valid cookies"}
        imported = 0
        failed = 0
        for c in valid:
            try:
                _context.add_cookies([c])
                imported += 1
            except Exception:
                failed += 1
        return {"ok": True, "imported": imported, "failed": failed, "total": len(valid)}


def cookie_health() -> dict:
    """Check if key auth cookies exist and are still valid.
    Returns login status for major sites the agent might visit."""
    AUTH_COOKIE_MAP = {
        "x.com": {
            "domains": [".x.com", "x.com", ".twitter.com"],
            "key_cookies": ["auth_token", "ct0"],
        },
        "chatgpt.com": {
            "domains": [".chatgpt.com", "chatgpt.com", ".auth.openai.com", "auth.openai.com"],
            "key_cookies": ["oai-client-auth-session", "__Secure-next-auth.session-token"],
            "any_of": True,  # Need at least ONE of these specific auth cookies
        },
        "claude.ai": {
            "domains": [".claude.ai", "claude.ai"],
            "key_cookies": ["sessionKey", "sessionKeyLC"],
        },
        "meta.ai": {
            "domains": [".meta.ai", ".auth.meta.com"],
            "key_cookies": ["datr", "ps_l"],
        },
        "github.com": {
            "domains": [".github.com", "github.com"],
            "key_cookies": ["user_session", "_gh_sess"],
        },
    }
    now = time.time()
    with _lock:
        if not _context:
            return {"error": "no browser context"}
        all_cookies = _context.cookies()

    results = {}
    for site, spec in AUTH_COOKIE_MAP.items():
        site_cookies = [
            c for c in all_cookies
            if any(c.get("domain", "").endswith(d.lstrip(".")) for d in spec["domains"])
        ]
        found_keys = []
        expired_keys = []
        for kc in spec["key_cookies"]:
            # Use startswith to catch split cookies (e.g. __Secure-next-auth.session-token.0/.1)
            match = [c for c in site_cookies if c.get("name", "") == kc or c.get("name", "").startswith(kc + ".")]
            if match:
                c = match[0]
                exp = c.get("expires", -1)
                if exp > 0 and exp < now:
                    expired_keys.append(kc)
                else:
                    found_keys.append(kc)
        if found_keys:
            status = "logged_in"
        elif expired_keys:
            status = "expired"
        else:
            status = "not_logged_in"
        results[site] = {
            "status": status,
            "found": found_keys,
            "expired": expired_keys,
            "total_cookies": len(site_cookies),
        }
    return results


def get_status() -> dict:
    """Get browser status.

    MUST NOT call into Playwright (no _page.title(), no _page.goto, etc.) —
    Playwright's sync API serializes all calls onto one event loop, so any
    Playwright call here would block while a navigate/click/etc. is in flight.
    The watchdog polls this endpoint to detect liveness; if /status blocks
    during normal long requests, the watchdog mis-fires and restarts a
    healthy process.

    Both `running` (object identity) and `url` (cached property, no round-
    trip) are safe to read. `title` requires a browser round-trip and is
    intentionally omitted — read it via a dedicated endpoint if needed.
    """
    return {
        "running": _page is not None,
        "url": _page.url if _page else None,
        "port": PORT,
    }


# ─── HTTP API ────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress access logs

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        return self.rfile.read(length).decode() if length else ""

    def _send_json(self, data, code=200):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_image(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/screenshot":
            data = screenshot()
            if data:
                self._send_image(data)
            else:
                self._send_json({"error": "no page"}, 500)
        elif self.path == "/screenshot/annotated":
            data = screenshot_annotated()
            if data:
                self._send_image(data)
            else:
                self._send_json({"error": "no page"}, 500)
        elif self.path == "/screenshot/base64":
            data = screenshot()
            if data:
                self._send_json({"data": base64.b64encode(data).decode()})
            else:
                self._send_json({"error": "no page"}, 500)
        elif self.path == "/elements":
            self._send_json(get_elements())
        elif self.path == "/cloudflare":
            self._send_json(wait_for_cloudflare())
        elif self.path == "/refresh_cookies":
            self._send_json(refresh_cookies())
        elif self.path == "/cookie_health":
            self._send_json(cookie_health())
        elif self.path == "/cookies":
            # Debug: show cookies the browser context currently has
            with _lock:
                if _context:
                    all_cookies = _context.cookies()
                    # Summarize — don't leak full values
                    summary = []
                    for c in all_cookies:
                        summary.append({
                            "domain": c.get("domain"),
                            "name": c.get("name"),
                            "secure": c.get("secure"),
                            "httpOnly": c.get("httpOnly"),
                            "sameSite": c.get("sameSite"),
                            "hasValue": bool(c.get("value")),
                            "expires": c.get("expires"),
                        })
                    self._send_json({"count": len(summary), "cookies": summary})
                else:
                    self._send_json({"error": "no context"})
        elif self.path == "/status":
            self._send_json(get_status())
        elif self.path == "/pause_status":
            self._send_json({"paused": _paused})
        else:
            self._send_json({"error": "not found"}, 404)

    def do_POST(self):
        global _paused
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length).decode() if length else "{}"
        try:
            body = json.loads(raw)
        except json.JSONDecodeError:
            body = {}

        # Pause/resume control endpoints — always allowed regardless of pause state.
        if self.path == "/pause":
            _paused = True
            bring_firefox_to_front()
            self._send_json({"ok": True, "paused": True})
            return
        if self.path == "/resume":
            _paused = False
            self._send_json({"ok": True, "paused": False})
            return

        # Gate: when paused, refuse user-style actions so the agent doesn't fight the human.
        if _paused and self.path in PAUSE_GATED_PATHS:
            self._send_json({
                "error": "browser is paused — user has control",
                "paused": True,
            }, 423)
            return

        if self.path == "/navigate":
            url = body.get("url", "")
            if not url:
                self._send_json({"error": "url required"}, 400)
                return
            self._send_json(navigate(url))
        elif self.path == "/click":
            x, y = body.get("x", 0), body.get("y", 0)
            self._send_json(click(x, y))
        elif self.path == "/type":
            text = body.get("text", "")
            delay = int(body.get("delay", 50))
            self._send_json(type_text(text, delay=delay))
        elif self.path == "/text":
            # Return innerText from the page (or a selector).
            # Use to read model responses without screenshot parsing.
            sel = body.get("selector", "")
            try:
                with _lock:
                    if not _page:
                        self._send_json({"error": "no page"}, 500)
                    elif sel:
                        txt = _page.evaluate(
                            "(s) => { const els = document.querySelectorAll(s); return Array.from(els).map(e => e.innerText).join('\\n---\\n'); }",
                            sel,
                        )
                        self._send_json({"ok": True, "text": txt, "count": txt.count("\n---\n") + 1 if txt else 0})
                    else:
                        txt = _page.evaluate("() => document.body.innerText")
                        self._send_json({"ok": True, "text": txt})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        elif self.path == "/paste":
            # Atomic insert via keyboard.insert_text — bypasses per-char delay.
            # Targets whatever has focus. For ProseMirror (claude.ai),
            # click the contenteditable first to focus.
            text = body.get("text", "")
            try:
                with _lock:
                    if not _page:
                        self._send_json({"error": "no page"}, 500)
                    else:
                        _page.keyboard.insert_text(text)
                        self._send_json({"ok": True, "length": len(text)})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        elif self.path == "/press":
            key = body.get("key", "Enter")
            self._send_json(press_key(key))
        elif self.path == "/scroll":
            x, y = body.get("x", 640), body.get("y", 450)
            dx, dy = body.get("delta_x", 0), body.get("delta_y", body.get("amount", -300))
            self._send_json(scroll(x, y, dx, dy))
        elif self.path == "/screenshot":
            data = screenshot()
            self._send_json({"ok": True, "saved": SS_PATH})
        elif self.path == "/click_element":
            sel = body.get("selector", "")
            txt = body.get("text", "")
            idx = body.get("index", -1)
            self._send_json(click_element(selector=sel, text=txt, index=idx))
        elif self.path == "/fill":
            sel = body.get("selector", "")
            txt = body.get("text", "")
            val = body.get("value", "")
            self._send_json(fill_element(selector=sel, text=txt, value=val))
        elif self.path == "/upload_files":
            paths = body.get("paths", body.get("files", []))
            if isinstance(paths, str):
                paths = [paths]
            self._send_json(upload_files(paths))
        elif self.path == "/tweet":
            text = body.get("text", "")
            images = body.get("images", body.get("image_paths", []))
            if isinstance(images, str):
                images = [images]
            if not text:
                self._send_json({"error": "text required"}, 400)
            else:
                self._send_json(post_tweet(text, images))
        elif self.path == "/thread":
            tweets = body.get("tweets", [])
            images = body.get("images", body.get("image_paths", []))
            if isinstance(images, str):
                images = [images]
            if not tweets or not isinstance(tweets, list):
                self._send_json({"error": "tweets array required"}, 400)
            elif any(len(t) > 280 for t in tweets):
                over = [(i, len(t)) for i, t in enumerate(tweets) if len(t) > 280]
                self._send_json({"error": f"tweets too long: {over}"}, 400)
            else:
                self._send_json(post_thread(tweets, images))
        else:
            self._send_json({"error": "not found"}, 404)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Pliny Agent Browser (Playwright)")
    parser.add_argument("--port", type=int, default=PORT)
    # Default: headless. Playwright Firefox in headless mode is still real
    # Gecko (verified working on x.com with full auth) — and there is no clean
    # way on macOS to keep a headed window truly off-screen across multi-display
    # setups (WindowServer clamps both negative and large positive coords to
    # keep a sliver on-screen). Hidden/minimized apps get rendering-pipeline
    # throttled so Playwright navs hang. Pass --headed only for live debugging.
    parser.add_argument("--headed", action="store_true", default=False, help="Show browser window (debug only)")
    parser.add_argument("--headless", dest="headed", action="store_false", help="Run hidden (default)")
    parser.add_argument("--url", default=None, help="Initial URL to navigate to")
    args = parser.parse_args()

    print(f"Starting Pliny Agent Browser on port {args.port} (headed={args.headed})...", file=sys.stderr)
    launch_browser(headless=not args.headed)

    if args.url:
        result = navigate(args.url)
        print(f"Navigated to: {result}", file=sys.stderr)

    # Single-threaded HTTPServer — Playwright's sync API is bound to the
    # greenlet/thread that initialized it. ThreadingHTTPServer breaks every
    # Playwright op with "Cannot switch to a different thread". Trade-off:
    # /status calls queue behind in-flight navigates, so watchdog HTTP
    # timeouts must be wide enough to absorb that (see server.py watchdog).
    server = HTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Agent browser API ready at http://localhost:{args.port}", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        # Persistent context owns the browser, so close it directly when
        # _browser is None (Firefox path). Chromium fallback has both.
        try:
            if _context:
                _context.close()
        except Exception:
            pass
        try:
            if _browser:
                _browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
