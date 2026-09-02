"""AI decision adapter. Calls an [OI]-compatible chat endpoint with strict JSON output.

AI only returns open|close|hold|skip + side + confidence + reason.
Amount/TP/SL/asset never come from AI. Fallback: local heuristic.
"""
import json
import os
import urllib.request


def _load_env():
    path = "/entropy/.env"
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


_load_env()

from decision import build_prompt_context, fallback_decision, validate_ai_decision

SYSTEM_PROMPT = (
    "You are a price-trend trading decision engine using candle charts. "
    "Decide by trend only: if candles are rising, open long (buy); if falling, open short (sell). "
    "If no clear trend, hold or skip. Respond ONLY with a single JSON object: "
    '{"decision":"open|close|hold|skip","side":"buy|sell","confidence":0.0-1.0,"reason":"short"}. '
    "You cannot choose amount, leverage, TP, SL, or asset."
)


class AIError(RuntimeError):
    pass


def ai_configured():
    return bool(os.getenv("AI_API_KEY") and os.getenv("AI_ENDPOINT") and os.getenv("AI_MODEL"))


def _extract_json(text):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AIError("no JSON object in AI response")
    return json.loads(text[start:end + 1])


def ai_decision(coin, market_meta, book, candles, cfg, account=None, news=None, timeout=45):
    """Call AI endpoint; validate strictly; return normalized decision or raise AIError."""
    ctx = build_prompt_context(coin, market_meta, book, candles, account, news)
    payload = json.dumps({
        "model": os.getenv("AI_MODEL"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(ctx)},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
    }).encode()
    req = urllib.request.Request(
        os.getenv("AI_ENDPOINT"), data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('AI_API_KEY')}",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"unexpected AI response shape: {exc}") from exc
    raw = _extract_json(content)
    return validate_ai_decision(raw, market_meta, book, cfg)


def decide(coin, market_meta, book, candles, cfg, account=None, news=None):
    """AI if configured, else heuristic. Never raises; heuristic is the floor."""
    if ai_configured():
        try:
            return ai_decision(coin, market_meta, book, candles, cfg, account, news), "ai"
        except Exception as exc:
            return fallback_decision(market_meta, book, candles, cfg), f"ai_failed:{exc}"
    return fallback_decision(market_meta, book, candles, cfg), "heuristic"

if __name__ == "__main__":
    print("ai configured:", ai_configured())
# ponytail: single chat-completion contract; swap endpoint/model via env only.
