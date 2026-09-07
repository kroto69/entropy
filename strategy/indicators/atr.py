"""Average True Range (ATR, Wilder). Pure function, stdlib only."""


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _series(source, *keys):
    out = []
    for c in source:
        val = None
        if isinstance(c, dict):
            for k in keys:
                if k in c:
                    val = c[k]
                    break
        else:
            val = c
        out.append(_float(val))
    return out


def calculate_atr(candles, period=14):
    """ATR aligned with input; leading None while seeding.

    Accepts candle dicts (keys high/h, low/l, close/c). Volatility measure.
    """
    highs = _series(candles, "high", "h")
    lows = _series(candles, "low", "l")
    closes = _series(candles, "close", "c")
    n = min(len(highs), len(lows), len(closes))
    out: list = [None] * n
    if period <= 0 or n < period + 1:
        return out
    trs = []
    for i in range(n):
        if highs[i] is None or lows[i] is None or closes[i] is None:
            continue
        # TR needs previous close; first candle has no prior close
        if i > 0 and closes[i - 1] is None:
            continue
        prev_close = closes[i - 1] if i > 0 else closes[i]
        tr = max(highs[i] - lows[i],
                 abs(highs[i] - prev_close),
                 abs(lows[i] - prev_close))
        trs.append(tr)
    if len(trs) < period:
        return out
    atr_vals = [None] * len(trs)
    atr_vals[period - 1] = sum(trs[:period]) / period
    for i in range(period, len(trs)):
        atr_vals[i] = (atr_vals[i - 1] * (period - 1) + trs[i]) / period
    # map back onto candle indices where a TR was defined
    idx = 0
    prev_close = None
    for i in range(n):
        if highs[i] is None or lows[i] is None or closes[i] is None:
            continue
        if i > 0 and closes[i - 1] is None:
            continue
        prev_close = closes[i - 1] if i > 0 else closes[i]
        out[i] = atr_vals[idx]
        idx += 1
    return out