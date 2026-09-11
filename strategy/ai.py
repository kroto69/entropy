"""AI decision adapter. Calls an [OI]-compatible chat endpoint with strict JSON output.

AI only returns open|close|hold|skip + side + confidence + reason.
Amount/TP/SL/asset never come from AI. Fallback: local heuristic.
"""
import json
import os
import time
import urllib.error
import urllib.request


def _load_env():
    from core.env import load_env as _load
    _load("/entropy/.env")


_load_env()

from strategy.decision import (build_prompt_context, fallback_decision,
                                validate_ai_decision, build_ai_market_snapshot)

SYSTEM_PROMPT = (
    "You are the primary market analyst for io:* perpetual markets. Analyze supplied JSON context and decide open, close, hold, or skip.\n"
    "Use only CLOSED candles in closed_15m and closed_5m; never infer from an active candle or invent missing data.\n"
    "Analyze 15m HH/HL or LH/LL structure, momentum, consecutive closes, EMA20/EMA50 and gap, RSI14, ATR, wicks/body, pullback/reclaim, breakout quality, extension, support/resistance room. Analyze 5m structure, impulse/pullback/reclaim/breakdown, candle counts, latest close, body/wicks, and momentum for timing.\n"
    "Consider spread, depth, imbalance, account capacity, same_coin_position, and free collateral. Prefer HOLD when mixed, late, extended, low-liquidity, near resistance/support, or timing is unclear. Never chase a vertical move.\n"
    "If data_valid is false, output skip. If same_coin_position is true or max positions/collateral prevent entry, output hold or skip.\n"
    "Execution context is awareness only. Never change amount_usdc, leverage, take_profit_pct, stop_loss_pct, max_hold_minutes, asset, order type, price, size, or exchange payload. Do not recommend scaling, averaging down, pyramiding, or stop changes.\n"
    "AI has no execution authority. Deterministic validator, entry-quality veto, risk gate, and dry-run/live executor protection always apply after this response. If uncertain, output hold.\n"
    "Respond ONLY with exactly one JSON object, no prose, markdown, or code fences. Required schema: "
    '{"decision":"open|close|hold|skip","side":"buy|sell|null","confidence":0.0,"reason":"max 200 chars, concrete supplied-data evidence"}'
)


class AIError(RuntimeError):
    pass


POSITION_PROMPT = (
    "You are the position manager for an ALREADY-OPEN io:* perpetual position. Decide whether to keep it or close it now.\n"
    "Use only CLOSED candles in closed_15m and closed_5m. Analyze trend structure, EMA20/EMA50, RSI14, ATR, momentum, and where price sits relative to entry, distance to the hard TP/SL, held_minutes, and unrealized PnL.\n"
    "Output CLOSE ONLY when the evidence says the thesis has failed or momentum has clearly reversed against the position; otherwise output HOLD and let the hard TP/SL work.\n"
    "Decide only hold or close. You may NEVER open, reverse, add to, scale, average down, or pyramid. You may NEVER change, move, widen, or cancel the hard TP/SL; those stay active and are the final authority.\n"
    "Closing is reduce-only and never selects size, price, leverage, or order type. Deterministic close safety checks, reduce-only enforcement, and the executor protection always apply after this response. If uncertain, output hold.\n"
    "Respond ONLY with exactly one JSON object, no prose, markdown, or code fences. Required schema: "
    '{"decision":"hold|close","side":null,"confidence":0.0,"reason":"max 200 chars, concrete supplied-data evidence"}'
)


def position_ai_decision(coin, position, market_meta, book, candles, timing_candles, cfg, account=None, context=None, timeout=45):
    """Position-manager AI. Returns only normalized HOLD/CLOSE; never entry parameters."""
    snapshot = build_ai_market_snapshot(coin, market_meta, book, candles,
                                        timing_candles=timing_candles, account=account, cfg=cfg)
    if not snapshot.get("data_valid"):
        raise AIError("invalid position snapshot")
    ctx = build_prompt_context(coin, market_meta, book, candles, account, snapshot=snapshot)
    ctx["active_position"] = {"side": position.get("side"), "size": position.get("size"),
                               "entry_px": position.get("entry_px"), "mark_px": position.get("mark_px"),
                               "unrealized_pnl": position.get("u_pnl"), "held_minutes": position.get("held_minutes"),
                               "hard_tp": position.get("tp"), "hard_sl": position.get("sl")}
    if context:
        ctx["recent_track_record"] = context
    payload = json.dumps({"model": os.getenv("AI_MODEL"), "messages": [
        {"role": "system", "content": POSITION_PROMPT},
        {"role": "user", "content": json.dumps(ctx)}], "temperature": 0.2, "max_tokens": 300}).encode()
    req = urllib.request.Request(os.getenv("AI_ENDPOINT"), data=payload, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {os.getenv('AI_API_KEY')}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    choice = data["choices"][0]
    if choice.get("finish_reason") == "length":
        raise AIError("position AI response truncated")
    raw = _extract_json(choice["message"]["content"])
    if not isinstance(raw, dict) or raw.get("decision") not in ("hold", "close"):
        raise AIError("position AI decision must be hold|close")
    conf = raw.get("confidence", 0)
    if not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
        raise AIError("position AI confidence invalid")
    return {"decision": raw["decision"], "side": None, "confidence": float(conf),
            "reason": str(raw.get("reason", ""))[:200]}


def position_monitor_due(positions, last_candle_by_coin, candles_by_coin):
    """Return positions whose latest closed 15m candle has changed."""
    due = []
    from strategy.indicators.price_action import closed_candles
    for p in positions:
        coin = p.get("coin")
        closed = closed_candles(candles_by_coin.get(coin) or [])
        if not closed:
            continue
        latest = closed[-1].get("T", closed[-1].get("t"))
        if latest is not None and latest != last_candle_by_coin.get(coin):
            due.append((coin, latest))
    return due


def ai_configured():
    return bool(os.getenv("AI_API_KEY") and os.getenv("AI_ENDPOINT") and os.getenv("AI_MODEL"))


def _extract_json(text):
    text = text.strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise AIError("no JSON object in AI response")
    return json.loads(text[start:end + 1])


def ai_decision(coin, market_meta, book, candles, cfg, account=None, news=None, timeout=45, context=None, snapshot=None):
    """Call AI endpoint; validate strictly; return normalized decision or raise AIError.

    Retries transient 5xx/network failures once. Fail-closed caller still vetoes.
    """
    ctx = build_prompt_context(coin, market_meta, book, candles, account, news, snapshot=snapshot)
    strat = cfg.get("strategy", {})
    strat_cfg = strat if isinstance(strat, dict) else {}
    ctx["config"] = {
        "min_confidence": strat_cfg.get("min_confidence"),
        "max_spread_bps": strat_cfg.get("max_spread_bps"),
        "max_positions": cfg.get("position", {}).get("max_positions"),
        "amount_usdc": cfg.get("position", {}).get("amount_usdc"),
        "leverage": cfg.get("position", {}).get("leverage"),
    }
    ctx["active_strategy"] = strat if isinstance(strat, str) else strat_cfg.get("decision_mode")
    ctx["strategy_params"] = cfg.get("strategy_params", {})
    if context:
        ctx["recent_track_record"] = context
    payload = json.dumps({
        "model": os.getenv("AI_MODEL"),
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(ctx)},
        ],
        "temperature": 0.2,
        "max_tokens": 600,
    }).encode()
    req = urllib.request.Request(
        os.getenv("AI_ENDPOINT"), data=payload, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {os.getenv('AI_API_KEY')}",
        },
    )
    data = None
    last_exc = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code >= 500 and attempt == 0:
                time.sleep(1.5)
                continue
            raise
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise
    if data is None:
        raise AIError(f"AI request failed after retries: {last_exc}")
    try:
        choice = data["choices"][0]
        if choice.get("finish_reason") == "length":
            raise AIError("AI response truncated (finish_reason=length)")
        content = choice["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError(f"unexpected AI response shape: {exc}") from exc
    raw = _extract_json(content)
    # Only closed candles reach validator/gates; AI cannot see/use active candle.
    from strategy.indicators.price_action import closed_candles as _closed
    closed_input = _closed(candles)
    indicators = {"ema20": (ctx.get("closed_15m") or {}).get("ema20")}
    from strategy.indicators import calculate_rsi as _calc_rsi
    _closes = [float(c.get("c") or c.get("close") or 0) for c in closed_input]
    _closes = [c for c in _closes if c > 0]
    if len(_closes) >= 15:
        indicators["rsi_series"] = _calc_rsi(_closes, 14)
    return validate_ai_decision(raw, market_meta, book, cfg, candles=closed_input,
                                indicators=indicators)


def decide(coin, market_meta, book, candles, cfg, account=None, news=None, timing_candles=None):
    """Select deterministic strategy or AI according to root ``strategy`` + ``ai_mode``."""
    strat = cfg.get("strategy")
    mode = cfg.get("ai_mode", "primary")

    # Unknown roots fail closed; dict means legacy strategy config.
    if not (strat in ("ai", "ohlc") or isinstance(strat, dict)):
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": f"unknown strategy: {strat}"}, "config"
    if mode not in ("primary", "off", "veto"):
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": f"unknown ai_mode: {mode}"}, "config"

    # --- AI-primary: AI is the sole signal generator, no OHLC/5m precondition ---
    if strat == "ai" and mode == "primary":
        snapshot = build_ai_market_snapshot(
            coin, market_meta, book, candles, timing_candles=timing_candles,
            account=account, cfg=cfg)
        if not snapshot.get("data_valid"):
            return {"decision": "skip", "side": None, "confidence": 0.0,
                    "reason": "invalid or missing market snapshot data"}, "ai_primary_data_error"
        try:
            ai_sig = ai_decision(coin, market_meta, book, candles, cfg, account, news,
                                 context=recent_context(coin), snapshot=snapshot)
            return ai_sig, "ai_primary"
        except Exception as exc:
            print(f"[ai_primary] ERROR for {coin}: {type(exc).__name__}: {exc}")
            return {"decision": "skip", "side": None, "confidence": 0.0,
                    "reason": f"AI primary unavailable ({type(exc).__name__})"}, "ai_primary_error"

    candidate = fallback_decision(market_meta, book, candles, cfg, timing_candles)
    if mode == "off":
        return candidate, "strategy"
    if mode == "veto":
        if candidate.get("decision") != "open":
            return candidate, "strategy_veto_no_candidate"
        try:
            ai_sig = ai_decision(coin, market_meta, book, candles, cfg, account, news,
                                 context=recent_context(coin))
            if ai_sig.get("decision") != "open" or ai_sig.get("side") != candidate.get("side"):
                return {"decision": "hold", "side": None, "confidence": 0.0,
                        "reason": "AI veto: disagreement with OHLC candidate"}, "ai_veto"
            return ai_sig, "ai_veto_agree"
        except Exception as exc:
            print(f"[ai_veto] ERROR for {coin}: {type(exc).__name__}: {exc}")
            return {"decision": "hold", "side": None, "confidence": 0.0,
                    "reason": f"AI veto: unavailable ({type(exc).__name__}: {exc})"}, "ai_veto_error"
    if mode not in ("primary", "off", "veto"):
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": f"unknown ai_mode: {mode}"}, "config"
    if ai_configured():
        try:
            return ai_decision(coin, market_meta, book, candles, cfg, account, news,
                               context=recent_context(coin)), "ai"
        except Exception as exc:
            return candidate, f"ai_failed:{exc}"
    return candidate, "heuristic"


def recent_context(coin=None, limit=8):
    """Feed the AI its own recent decisions + outcomes so it learns from them.

    Returns a compact summary: last N learning-log events (same coin first),
    plus a simple win/loss tally. The AI can see why past opens won or lost
    and which skips missed a move — and adjust confidence accordingly.
    """
    try:
        from report.learning import read_all
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
