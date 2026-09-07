"""Phase 3: decision engine.

AI (or fallback heuristic) outputs only: open|close|hold|skip + side + confidence.
Amount, TP, SL, leverage, asset ID never come from AI; they come from config.
This module produces decisions only; it cannot place orders.
"""
import json
from pathlib import Path

from indicators import calculate_ema, calculate_rsi, calculate_atr

VALID = ("open", "close", "hold", "skip")


class DecisionError(Exception):
    pass


def load_config(path="/entropy/config.json"):
    return json.loads(Path(path).read_text())


def _candles_features(candles):
    if not candles:
        return None
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    closes = [c for c in closes if c > 0]
    if len(closes) < 2:
        return None
    return {
        "n": len(closes),
        "first": closes[0],
        "last": closes[-1],
        "change_pct": (closes[-1] - closes[0]) / closes[0] * 100,
    }


def _indicators_snapshot(candles):
    """Latest indicator values + regime detection from candles."""
    if not candles or len(candles) < 15:
        return None
    ema20 = calculate_ema(candles, 20)
    ema50 = calculate_ema(candles, 50)
    rsi14 = calculate_rsi(candles, 14)
    atr14 = calculate_atr(candles, 14)
    closes = [float(c.get("close") or c.get("c") or 0) for c in candles]
    closes = [c for c in closes if c > 0]
    if not closes:
        return None
    last = closes[-1]
    e20 = ema20[-1]
    e50 = ema50[-1]
    r = rsi14[-1]
    a = atr14[-1]
    snap = {
        "ema20": round(e20, 6) if e20 else None,
        "ema50": round(e50, 6) if e50 else None,
        "rsi14": round(r, 2) if r else None,
        "atr14": round(a, 6) if a else None,
        "atr_pct": round(a / last * 100, 3) if a and last else None,
    }
    # Regime: trend from EMA relation, vol from ATR% of price
    if e20 and e50:
        if e20 > e50 * 1.002:
            trend = "bull"
        elif e20 < e50 * 0.998:
            trend = "bear"
        else:
            trend = "range"
    elif e20 and last:
        # EMA50 unavailable (short lookback): fall back to EMA20 vs price
        if last > e20 * 1.002:
            trend = "bull"
        elif last < e20 * 0.998:
            trend = "bear"
        else:
            trend = "range"
    else:
        trend = "unknown"
    if snap["atr_pct"] is not None:
        vol = "high" if snap["atr_pct"] > 2.0 else ("low" if snap["atr_pct"] < 0.5 else "normal")
    else:
        vol = "unknown"
    snap["regime"] = {"trend": trend, "volatility": vol}
    return snap


def fallback_decision(market_meta, book, candles, cfg):
    """Deterministic heuristic used when AI is unavailable.

    Simple momentum+spread filter; intentionally conservative.
    """
    strat = cfg["strategy"]
    if market_meta.get("is_delisted"):
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": "market delisted"}
    if book["spread_bps"] > strat["max_spread_bps"]:
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": f"spread {book['spread_bps']:.2f}bps > max"}
    f = _candles_features(candles)
    if not f or f["n"] < strat["min_candle_count"]:
        return {"decision": "hold", "side": None, "confidence": 0.0,
                "reason": "insufficient candles"}
    threshold = float(strat.get("trend_threshold_pct", 0.5))
    if abs(f["change_pct"]) < threshold:
        return {"decision": "hold", "side": None, "confidence": 0.5,
                "reason": f"trend below threshold {threshold}%"}
    side = "buy" if f["change_pct"] > 0 else "sell"
    conf = min(0.9, strat["min_confidence"] + abs(f["change_pct"]) / 100)
    return {"decision": "open", "side": side, "confidence": round(conf, 3),
            "reason": f"momentum {f['change_pct']:.2f}% over {f['n']} candles"}


def validate_ai_decision(raw, market_meta, book, cfg):
    """Validate an AI-produced decision dict against the contract.

    Returns a normalized decision or raises DecisionError.
    """
    if not isinstance(raw, dict):
        raise DecisionError("AI output must be an object")
    d = raw.get("decision")
    if d not in VALID:
        raise DecisionError(f"invalid decision {d!r}; must be one of {VALID}")
    if d == "open":
        side = raw.get("side")
        if side not in ("buy", "sell"):
            raise DecisionError("open requires side buy|sell")
        conf = raw.get("confidence", 0)
        if not isinstance(conf, (int, float)) or not (0 <= conf <= 1):
            raise DecisionError("confidence must be number 0..1")
        if conf < cfg["strategy"]["min_confidence"]:
            return {"decision": "skip", "side": None, "confidence": conf,
                    "reason": "confidence below threshold"}
        if market_meta.get("is_delisted"):
            return {"decision": "skip", "side": None, "confidence": conf,
                    "reason": "market delisted"}
        if book["spread_bps"] > cfg["strategy"]["max_spread_bps"]:
            return {"decision": "skip", "side": None, "confidence": conf,
                    "reason": f"spread {book['spread_bps']:.2f}bps > max"}
        return {"decision": "open", "side": side, "confidence": float(conf),
                "reason": str(raw.get("reason", ""))[:200]}
    return {"decision": d, "side": None,
            "confidence": float(raw.get("confidence", 0) or 0),
            "reason": str(raw.get("reason", ""))[:200]}


def build_prompt_context(coin, market_meta, book, candles, account=None, news=None):
    """Build bounded market/account context; all external text remains data only."""
    f = _candles_features(candles) or {}
    rows = []
    for c in (candles or [])[-20:]:
        rows.append({k: c.get(k) for k in ("t", "T", "o", "h", "l", "c", "v") if k in c})
    positions = (account or {}).get("positions", [])
    ind = _indicators_snapshot(candles)
    return {
        "coin": coin,
        "max_leverage": market_meta.get("max_leverage"),
        "margin_mode": market_meta.get("margin_mode"),
        "bid": book["bid_px"], "ask": book["ask_px"],
        "spread_bps": book["spread_bps"],
        "order_book": {"bids": book.get("bids", [])[:5], "asks": book.get("asks", [])[:5]},
        "candles": {"count": f.get("n", 0), "change_pct": round(f.get("change_pct", 0), 3), "recent": rows},
        "indicators": ind or {},
        "account": {"free_collateral": (account or {}).get("free_collateral", 0), "positions": positions},
        "news": (news or [])[:5],
        "allowed_decisions": list(VALID),
        "note": "Return JSON only. You cannot choose amount, leverage, TP, SL, asset ID, or raw order payload.",
    }


if __name__ == "__main__":
    import sys
    from market_data import book_view, candles_snapshot
    from resolver import resolve_market

    coin = sys.argv[1] if len(sys.argv) > 1 else "io:SNDK"
    cfg = load_config()
    meta = resolve_market(coin)
    book = book_view(coin)
    candles = candles_snapshot(coin)
    dec = fallback_decision(meta, book, candles, cfg)
    print(json.dumps({"market": meta, "decision": dec}, indent=2))
