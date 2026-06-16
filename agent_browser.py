#!/usr/bin/env python3
"""
PLINY AGENT BROWSER
Manages a dedicated Firefox instance for agent computer use.
Clones cookies from the user's real Firefox profile so the agent
is already logged into Twitter, ChatGPT, Meta.ai, etc.

Usage:
  from agent_browser import AgentBrowser
  ab = AgentBrowser()
  ab.provision()   # Clone cookies from real Firefox
  ab.launch()      # Launch agent Firefox window
  ab.screenshot()  # Capture just the agent's window
  ab.click(x, y)   # Click inside the agent's window
  ab.close()       # Kill the agent Firefox

The agent's Firefox runs as a completely separate instance
(--new-instance --profile ...) so it doesn't interfere with
the user's browsing.
"""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

AGENT_PROFILE_DIR = Path.home() / ".pliny-agent-firefox"
REAL_FIREFOX_PROFILES = Path.home() / "Library/Application Support/Firefox/Profiles"
FIREFOX_BIN = "/Applications/Firefox.app/Contents/MacOS/firefox"
SS_PATH = "/tmp/pliny_agent_browser.png"

# Files to copy from real profile for login session persistence
SESSION_FILES = [
    "cookies.sqlite",
    "cookies.sqlite-wal",
    "cookies.sqlite-shm",
    "key4.db",           # encryption key for stored passwords
    "logins.json",       # saved passwords
    "cert9.db",          # certificates
    "permissions.sqlite", # site permissions (camera, mic, etc)
    "storage.sqlite",    # localStorage / IndexedDB metadata
    "webappsstore.sqlite",  # localStorage data
    "formhistory.sqlite",   # form autofill
]

# Directories to copy (for localStorage/sessionStorage data)
SESSION_DIRS = [
    "storage/default",   # site-specific localStorage/IndexedDB
]


def find_real_profile() -> Path:
    """Find the user's main Firefox profile directory."""
    if not REAL_FIREFOX_PROFILES.exists():
        return None
    # Look for the default-release profile first
    for p in REAL_FIREFOX_PROFILES.iterdir():
        if p.is_dir() and "default-release" in p.name:
            return p
    # Fall back to any profile
    for p in REAL_FIREFOX_PROFILES.iterdir():
        if p.is_dir() and "default" in p.name:
            return p
    # Last resort: first directory
    for p in REAL_FIREFOX_PROFILES.iterdir():
        if p.is_dir():
            return p
    return None


class AgentBrowser:
    """Manages a dedicated Firefox instance for agent use."""

    def __init__(self, profile_dir: Path = None, width: int = 1280, height: int = 900):
        self.profile_dir = profile_dir or AGENT_PROFILE_DIR
        self.width = width
        self.height = height
        self.process = None
        self.window_id = None  # macOS window ID for targeted screenshots
        self.window_title = "Pliny Agent"  # used to find the window

    def provision(self, force: bool = False) -> dict:
        """Clone login cookies from the real Firefox profile.

        Args:
            force: If True, re-provision even if profile already exists.

        Returns:
            dict with status info.
        """
        real_profile = find_real_profile()
        if not real_profile:
            return {"ok": False, "error": "No Firefox profile found"}

        # Create agent profile dir
        self.profile_dir.mkdir(parents=True, exist_ok=True)

        # Check if already provisioned
        cookies_dst = self.profile_dir / "cookies.sqlite"
        if cookies_dst.exists() and not force:
            age_hours = (time.time() - cookies_dst.stat().st_mtime) / 3600
            if age_hours < 24:
                return {"ok": True, "status": "already_provisioned", "age_hours": round(age_hours, 1)}

        # Checkpoint WAL on cookie DB first so backup includes pending writes
        cookies_src = real_profile / "cookies.sqlite"
        if cookies_src.exists():
            try:
                subprocess.run(
                    ["sqlite3", str(cookies_src), "PRAGMA wal_checkpoint(TRUNCATE);"],
                    capture_output=True, text=True, timeout=10
                )
            except Exception:
                pass  # Non-fatal

        # Firefox locks its DB files while running. Use sqlite3 .backup for safe copy.
        copied = []
        failed = []

        for fname in SESSION_FILES:
            src = real_profile / fname
            dst = self.profile_dir / fname
            if not src.exists():
                continue
            try:
                if fname.endswith(".sqlite"):
                    # Use SQLite backup API to safely copy even if Firefox has it locked
                    r = subprocess.run(
                        ["sqlite3", str(src), f".backup '{dst}'"],
                        capture_output=True, text=True, timeout=10
                    )
                    if r.returncode == 0:
                        copied.append(fname)
                    else:
                        # Fallback: direct copy (works if Firefox isn't holding a write lock)
                        shutil.copy2(src, dst)
                        copied.append(fname + " (direct)")
                else:
                    shutil.copy2(src, dst)
                    copied.append(fname)
            except Exception as e:
                failed.append(f"{fname}: {e}")

        # Copy storage directories for localStorage data
        for dirname in SESSION_DIRS:
            src_dir = real_profile / dirname
            dst_dir = self.profile_dir / dirname
            if src_dir.exists() and src_dir.is_dir():
                try:
                    if dst_dir.exists():
                        shutil.rmtree(dst_dir)
                    shutil.copytree(src_dir, dst_dir, dirs_exist_ok=True)
                    copied.append(dirname + "/")
                except Exception as e:
                    failed.append(f"{dirname}: {e}")

        # Write user.js with anti-bot-detection prefs
        user_js = self.profile_dir / "user.js"
        user_js.write_text(
            '// Pliny Agent Browser — auto-generated config\n'
            'user_pref("browser.shell.checkDefaultBrowser", false);\n'
            'user_pref("browser.startup.homepage_override.mstone", "ignore");\n'
            'user_pref("browser.tabs.warnOnClose", false);\n'
            'user_pref("browser.tabs.warnOnCloseOtherTabs", false);\n'
            'user_pref("datareporting.policy.dataSubmissionEnabled", false);\n'
            'user_pref("toolkit.telemetry.enabled", false);\n'
            'user_pref("browser.newtabpage.enabled", false);\n'
            'user_pref("browser.startup.page", 0);\n'  # blank page on start
            'user_pref("browser.aboutConfig.showWarning", false);\n'
            'user_pref("dom.webnotifications.enabled", false);\n'  # no notification popups
            'user_pref("permissions.default.desktop-notification", 2);\n'  # block notifications
            '// Anti-bot-detection prefs\n'
            'user_pref("dom.webdriver.enabled", false);\n'  # hide webdriver flag
            'user_pref("marionette.enabled", false);\n'  # hide Marionette
            'user_pref("useAutomationExtension", false);\n'  # no automation extension
            'user_pref("privacy.resistFingerprinting", false);\n'  # DON'T resist — it makes us uniform
            'user_pref("general.platform.override", "MacIntel");\n'  # consistent platform
            'user_pref("general.appversion.override", "5.0 (Macintosh)");\n'
            'user_pref("privacy.trackingprotection.enabled", false);\n'  # don't block trackers (looks like real user)
            'user_pref("network.http.referer.spoofSource", false);\n'  # send natural referers
            'user_pref("media.peerconnection.enabled", true);\n'  # WebRTC on (bots often disable)
            'user_pref("webgl.disabled", false);\n'  # WebGL on (bots often disable)
        )

        return {
            "ok": True,
            "status": "provisioned",
            "profile": str(self.profile_dir),
            "source": str(real_profile),
            "copied": copied,
            "failed": failed,
        }

    def launch(self, url: str = "about:blank") -> dict:
        """Launch the agent's Firefox instance.

        Returns:
            dict with pid, window info.
        """
        if self.process and self.process.poll() is None:
            return {"ok": True, "status": "already_running", "pid": self.process.pid}

        if not self.profile_dir.exists():
            result = self.provision()
            if not result.get("ok"):
                return result

        cmd = [
            FIREFOX_BIN,
            "--new-instance",
            "--profile", str(self.profile_dir),
            "--window-size", f"{self.width},{self.height}",
            url,
        ]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                preexec_fn=os.setsid,
            )
        except FileNotFoundError:
            return {"ok": False, "error": "Firefox not found at " + FIREFOX_BIN}

        # Wait for window to appear
        time.sleep(3)

        # Find the window ID
        self._detect_window()

        return {
            "ok": True,
            "status": "launched",
            "pid": self.process.pid,
            "window_id": self.window_id,
            "profile": str(self.profile_dir),
        }

    def _detect_window(self):
        """Find the agent Firefox window ID for targeted screenshots."""
        try:
            # Get window list and find our Firefox instance by PID
            script = '''
            tell application "System Events"
                set windowList to {}
                repeat with proc in (every process whose unix id is %d)
                    repeat with win in (every window of proc)
                        set end of windowList to {name of win, id of win}
                    end repeat
                end repeat
                return windowList
            end tell
            ''' % self.process.pid
            r = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=5)
            if r.stdout.strip():
                # Parse AppleScript list
                self.window_title = "agent-firefox"
        except Exception:
            pass

        # Alternative: use CGWindowListCopyWindowInfo via python
        # For now, use the window-name approach with screencapture -l
        try:
            # Find window ID via macOS window server
            r = subprocess.run(
                ["osascript", "-e",
                 f'tell application "System Events" to get id of first window of (first process whose unix id is {self.process.pid})'],
                capture_output=True, text=True, timeout=5
            )
            if r.returncode == 0 and r.stdout.strip():
                self.window_id = r.stdout.strip()
        except Exception:
            pass

    def screenshot(self, output_path: str = None) -> str:
        """Capture just the agent's browser window.

        Returns:
            Path to the screenshot file.
        """
        out = output_path or SS_PATH

        if self.window_id:
            # Capture just this window by ID
            subprocess.run(
                ["screencapture", "-x", "-l", str(self.window_id), out],
                capture_output=True, timeout=10,
            )
        else:
            # Fallback: try to capture by window name matching
            # First bring agent firefox to front, then capture it
            if self.process:
                try:
                    script = f'''
                    tell application "System Events"
                        set frontmost of (first process whose unix id is {self.process.pid}) to true
                    end tell
                    '''
                    subprocess.run(["osascript", "-e", script],
                                 capture_output=True, timeout=5)
                    time.sleep(0.3)
                except Exception:
                    pass
            subprocess.run(
                ["screencapture", "-x", "-C", out],
                capture_output=True, timeout=10,
            )

        # Resize to standard dimensions
        if os.path.exists(out):
            subprocess.run(
                ["sips", "--resampleWidth", "1280", out, "--out", out],
                capture_output=True, timeout=10,
            )

        return out

    def get_window_bounds(self) -> dict:
        """Get the agent window's position and size in screen points."""
        if not self.process:
            return None
        try:
            script = f'''
            tell application "System Events"
                tell (first process whose unix id is {self.process.pid})
                    set w to first window
                    set p to position of w
                    set s to size of w
                    return (item 1 of p) & "," & (item 2 of p) & "," & (item 1 of s) & "," & (item 2 of s)
                end tell
            end tell
            '''
            r = subprocess.run(["osascript", "-e", script],
                             capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                parts = r.stdout.strip().split(",")
                return {
                    "x": int(parts[0]),
                    "y": int(parts[1]),
                    "w": int(parts[2]),
                    "h": int(parts[3]),
                }
        except Exception:
            pass
        return None

    def click(self, x: int, y: int):
        """Click at coordinates relative to the agent's window."""
        bounds = self.get_window_bounds()
        if bounds:
            # Convert window-relative coords to screen-absolute
            abs_x = bounds["x"] + x
            abs_y = bounds["y"] + y
        else:
            abs_x, abs_y = x, y

        subprocess.run(["cliclick", f"c:{abs_x},{abs_y}"],
                      capture_output=True, timeout=5)

    def type_text(self, text: str):
        """Type text into the agent's browser via clipboard paste."""
        # Focus the agent window first
        self.focus()
        time.sleep(0.2)
        # Copy to clipboard and paste
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(text.encode("utf-8"))
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using {command down}'],
            capture_output=True, timeout=5,
        )

    def press_key(self, key_code: int, modifiers: list = None):
        """Press a key in the agent's browser."""
        self.focus()
        time.sleep(0.1)
        if modifiers:
            mod_str = ", ".join(f"{m} down" for m in modifiers)
            script = f'tell application "System Events" to key code {key_code} using {{{mod_str}}}'
        else:
            script = f'tell application "System Events" to key code {key_code}'
        subprocess.run(["osascript", "-e", script],
                      capture_output=True, timeout=5)

    def navigate(self, url: str):
        """Navigate the agent's browser to a URL."""
        self.focus()
        time.sleep(0.3)
        # Cmd+L to focus address bar, type URL, press Return
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "l" using {command down}'],
            capture_output=True, timeout=5,
        )
        time.sleep(0.3)
        # Clear address bar and type URL
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "a" using {command down}'],
            capture_output=True, timeout=5,
        )
        time.sleep(0.1)
        proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
        proc.communicate(url.encode("utf-8"))
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to keystroke "v" using {command down}'],
            capture_output=True, timeout=5,
        )
        time.sleep(0.2)
        subprocess.run(
            ["osascript", "-e",
             'tell application "System Events" to key code 36'],  # Return
            capture_output=True, timeout=5,
        )

    def focus(self):
        """Bring the agent's browser window to front."""
        if self.process and self.process.poll() is None:
            try:
                script = f'''
                tell application "System Events"
                    set frontmost of (first process whose unix id is {self.process.pid}) to true
                end tell
                '''
                subprocess.run(["osascript", "-e", script],
                             capture_output=True, timeout=5)
            except Exception:
                pass

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def close(self):
        """Kill the agent's Firefox instance."""
        if self.process and self.process.poll() is None:
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=5)
            except Exception:
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except Exception:
                    pass
        self.process = None
        self.window_id = None

    def status(self) -> dict:
        return {
            "running": self.is_running(),
            "pid": self.process.pid if self.process else None,
            "window_id": self.window_id,
            "profile": str(self.profile_dir),
            "provisioned": (self.profile_dir / "cookies.sqlite").exists(),
        }


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pliny Agent Browser Manager")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("provision", help="Clone cookies from real Firefox")
    sub.add_parser("launch", help="Launch agent Firefox").add_argument("--url", default="about:blank")
    sub.add_parser("screenshot", help="Capture the agent window")
    sub.add_parser("status", help="Show agent browser status")
    sub.add_parser("close", help="Kill agent Firefox")
    sub.add_parser("navigate", help="Go to URL").add_argument("url")
    sub.add_parser("click", help="Click at x,y").add_argument("x", type=int)

    args = parser.parse_args()
    ab = AgentBrowser()

    if args.cmd == "provision":
        print(json.dumps(ab.provision(force=True), indent=2))
    elif args.cmd == "launch":
        print(json.dumps(ab.launch(getattr(args, "url", "about:blank")), indent=2))
    elif args.cmd == "screenshot":
        path = ab.screenshot()
        print(f"Screenshot saved to {path}")
    elif args.cmd == "status":
        print(json.dumps(ab.status(), indent=2))
    elif args.cmd == "close":
        ab.close()
        print("Agent browser closed.")
    elif args.cmd == "navigate":
        ab.navigate(args.url)
    else:
        parser.print_help()
