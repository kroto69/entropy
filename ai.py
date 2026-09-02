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
    "If no clear trend, hold or skip. "
    "You will also see 'recent_track_record': your own past decisions and their outcomes "
    "(open reasons, skip reasons, close PnL). Use it to self-correct: "
    "if similar past opens lost money, lower your confidence for the same setup; "
    "if skipped setups would have won, be less conservative. "
    "Respond ONLY with a single JSON object: "
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


def ai_decision(coin, market_meta, book, candles, cfg, account=None, news=None, timeout=45, context=None):
    """Call AI endpoint; validate strictly; return normalized decision or raise AIError."""
    ctx = build_prompt_context(coin, market_meta, book, candles, account, news)
    if context:
        ctx["recent_track_record"] = context
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
    """AI if configured, else heuristic. AI also gets its own recent track record."""
    if ai_configured():
        try:
            return ai_decision(coin, market_meta, book, candles, cfg, account, news,
                               context=recent_context(coin)), "ai"
        except Exception as exc:
            return fallback_decision(market_meta, book, candles, cfg), f"ai_failed:{exc}"
    return fallback_decision(market_meta, book, candles, cfg), "heuristic"


def recent_context(coin=None, limit=8):
    """Feed the AI its own recent decisions + outcomes so it learns from them.

    Returns a compact summary: last N learning-log events (same coin first),
    plus a simple win/loss tally. The AI can see why past opens won or lost
    and which skips missed a move — and adjust confidence accordingly.
    """
    try:
        from learning import read_all
        rows = read_all()
    except Exception:
        return []
    if not rows:
        return []
    if coin:
        same = [r for r in rows if r.get("coin") == coin]
        rest = [r for r in rows if r.get("coin") != coin]
        rows = (same + rest)[:limit]
    else:
        rows = rows[-limit:]
    out = []
    for r in rows:
        if r.get("type") not in ("open", "close", "decision", "no_fill"):
            continue
        item = {"t": r.get("type"), "coin": r.get("coin"),
                "side": r.get("side"), "conf": r.get("confidence"),
                "reason": (r.get("reason") or "")[:80],
                "ts": (r.get("recorded_at") or "")[:19]}
        if "pnl" in r:
            item["pnl"] = round(r["pnl"], 4)
        if r.get("risk"):
            item["risk"] = r["risk"]
        out.append(item)
    return out

if __name__ == "__main__":
    print("ai configured:", ai_configured())
# ponytail: single chat-completion contract; swap endpoint/model via env only.
