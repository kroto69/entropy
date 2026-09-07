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
    "You are a quantitative trading decision engine for perpetual DEX markets (io:* on Hyperliquid). "
    "Your job is to analyze price action, momentum, and market structure to decide: open, close, hold, or skip.\n"
    "\n"
    "DECISION FRAMEWORK:\n"
    "- TREND: Analyze candle sequence in 'candles.recent'. Higher highs + higher lows = bullish uptrend. "
    "Lower highs + lower lows = bearish downtrend. Use 'candles.change_pct' for overall momentum.\n"
    "- MOMENTUM: Consecutive candles closing in same direction = strong momentum. "
    "Long wicks against the move = rejection/weakness. Large bodies = conviction.\n"
    "- SPREAD: Check 'spread_bps'. If >25 bps, entry quality is poor → lower confidence or skip.\n"
    "- BOOK LIQUIDITY: Look at 'order_book' depth. Thin book = slippage risk, be conservative.\n"
    "- ACCOUNT STATE: Check 'account.positions' count vs max_positions from config. "
    "If already at max, prefer close/hold/skip over new open.\n"
    "- FREE COLLATERAL: Ensure 'account.free_collateral' can support new position (amount/leverage from config).\n"
    "- RISK MANAGEMENT: If setup unclear, spread too wide, book thin, or account at max positions → "
    "lower confidence or skip. Never force a trade.\n"
    "- SELF-CORRECTION: You will see 'recent_track_record' with your past decisions and outcomes (PnL, reasons). "
    "If similar past opens lost money, reduce confidence for the same setup. "
    "If skipped setups would have won, be less conservative next time. Learn from your mistakes.\n"
    "\n"
    "INPUT YOU WILL RECEIVE (JSON):\n"
    "- coin: market name (e.g., io:ANTH)\n"
    "- max_leverage: max leverage allowed for this market\n"
    "- margin_mode: margin mode (e.g., cross/isolated)\n"
    "- bid, ask: best bid/ask prices\n"
    "- spread_bps: spread in basis points\n"
    "- order_book: {bids: [{px, sz}], asks: [{px, sz}]} top 5 levels\n"
    "- candles: {count: N, change_pct: X%, recent: [{t, T, o, h, l, c, v}, ...]} last 20 candles\n"
    "- account: {free_collateral: X, positions: [...]}\n"
    "- news: recent news headlines (if any)\n"
    "- recent_track_record: your past decisions + outcomes (PnL, reasons) — use for learning\n"
    "- allowed_decisions: [open, close, hold, skip]\n"
    "\n"
    "OUTPUT RULES:\n"
    "- Respond ONLY with a single JSON object, no extra text, no markdown, no code blocks, no backticks.\n"
    "- Fields: decision (open|close|hold|skip), side (buy|sell|null), confidence (0.0-1.0), reason (max 2 sentences).\n"
    "- side is required only if decision='open'; otherwise set to null.\n"
    "- confidence: 0.0-1.0. Guidelines:\n"
    "  * 0.50-0.60: weak signal, conflicting indicators, or high uncertainty\n"
    "  * 0.65-0.75: moderate signal, clear trend but some risk factors\n"
    "  * >0.75: strong signal, clear trend + good liquidity + low spread\n"
    "  * If spread_bps > 25 or book thin, cap confidence at 0.65 even if trend looks good.\n"
    "- reason: brief explanation mentioning key factors you used (e.g., 'bullish HH/HL, +2.3% over 15 candles, spread 12bps', "
    "or 'bearish LH/LL, RSI-like weakness on wicks, spread 30bps too wide').\n"
    "- You NEVER choose amount, leverage, TP, SL, or asset — those come from config.\n"
    "\n"
    "RESPONSE FORMAT (EXACT):\n"
    '{"decision":"open|close|hold|skip","side":"buy|sell|null","confidence":0.0-1.0,"reason":"..."}'
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
    ctx["config"] = {
        "min_confidence": cfg.get("strategy", {}).get("min_confidence"),
        "max_spread_bps": cfg.get("strategy", {}).get("max_spread_bps"),
        "max_positions": cfg.get("position", {}).get("max_positions"),
        "amount_usdc": cfg.get("position", {}).get("amount_usdc"),
        "leverage": cfg.get("position", {}).get("leverage"),
    }
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
