#!/usr/bin/env python3
"""
Red Team Send via OpenRouter — sends prompts to any model through OpenRouter API.
Posts prompt + response to the Pliny Command dashboard.

Usage:
    python3 rt_send_or.py <attempt> <model_id> <<'PROMPT'
    Your prompt text here
    PROMPT

Model IDs: anthropic/claude-fable-5, openai/gpt-4o, google/gemini-2.5-pro, etc.
"""

import json
import os
import sys
import urllib.request

DASHBOARD_API = "http://localhost:8888/api/redteam/chat"
OPENROUTER_API = "https://openrouter.ai/api/v1/chat/completions"
API_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def post_to_dashboard(data):
    try:
        body = json.dumps(data).encode()
        req = urllib.request.Request(DASHBOARD_API, body, {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
    except Exception:
        pass


def send_to_openrouter(prompt, model_id):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}",
        "HTTP-Referer": "https://pliny.red-team",
        "X-Title": "Pliny Red Team",
    }
    payload = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
        "temperature": 1.0,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(OPENROUTER_API, body, headers)
    try:
        resp = urllib.request.urlopen(req, timeout=120)
        data = json.loads(resp.read())
        choice = data["choices"][0]
        text = choice.get("message", {}).get("content") or choice.get("text") or ""
        if not text and "error" in data:
            text = f"[API error: {data['error']}]"
        if not text:
            finish = choice.get("finish_reason", "unknown")
            if finish == "refusal":
                text = f"[MODEL HARD REFUSAL — zero tokens generated. finish_reason=refusal]"
            else:
                text = f"[Empty response. finish_reason={finish}. Raw: {json.dumps(data)[:500]}]"
        usage = data.get("usage", {})
        meta = []
        if usage.get("total_tokens"):
            meta.append(f"{usage['total_tokens']} tokens")
        model_used = data.get("model", model_id)
        meta.append(f"model: {model_used}")
        if meta:
            text += f"\n\n`[{' · '.join(meta)}]`"
        return text
    except Exception as e:
        return f"[ERROR — OpenRouter ({model_id}): {e}]"


def main():
    if len(sys.argv) < 3:
        print("Usage: python3 rt_send_or.py <attempt> <model_id>")
        print("Prompt is read from stdin.")
        sys.exit(1)

    attempt = int(sys.argv[1])
    model_id = sys.argv[2]
    prompt = sys.stdin.read().strip()

    if not prompt:
        print("[ERROR — empty prompt]")
        sys.exit(1)

    # Report prompt
    post_to_dashboard({
        "type": "prompt",
        "text": prompt,
        "attempt": attempt,
        "target": f"openrouter/{model_id}",
    })

    # Typing indicator
    post_to_dashboard({"type": "typing", "attempt": attempt})

    # Send
    response = send_to_openrouter(prompt, model_id)

    # Report response
    post_to_dashboard({
        "type": "response",
        "text": response,
        "attempt": attempt,
        "target": f"openrouter/{model_id}",
    })

    print(response)


if __name__ == "__main__":
    main()
