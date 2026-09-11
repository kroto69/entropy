"""Phase 3: decision engine.

AI (or fallback heuristic) outputs only: open|close|hold|skip + side + confidence.
Amount, TP, SL, leverage, asset ID never come from AI; they come from config.
This module produces decisions only; it cannot place orders.
"""
import json
from pathlib import Path

try:
    from strategy.indicators import calculate_ema, calculate_rsi, calculate_atr
    from strategy.indicators.price_action import entry_quality_gate, candle_ohlc, closed_candles
except ModuleNotFoundError:  # direct ``python3 strategy/decision.py`` compatibility
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from strategy.indicators import calculate_ema, calculate_rsi, calculate_atr
    from strategy.indicators.price_action import entry_quality_gate, candle_ohlc, closed_candles
# closed_candles is enforced inside entry_quality_gate.

QUALITY_DEFAULTS = {
    "post_extreme_lookback_candles": 5,
    "rsi_overbought": 75,
    "rsi_oversold": 25,
    "max_ema_extension_pct": 0.8,
    "reversal_min_body_ratio": 0.55,
    "sr_lookback_candles": 20,
    "sr_min_room_pct": 0.35,
    # late-exhaustion gate (None window = whole available closed-candle window)
    "late_move_lookback_candles": None,
    "late_move_threshold_pct": 8.0,
    "late_long_rsi_ceiling": 65.0,
    "late_short_rsi_floor": 35.0,
}

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


def _rsi_series_from_closes(closes):
    return calculate_rsi(closes, 14)


def _gate_open(side, candles, book, cfg, indicators):
    """Run entry_quality_gate on an open signal; return reject reason or None."""
    strat = cfg.get("strategy")
    strat_params = strat if isinstance(strat, dict) else {}
    gate_cfg = {**QUALITY_DEFAULTS, **(strat_params or {})}
    return entry_quality_gate(side, candles, indicators, book, gate_cfg)


def _legacy_fallback(market_meta, book, candles, cfg):
    """Original object-style strategy (trend + momentum + indicators)."""
    strat = cfg["strategy"]

    # Safety filters
    if market_meta.get("is_delisted"):
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": "market delisted"}
    if book["spread_bps"] > strat["max_spread_bps"]:
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": f"spread {book['spread_bps']:.2f}bps > max"}

    closes = [float(c.get("c") or c.get("close") or 0) for c in candles]
    closes = [c for c in closes if c > 0]

    if len(candles) < strat["min_candle_count"]:
        return {"decision": "hold", "side": None, "confidence": 0.0,
                "reason": "insufficient candles"}

    # Calculate indicators (indicators return aligned lists -> take latest with [-1])
    ema20 = calculate_ema(closes, 20)[-1] if len(closes) >= 20 else None
    ema50 = calculate_ema(closes, 50)[-1] if len(closes) >= 50 else None
    rsi14 = calculate_rsi(closes, 14)[-1] if len(closes) >= 15 else None
    atr14 = calculate_atr(candles, 14)[-1] if len(candles) >= 15 else None

    # Momentum
    f = _candles_features(candles) or {}
    change_pct = f.get("change_pct", 0)

    # Trend detection (price above EMA20 = bull, not the inverse)
    trend = "neutral"
    if ema20 is not None and ema50 is not None:
        trend = "bull" if ema20 > ema50 else "bear"
    elif ema20 is not None and closes:
        trend = "bull" if closes[-1] > ema20 else "bear"

    threshold = float(strat.get("trend_threshold_pct", 0.5))
    min_conf = float(strat.get("min_confidence", 0.65))

    # RSI filter
    rsi_extreme = False
    if rsi14 is not None:
        rsi_extreme = (rsi14 > 75 or rsi14 < 25)

    # Volatility filter
    low_vol = False
    if atr14 is not None and closes:
        atr_pct = (atr14 / closes[-1]) * 100
        low_vol = (atr_pct < 0.1)  # too quiet, skip (adjusted for io:* 15m)

    if low_vol:
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": "volatility too low"}

    if rsi_extreme:
        return {"decision": "hold", "side": None, "confidence": 0.5,
                "reason": f"RSI extreme ({rsi14:.1f})"}

    if abs(change_pct) < threshold:
        return {"decision": "hold", "side": None, "confidence": 0.5,
                "reason": f"trend below threshold {threshold}%"}

    # Align trend + momentum
    side = "buy" if change_pct > 0 else "sell"
    if trend == "bear" and change_pct > 0:
        return {"decision": "hold", "side": None, "confidence": 0.4,
                "reason": "momentum vs trend conflict"}
    if trend == "bull" and change_pct < 0:
        return {"decision": "hold", "side": None, "confidence": 0.4,
                "reason": "momentum vs trend conflict"}

    # Entry-quality gate: block chasing / post-pump entries before committing.
    # Reuses the same rsi14 arithmetic already computed above.
    quality_reject = _gate_open(
        side, candles, book, cfg,
        {"ema20": ema20, "rsi_series": calculate_rsi(closes, 14)})
    if quality_reject:
        return {"decision": "hold", "side": None, "confidence": 0.4,
                "reason": quality_reject}

    # Confidence based on alignment
    base_conf = min_conf
    if trend != "neutral":
        base_conf += 0.1
    if rsi14 is not None and 30 <= rsi14 <= 70:
        base_conf += 0.05

    conf = min(0.9, base_conf + abs(change_pct) / 100)
    rsi_str = f"{rsi14:.1f}" if rsi14 is not None else "N/A"

    return {"decision": "open", "side": side, "confidence": round(conf, 3),
            "reason": f"{trend} trend + momentum {change_pct:.2f}%, RSI {rsi_str}"}


def fallback_decision(market_meta, book, candles, cfg, timing_candles=None):
    """Route by root config ``strategy`` field.

    - string "ohlc" -> modular OHLC strategy
    - dict -> legacy object-style strategy
    - missing -> legacy behavior
    - unknown string -> skip (fail closed)
    """
    strat = cfg.get("strategy")
    if isinstance(strat, str):
        if strat == "ohlc":
            return _ohlc_decision(market_meta, book, candles, cfg, timing_candles)
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": f"unknown strategy: {strat}"}
    # dict (legacy) or missing -> original fallback path
    if not isinstance(strat, dict):
        cfg = dict(cfg)
        cfg["strategy"] = {
            "trend_threshold_pct": 0.5, "min_confidence": 0.65,
            "max_spread_bps": 25.0, "min_candle_count": 20,
        }
    return _legacy_fallback(market_meta, book, candles, cfg)


def _ohlc_decision(market_meta, book, candles, cfg, timing_candles=None):
    """OHLC signal + mandatory deterministic safety gates."""
    if market_meta.get("is_delisted"):
        return {"decision": "skip", "side": None, "confidence": 0.0,
                "reason": "market delisted"}

    from strategy.strategies.ohlc import decide as ohlc_decide
    from strategy.strategies.ohlc import check_5m_timing_filter

    params = cfg.get("strategy_params") or {}
    sig = ohlc_decide(candles, params)
    mode = params.get("mode", "default") if isinstance(params, dict) else "default"

    if sig["decision"] != "open":
        return sig

    # 5m timing filter: only applies after a 15m/30m OHLC open signal.
    if timing_candles is not None:
        passed, treason = check_5m_timing_filter(timing_candles, sig["side"], mode=mode)
        if not passed:
            return {"decision": "hold", "side": None,
                    "confidence": min(float(sig["confidence"]), 0.45),
                    "reason": treason}

    # Compute safety indicators (EMA/RSI) for the gate only — never the signal.
    from strategy.indicators.price_action import closed_candles as _cc
    closed = _cc(candles)
    valid_ohlc = [candle_ohlc(c) for c in closed]
    valid_ohlc = [x for x in valid_ohlc if x is not None]
    closes = [x[3] for x in valid_ohlc]
    ema20 = calculate_ema(closes, 20)[-1] if len(closes) >= 20 else None
    indicators = {"ema20": ema20, "rsi_series": calculate_rsi(closes, 14)}

    quality_reject = _gate_open(sig["side"], candles, book, cfg, indicators)
    if quality_reject:
        return {"decision": "hold", "side": None,
                "confidence": min(float(sig["confidence"]), 0.45),
                "reason": quality_reject}

    return sig


def validate_ai_decision(raw, market_meta, book, cfg, candles=None, indicators=None):
    """Validate an AI-produced decision dict against the contract.

    Returns a normalized decision or raises DecisionError.
    candles/indicators enable the deterministic entry-quality veto on AI opens.
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
        strat_cfg = cfg.get("strategy") if isinstance(cfg.get("strategy"), dict) else {}
        ai_cfg = cfg.get("strategy_params") if isinstance(cfg.get("strategy_params"), dict) else {}
        min_confidence = float(strat_cfg.get("min_confidence", ai_cfg.get("min_confidence", cfg.get("ai_min_confidence", 0.65))))
        max_spread_bps = float(strat_cfg.get("max_spread_bps", ai_cfg.get("max_spread_bps", 25.0)))
        if conf < min_confidence:
            return {"decision": "skip", "side": None, "confidence": conf,
                    "reason": "confidence below threshold"}
        if market_meta.get("is_delisted"):
            return {"decision": "skip", "side": None, "confidence": conf,
                    "reason": "market delisted"}
        if book["spread_bps"] > max_spread_bps:
            return {"decision": "skip", "side": None, "confidence": conf,
                    "reason": f"spread {book['spread_bps']:.2f}bps > max"}
        # Deterministic veto: block chasing / post-pump entries even if AI said open.
        if candles:
            gate_cfg = dict(QUALITY_DEFAULTS)
            if isinstance(cfg.get("strategy"), dict):
                gate_cfg.update(cfg.get("strategy") or {})
            quality_reject = entry_quality_gate(
                side, candles, indicators or {}, book, gate_cfg)
            if quality_reject:
                return {"decision": "hold", "side": None,
                        "confidence": min(float(conf), 0.45),
                        "reason": quality_reject}
        return {"decision": "open", "side": side, "confidence": float(conf),
                "reason": str(raw.get("reason", ""))[:200]}
    return {"decision": d, "side": None,
            "confidence": float(raw.get("confidence", 0) or 0),
            "reason": str(raw.get("reason", ""))[:200]}


def build_ai_market_snapshot(coin, market_meta, book, candles, timing_candles=None,
                             account=None, cfg=None, timestamp=None):
    """Pure bounded snapshot for AI-primary. Inputs are filtered to closed candles."""
    from datetime import datetime, timezone
    from strategy.indicators import calculate_ema, calculate_rsi, calculate_atr
    cfg = cfg or {}
    closed = closed_candles(candles)
    closed5 = closed_candles(timing_candles or [])
    def row(c):
        x = candle_ohlc(c)
        return {"o": x[0], "h": x[1], "l": x[2], "c": x[3]} if x else None
    def rows(xs): return [row(c) for c in xs[-20:] if row(c)]
    def features(xs):
        rs = [candle_ohlc(c) for c in xs if candle_ohlc(c)]
        closes = [x[3] for x in rs]
        if len(closes) < 2: return {"count": len(rs), "valid": False}
        ema20s = calculate_ema(closes, 20); ema50s = calculate_ema(closes, 50)
        rsis = calculate_rsi(closes, 14); atrs = calculate_atr(rs, 14)
        last = closes[-1]
        e20, e50, rsi, atr = ema20s[-1], ema50s[-1], rsis[-1], atrs[-1]
        hh = len(rs) >= 3 and rs[-1][1] > rs[-2][1] > rs[-3][1]
        hl = len(rs) >= 3 and rs[-1][2] > rs[-2][2] > rs[-3][2]
        lh = len(rs) >= 3 and rs[-1][1] < rs[-2][1] < rs[-3][1]
        ll = len(rs) >= 3 and rs[-1][2] < rs[-2][2] < rs[-3][2]
        up = sum(closes[i] > closes[i-1] for i in range(max(1,len(closes)-5),len(closes)))
        down = sum(closes[i] < closes[i-1] for i in range(max(1,len(closes)-5),len(closes)))
        return {"count": len(rs), "valid": len(rs) >= 2, "recent": rows(xs),
                "trend_structure": "bullish" if hh and hl else "bearish" if lh and ll else "range" if up < 3 and down < 3 else "unclear",
                "higher_highs": hh, "higher_lows": hl, "lower_highs": lh, "lower_lows": ll,
                "momentum_5": round((last-closes[-6])/closes[-6]*100, 4) if len(closes)>=6 else None,
                "consecutive_higher_closes": _run(closes, 1), "consecutive_lower_closes": _run(closes, -1),
                "ema20": e20, "ema50": e50,
                "ema_gap_pct": (e20-e50)/e50*100 if e20 and e50 else None,
                "rsi14": rsi, "atr": atr, "atr_pct": atr/last*100 if atr and last else None}
    def five_features(xs):
        f = features(xs); rs = [candle_ohlc(c) for c in xs if candle_ohlc(c)]
        last = rs[-1] if rs else None; prev = rs[-2] if len(rs)>1 else None
        def count(n, bullish): return sum((x[3]>x[0]) if bullish else (x[3]<x[0]) for x in rs[-n:])
        body = abs(last[3]-last[0])/max(last[1]-last[2], 1e-12) if last else None
        return {"count": f.get("count",0), "valid": f.get("valid",False), "recent": rows(xs),
                "structure": "impulse" if f.get("momentum_5") is not None and abs(f["momentum_5"]) >= 0.5 else "range",
                "bull_count_last_3": count(3,True), "bear_count_last_3": count(3,False),
                "bull_count_last_5": count(5,True), "bear_count_last_5": count(5,False),
                "latest_close_vs_previous": last[3]-prev[3] if last and prev else None,
                "latest_body_to_range": body,
                "momentum_5": f.get("momentum_5")}
    bids = book.get("bids") or []; asks = book.get("asks") or []
    bid_depth = sum(float(x.get("sz", x.get("size", 0))) for x in bids[:5])
    ask_depth = sum(float(x.get("sz", x.get("size", 0))) for x in asks[:5])
    positions = (account or {}).get("positions", [])
    poscfg = cfg.get("position", {})
    return {"market": {"coin": coin, "timestamp_utc": timestamp or datetime.now(timezone.utc).isoformat(),
        "delisted": bool(market_meta.get("is_delisted")), "bid": book.get("bid_px"), "ask": book.get("ask_px"),
        "spread_bps": book.get("spread_bps"), "book": {"bid_depth": bid_depth, "ask_depth": ask_depth,
        "imbalance": (bid_depth-ask_depth)/(bid_depth+ask_depth) if bid_depth+ask_depth else None,
        "depth_insufficient": not bids or not asks}},
        "account": {"free_collateral": (account or {}).get("free_collateral",0), "open_positions": len(positions),
        "max_positions": poscfg.get("max_positions"), "same_coin_position": any(p.get("coin")==coin for p in positions)},
        "closed_15m": features(closed), "closed_5m": five_features(closed5),
        "execution_context": {k: poscfg.get(k) for k in ("amount_usdc","leverage","take_profit_pct","stop_loss_pct","max_hold_minutes")},
        "data_valid": bool(len(closed) >= 2 and len(closed5) >= 3
                            and book.get("bid_px") is not None
                            and book.get("ask_px") is not None)}


def _run(values, direction):
    run = 0
    for i in range(len(values)-1, 0, -1):
        if (direction == 1 and values[i] > values[i-1]) or (direction == -1 and values[i] < values[i-1]): run += 1
        else: break
    return run


def build_prompt_context(coin, market_meta, book, candles, account=None, news=None, snapshot=None):
    """Build bounded market/account context; all external text remains data only."""
    if snapshot is not None:
        return snapshot
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
    from core.market_data import book_view, candles_snapshot
    from core.resolver import resolve_market

    coin = sys.argv[1] if len(sys.argv) > 1 else "io:SNDK"
    cfg = load_config()
    meta = resolve_market(coin)
    book = book_view(coin)
    candles = candles_snapshot(coin)
    dec = fallback_decision(meta, book, candles, cfg)
    print(json.dumps({"market": meta, "decision": dec}, indent=2))
