"""Pure OHLC-only signal strategy.

Uses closed OHLC candles exclusively (volume optional). No EMA/RSI/ATR, no
account/balance, no exchange/AI, no future candles. Safety vetoes run later in
the router via entry_quality_gate().

Two modes: "default" (conservative) and "aggressive" (looser entries).
The mode only loosens signal thresholds; entry_quality_gate always still runs.
"""
from strategy.strategies.base import hold, skip, open_signal
from strategy.indicators.price_action import (candle_ohlc, closed_candles,
                                              _confirmed_reclaim)


DEFAULT_PARAMS = {
    "lookback_candles": 20,
    "trend_threshold_pct": 0.5,
    "min_consecutive_candles": 3,
    "require_reclaim": True,
    "confidence_base": 0.60,
    "confidence_cap": 0.85,
}

AGGRESSIVE_PARAMS = {
    "lookback_candles": 10,
    "trend_threshold_pct": 0.25,
    "min_consecutive_candles": 2,
    "require_reclaim": False,
    "confidence_base": 0.55,
    "confidence_cap": 0.80,
}

PARAM_SETS = {
    "default": DEFAULT_PARAMS,
    "aggressive": AGGRESSIVE_PARAMS,
}

# Backward-compatible alias: older code imported DEFAULTS.
DEFAULTS = DEFAULT_PARAMS


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _valid_ohlc(candle):
    x = candle_ohlc(candle)
    if x is None:
        return None
    o, h, l, c = x
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        return None
    return x


def _consecutive_higher_closes(xs, n):
    closes = [x[3] for x in xs]
    if len(closes) < n:
        return 0
    run = 0
    best = 0
    for i in range(1, len(closes)):
        run = run + 1 if closes[i] > closes[i - 1] else 0
        best = max(best, run)
    return best


def _consecutive_lower_closes(xs, n):
    closes = [x[3] for x in xs]
    if len(closes) < n:
        return 0
    run = 0
    best = 0
    for i in range(1, len(closes)):
        run = run + 1 if closes[i] < closes[i - 1] else 0
        best = max(best, run)
    return best


def _reclaim(xs, side):
    """Reuse shared reclaim semantics while passing normalized OHLC tuples."""
    rows = [{"o": x[0], "h": x[1], "l": x[2], "c": x[3]} for x in xs]
    return _confirmed_reclaim(rows, side)


def resolve_params(params=None):
    """Return the effective param set for a given strategy_params dict."""
    if not isinstance(params, dict):
        return dict(DEFAULT_PARAMS)
    mode = params.get("mode", "default")
    base = dict(PARAM_SETS.get(mode, DEFAULT_PARAMS))
    # Allow numeric overrides of the known signal knobs (not mode).
    for k in ("lookback_candles", "trend_threshold_pct",
              "min_consecutive_candles", "confidence_base", "confidence_cap"):
        v = params.get(k)
        if isinstance(v, (int, float)):
            base[k] = v
    if "require_reclaim" in params:
        base["require_reclaim"] = bool(params["require_reclaim"])
    return base


def check_5m_timing_filter(candles, side, mode="default"):
    """Validate 5m-timeframe confirmation for an existing OHLC open signal.

    Returns ``(passed, reason)``. Missing/invalid candles fail closed.

    default    : >=3 of last 5 candles in direction AND latest close beyond
                 prior close (strong confirmation).
    aggressive : >=2 of last 3 candles in direction AND latest candle direction
                 match only (close > open for buy, < open for sell).
    """
    if side not in ("buy", "sell") or not isinstance(candles, (list, tuple)):
        return False, "5m timing filter failed: invalid input"

    aggressive = mode == "aggressive"
    span = 3 if aggressive else 5
    need = 2 if aggressive else 3

    xs = [x for x in (_valid_ohlc(c) for c in closed_candles(candles)) if x is not None]
    if len(xs) < span:
        return False, "5m timing filter failed: insufficient closed candles"

    window = xs[-span:]
    bullish = sum(x[3] > x[0] for x in window)
    bearish = sum(x[3] < x[0] for x in window)
    latest_open = window[-1][0]
    latest_close = window[-1][3]

    if side == "buy":
        if bullish < need:
            return False, f"5m timing filter failed: bullish candles {bullish}/{span}"
        if aggressive:
            if latest_close <= latest_open:
                return False, "5m timing filter failed: latest candle not bullish"
        else:
            prior_close = window[-2][3]
            if latest_close <= prior_close:
                return False, "5m timing filter failed: latest close not above prior close"
        return True, "5m timing filter passed"

    if bearish < need:
        return False, f"5m timing filter failed: bearish candles {bearish}/{span}"
    if aggressive:
        if latest_close >= latest_open:
            return False, "5m timing filter failed: latest candle not bearish"
    else:
        prior_close = window[-2][3]
        if latest_close >= prior_close:
            return False, "5m timing filter failed: latest close not below prior close"
    return True, "5m timing filter passed"


def decide(candles, params=None, indicators=None):
    """Produce a normalized OHLC signal.

    candles: iterable of candle dicts (raw snapshot may include active candle).
    params: optional dict; may carry ``mode`` ("default"|"aggressive") plus
            numeric overrides. Falls back to DEFAULT_PARAMS.
    indicators: accepted for interface symmetry; ignored.
    """
    p = resolve_params(params)
    mode = (params or {}).get("mode", "default") if isinstance(params, dict) else "default"
    aggressive = mode == "aggressive"

    lookback = int(p["lookback_candles"])
    threshold = float(p["trend_threshold_pct"])
    min_consec = int(p["min_consecutive_candles"])
    require_reclaim = bool(p["require_reclaim"])

    xs = [_valid_ohlc(c) for c in closed_candles(candles)]
    xs = [x for x in xs if x is not None]
    if len(xs) < lookback:
        return hold(f"OHLC insufficient closed candles ({len(xs)}<{lookback})")

    window = xs[-lookback:]
    first_close = window[0][3]
    last_close = window[-1][3]
    if first_close <= 0:
        return hold("OHLC invalid first close")

    change_pct = (last_close - first_close) / first_close * 100.0

    if change_pct >= threshold:
        side = "buy"
    elif change_pct <= -threshold:
        side = "sell"
    else:
        return hold(f"OHLC flat momentum {change_pct:.2f}%")

    if side == "buy":
        consec = _consecutive_higher_closes(window, min_consec)
        reclaim_present = _reclaim(xs, "buy")
    else:
        consec = _consecutive_lower_closes(window, min_consec)
        reclaim_present = _reclaim(xs, "sell")

    reclaim = require_reclaim and reclaim_present

    # Consecutive-close confirmation is always required.
    if consec < min_consec:
        kind = "higher" if side == "buy" else "lower"
        return hold(f"OHLC {'bullish' if side == 'buy' else 'bearish'} momentum "
                    f"but no {kind}-close confirmation", confidence=0.45)

    # Reclaim: mandatory in default mode, optional (confidence boost) in aggressive.
    if require_reclaim and not reclaim_present:
        return hold(f"OHLC momentum is {'bullish' if side == 'buy' else 'bearish'} "
                    "but pullback reclaim is not confirmed", confidence=0.45)

    # Confidence
    if aggressive:
        conf = float(p["confidence_base"])  # 0.55
        if abs(change_pct) >= 0.4:
            conf += 0.10
        if consec >= min_consec:
            conf += 0.08
        if reclaim_present:
            conf += 0.07
        conf = min(float(p["confidence_cap"]), conf)  # 0.80
    else:
        conf = float(p["confidence_base"])  # 0.60
        conf += min(0.10, abs(change_pct) / 10.0)
        if consec >= min_consec:
            conf += 0.08
        if reclaim_present:
            conf += 0.08
        conf = min(float(p["confidence_cap"]), conf)  # 0.85

    if side == "buy":
        note = "reclaim confirmed" if reclaim_present else "no reclaim"
        return open_signal("buy", conf,
                           f"OHLC bull trend +{change_pct:.2f}%, {note}")
    note = "reclaim confirmed" if reclaim_present else "no reclaim"
    return open_signal("sell", conf,
                       f"OHLC bear trend {change_pct:.2f}%, {note}")