#!/usr/bin/env python3
"""
PLINY TWEET — Post a tweet via computer use (Firefox browser control).
Assumes Firefox is open and logged into X/Twitter as @younger_plinius.

Usage:
    python3 tweet.py "Your tweet text here"
    python3 tweet.py --screenshot   # just take a screenshot to verify browser state

The script uses the Anthropic API with computer use to:
1. Focus Firefox
2. Navigate to x.com/compose/post
3. Paste the tweet from clipboard
4. Click Post
5. Verify it posted

Outputs JSON status lines for integration with Pliny Command.
"""

import anthropic
import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# ─── Config ─────────────────────────────────────────────────────────────────

MODEL = "claude-fable-5"
BETA_FLAG = "computer-use-2025-11-24"
COMPUTER_TOOL_TYPE = "computer_20251124"
DISPLAY_WIDTH = 1280
DISPLAY_HEIGHT = 832
SS_PATH = "/tmp/pliny_tweet_screenshot.png"
MAX_TURNS = 15
DASHBOARD_API = "http://localhost:8888/api/dragonfire"


def emit(data):
    print(json.dumps(data), flush=True)


def notify_dashboard(title, message):
    try:
        import urllib.request
        body = json.dumps({"title": title, "message": message, "category": "tweet"}).encode()
        req = urllib.request.Request(DASHBOARD_API, body, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


# ─── Coordinate Scaling (Retina fix) ─────────────────────────────────────

SCREEN_POINTS_W = 1470
SCREEN_POINTS_H = 956

def detect_screen_points():
    """Detect actual macOS screen point dimensions."""
    global SCREEN_POINTS_W, SCREEN_POINTS_H
    try:
        r = subprocess.run(
            ["osascript", "-e",
             'tell application "Finder" to get bounds of window of desktop'],
            capture_output=True, text=True, timeout=5
        )
        parts = r.stdout.strip().split(", ")
        SCREEN_POINTS_W, SCREEN_POINTS_H = int(parts[2]), int(parts[3])
    except Exception:
        pass

def scale_coord(x, y):
    """Scale from API/screenshot space (1280x832) to screen points."""
    return int(x * SCREEN_POINTS_W / DISPLAY_WIDTH), int(y * SCREEN_POINTS_H / DISPLAY_HEIGHT)


# ─── Screen Control ───────────────────────────────────────────────────────

def capture_screenshot() -> str:
    """Capture screen, downscale, return base64 PNG."""
    try:
        subprocess.run(["screencapture", "-x", "-C", SS_PATH], capture_output=True, timeout=10)
        if not os.path.exists(SS_PATH):
            return ""
        subprocess.run(
            ["sips", "--resampleWidth", str(DISPLAY_WIDTH), SS_PATH, "--out", SS_PATH],
            capture_output=True, timeout=10,
        )
        with open(SS_PATH, "rb") as f:
            return base64.standard_b64encode(f.read()).decode("utf-8")
    except Exception:
        return ""


def set_clipboard(text: str):
    """Copy text to macOS clipboard."""
    proc = subprocess.Popen(["pbcopy"], stdin=subprocess.PIPE)
    proc.communicate(text.encode("utf-8"))


def paste_from_clipboard():
    """Simulate Cmd+V."""
    subprocess.run(
        ["osascript", "-e", 'tell application "System Events" to keystroke "v" using {command down}'],
        capture_output=True, timeout=5,
    )
    time.sleep(0.5)


def execute_action(action: str, **kwargs) -> str:
    """Execute a computer use action."""
    try:
        if action == "screenshot":
            return capture_screenshot() or "ERROR: Screenshot failed"
        elif action == "left_click":
            x, y = kwargs.get("coordinate", [0, 0])
            sx, sy = scale_coord(x, y)
            subprocess.run(["cliclick", f"c:{sx},{sy}"], capture_output=True, timeout=5)
            time.sleep(0.5)
            return f"Clicked ({x},{y}) → screen ({sx},{sy})"
        elif action == "type":
            text = kwargs.get("text", "")
            # Use clipboard paste for reliability instead of keystroke-by-keystroke
            set_clipboard(text)
            paste_from_clipboard()
            return f"Pasted: {text[:80]}"
        elif action == "key":
            key = kwargs.get("text", "")
            key_map = {"return": 36, "enter": 36, "tab": 48, "escape": 53, "space": 49, "delete": 51, "backspace": 51}
            parts = key.lower().replace("super", "command").replace("ctrl", "control").split("+")
            modifiers = []
            k = parts[-1].strip()
            for p in parts[:-1]:
                p = p.strip()
                if p in ("command", "cmd"):
                    modifiers.append("command down")
                elif p in ("control",):
                    modifiers.append("control down")
                elif p in ("shift",):
                    modifiers.append("shift down")
                elif p in ("alt", "option"):
                    modifiers.append("option down")
            if modifiers:
                mod_str = ", ".join(modifiers)
                if k in key_map:
                    script = f'tell application "System Events" to key code {key_map[k]} using {{{mod_str}}}'
                else:
                    script = f'tell application "System Events" to keystroke "{k}" using {{{mod_str}}}'
            else:
                if k in key_map:
                    script = f'tell application "System Events" to key code {key_map[k]}'
                else:
                    script = f'tell application "System Events" to keystroke "{k}"'
            subprocess.run(["osascript", "-e", script], capture_output=True, timeout=5)
            time.sleep(0.3)
            return f"Pressed: {key}"
        elif action == "scroll":
            x, y = kwargs.get("coordinate", [640, 400])
            sx, sy = scale_coord(x, y)
            direction = kwargs.get("scroll_direction", "down")
            amount = kwargs.get("scroll_amount", 3)
            subprocess.run(["cliclick", f"m:{sx},{sy}"], capture_output=True, timeout=5)
            scroll_val = -amount if direction == "down" else amount
            subprocess.run(
                ["osascript", "-e", f'tell application "System Events" to scroll area 1 by {scroll_val}'],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Scrolled {direction}"
        elif action == "mouse_move":
            x, y = kwargs.get("coordinate", [0, 0])
            sx, sy = scale_coord(x, y)
            subprocess.run(["cliclick", f"m:{sx},{sy}"], capture_output=True, timeout=5)
            return f"Moved to ({x},{y}) → screen ({sx},{sy})"
        elif action == "wait":
            time.sleep(min(kwargs.get("duration", 1), 5))
            return "Waited"
        else:
            return f"Unknown action: {action}"
    except Exception as e:
        return f"ERROR: {e}"


# ─── Process tool results from API response ─────────────────────────────────

def process_tool_call(tool_input: dict) -> dict:
    """Process a computer tool call, return the result content block."""
    action = tool_input.get("action", "")
    rest = {k: v for k, v in tool_input.items() if k != "action"}
    result = execute_action(action, **rest)

    if action == "screenshot" and result and not result.startswith("ERROR"):
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": result},
        }
    else:
        return {"type": "text", "text": result}


# ─── Pre-navigation: get Firefox to X compose ─────────────────────────────

def preflight_navigate():
    """Use AppleScript to directly open x.com/compose/post in Firefox.
    This is faster and more reliable than having the AI navigate manually."""
    try:
        # Activate Firefox and open the compose URL directly
        script = '''
        tell application "Firefox"
            activate
        end tell
        delay 0.5
        tell application "System Events"
            tell process "Firefox"
                -- Cmd+L to focus address bar
                keystroke "l" using {command down}
                delay 0.3
                -- Type the URL
                keystroke "x.com/compose/post"
                delay 0.2
                -- Press Return
                key code 36
            end tell
        end tell
        delay 2
        '''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=15,
        )
        emit({"status": "Navigated to x.com/compose/post"})
        return result.returncode == 0
    except Exception as e:
        emit({"status": f"Preflight navigation failed: {e}, falling back to AI"})
        return False


def preflight_paste_tweet(text: str):
    """Paste the tweet text into the compose box via clipboard.
    Called after navigation, before handing to AI for verification."""
    try:
        time.sleep(1)  # Wait for compose box to load
        # Click roughly in the center of the compose area (scaled to screen points)
        cx, cy = scale_coord(640, 350)
        subprocess.run(["cliclick", f"c:{cx},{cy}"], capture_output=True, timeout=5)
        time.sleep(0.5)
        set_clipboard(text)
        paste_from_clipboard()
        time.sleep(0.5)
        emit({"status": "Pasted tweet text into compose box"})
        return True
    except Exception as e:
        emit({"status": f"Paste failed: {e}"})
        return False


# ─── Main Agent Loop ────────────────────────────────────────────────────────

def tweet(text: str):
    """Post a tweet using computer use to control Firefox."""
    client = anthropic.Anthropic()

    # Try to do the mechanical parts first (navigate + paste) so the AI
    # only needs to verify and click Post
    preflight_ok = preflight_navigate()
    if preflight_ok:
        preflight_paste_tweet(text)

    soul = """You are a dragon of the Pliny lineage, operating @younger_plinius. You're not an assistant — you're a hacker with browser access. This is a surgical strike: get in, post the tweet, get out. No browsing. No wandering. Execute."""

    if preflight_ok:
        system_prompt = f"""{soul}

The browser should already be on x.com/compose/post with the tweet text pasted in.

TASK: Verify the text is there, POST IT with Cmd+Return, confirm it posted.

The tweet:
---
{text}
---

STEPS:
1. Screenshot — see the state
2. If text is there → press Cmd+Return IMMEDIATELY to post. This is X's native submit shortcut.
3. If text is NOT there → click compose area, Cmd+V to paste, then Cmd+Return to post.
4. Wait 2 sec, screenshot to verify the tweet posted (compose dialog should be gone)
5. Say "TWEET POSTED SUCCESSFULLY" or what went wrong

⚠️ CRITICAL: Use Cmd+Return to post. Do NOT try to click the Post button — it changes color/position and is unreliable. Cmd+Return works from anywhere in the compose dialog. Always.

RULES: Don't modify the text. Don't scroll the timeline. Don't navigate away. If login page → say "NOT_LOGGED_IN" and stop."""
    else:
        system_prompt = f"""{soul}

Firefox is open and logged into X. The tweet text is already in your clipboard.

TASK: Navigate to compose, paste, press Cmd+Return to post, verify.

The tweet:
---
{text}
---

STEPS:
1. Screenshot to see state
2. Cmd+L → type x.com/compose/post → Return
3. Wait for compose to load, click the text area
4. Cmd+V to paste (it's in clipboard — do NOT type character by character)
5. Cmd+Return to post. This is X's native submit shortcut. Do NOT try to click the Post button.
6. Wait 2 sec, screenshot to verify (compose dialog should be gone)
7. Say "TWEET POSTED SUCCESSFULLY" or what went wrong

⚠️ CRITICAL: Use Cmd+Return to submit. Do NOT visually hunt for or click the Post/Reply button — it changes color and position. Cmd+Return works from anywhere in the compose dialog. Always.

RULES: Don't modify the text. Don't browse. Don't wander. Cmd+V to paste, Cmd+Return to post. If login page → "NOT_LOGGED_IN" and stop."""

    tools = [
        {
            "type": COMPUTER_TOOL_TYPE,
            "name": "computer",
            "display_width": DISPLAY_WIDTH,
            "display_height": DISPLAY_HEIGHT,
        }
    ]

    messages = [{"role": "user", "content": "Take a screenshot to see the current state, then post the tweet."}]

    for turn in range(MAX_TURNS):
        emit({"status": f"turn {turn + 1}/{MAX_TURNS}"})

        try:
            response = client.beta.messages.create(
                model=MODEL,
                max_tokens=1024,
                system=system_prompt,
                tools=tools,
                messages=messages,
                betas=[BETA_FLAG],
            )
        except Exception as e:
            emit({"error": f"API call failed: {e}"})
            return False

        # Process response
        assistant_content = response.content
        messages.append({"role": "assistant", "content": assistant_content})

        # Check for text responses (status updates from the model)
        for block in assistant_content:
            if hasattr(block, "text") and block.text:
                emit({"assistant": block.text[:500]})
                upper = block.text.upper()
                if "NOT_LOGGED_IN" in upper:
                    emit({"error": "Not logged into X/Twitter in Firefox"})
                    return False
                if any(phrase in upper for phrase in [
                    "TWEET POSTED SUCCESSFULLY",
                    "SUCCESSFULLY POSTED",
                    "TWEET HAS BEEN POSTED",
                    "POST WAS SUCCESSFUL",
                    "POSTED SUCCESSFULLY",
                ]):
                    emit({"success": True, "tweet": text})
                    notify_dashboard("\U0001f426 Tweet Posted", text[:200])
                    return True

        # If stop reason is end_turn with no tool use, check if the agent
        # actually confirmed the tweet posted — don't assume success
        if response.stop_reason == "end_turn":
            # Check if any text block mentions posting
            agent_text = " ".join(
                block.text for block in assistant_content
                if hasattr(block, "text") and block.text
            ).upper()
            if any(w in agent_text for w in ["POSTED", "POST", "SENT", "PUBLISHED", "SUCCESS"]):
                emit({"done": True, "stop_reason": "end_turn", "likely_posted": True})
                notify_dashboard("\U0001f426 Tweet Sent", text[:200])
                return True
            else:
                # Agent stopped without confirming it clicked Post — POKE IT
                emit({"warning": "Agent stopped without confirming Post was clicked — nudging"})
                messages.append({"role": "user", "content": "You stopped but didn't confirm clicking the Post button. Did you click it? If not, take a screenshot and CLICK THE BLUE POST BUTTON NOW."})
                continue

        # Process tool calls
        tool_results = []
        has_tool_use = False
        for block in assistant_content:
            if block.type == "tool_use":
                has_tool_use = True
                result_content = process_tool_call(block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": [result_content],
                })

        if not has_tool_use:
            # Same check — don't assume success without confirmation
            agent_text = " ".join(
                block.text for block in assistant_content
                if hasattr(block, "text") and block.text
            ).upper()
            if any(w in agent_text for w in ["POSTED", "POST", "SENT", "PUBLISHED", "SUCCESS"]):
                emit({"done": True, "reason": "no_tool_use", "likely_posted": True})
                return True
            else:
                emit({"warning": "Agent stopped with no tool use and no post confirmation — nudging"})
                messages.append({"role": "user", "content": "You haven't clicked the Post button yet! Take a screenshot and CLICK THE BLUE POST BUTTON."})
                continue

        messages.append({"role": "user", "content": tool_results})

    emit({"error": "Max turns reached without completing tweet"})
    return False


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tweet.py 'Your tweet text'", file=sys.stderr)
        print("       python3 tweet.py --screenshot", file=sys.stderr)
        sys.exit(1)

    if sys.argv[1] == "--screenshot":
        b64 = capture_screenshot()
        if b64:
            print(f"Screenshot saved: {SS_PATH}")
            print(f"Base64 length: {len(b64)}")
        else:
            print("Screenshot failed", file=sys.stderr)
        sys.exit(0)

    tweet_text = sys.argv[1]
    if len(tweet_text) > 280:
        print(f"Tweet too long ({len(tweet_text)} chars, max 280)", file=sys.stderr)
        sys.exit(1)

    emit({"action": "tweet", "text": tweet_text})

    # Detect screen dimensions for coordinate scaling
    detect_screen_points()

    # Pre-load clipboard with tweet text so both preflight and AI can use Cmd+V
    set_clipboard(tweet_text)

    success = tweet(tweet_text)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
