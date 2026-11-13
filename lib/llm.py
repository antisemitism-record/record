"""One place for calls to the Claude API, with retries and token accounting."""
import json
import os
import re
import time
import requests

API = "https://api.anthropic.com/v1/messages"
FAST = os.environ.get("EXTRACT_MODEL", "claude-haiku-5-5")


class Stopped(RuntimeError):
    """The API refused in a way retrying will not fix: no credit, spend limit reached, bad key."""


class Usage:
    def __init__(self):
        self.input_tokens = self.output_tokens = self.calls = 0


def ask(prompt, system=None, model=FAST, max_tokens=8000, usage=None):
    headers = {"x-api-key": "".join(os.environ["ANTHROPIC_API_KEY"].split()),
               "anthropic-version": "2023-06-01", "content-type": "application/json"}
    body = {"model": model, "max_tokens": max_tokens, "messages": [{"role": "user", "content": prompt}]}
    if system:
        body["system"] = system
    last = None
    for attempt in range(7):
        try:
            r = requests.post(API, headers=headers, json=body, timeout=600)
        except requests.RequestException as e:
            last = type(e).__name__
            time.sleep(5 * (attempt + 1))
            continue
        if r.status_code == 200:
            out = r.json()
            if usage is not None:
                u = out.get("usage", {})
                usage.input_tokens += u.get("input_tokens", 0) + u.get("cache_read_input_tokens", 0)
                usage.output_tokens += u.get("output_tokens", 0)
                usage.calls += 1
            return "".join(b.get("text", "") for b in out["content"] if b["type"] == "text")
        last = f"{r.status_code} {r.text[:300]}"
        if r.status_code in (429, 500, 502, 503, 529):
            time.sleep(min(90, 8 * (attempt + 1)))
            continue
        raise Stopped(f"Claude API refused: {last}")
    raise RuntimeError(f"Claude API failed: {last}")


def json_block(text, tag="incidents"):
    """Pull the JSON array out of <tag>...</tag>. Returns (list, parsed_ok)."""
    m = re.search(rf"<{tag}>\s*(\[.*\])\s*</{tag}>", text, re.S)
    if not m:
        return [], False
    try:
        return json.loads(m.group(1)), True
    except json.JSONDecodeError:
        return [], False
