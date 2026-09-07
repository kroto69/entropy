"""Exponential Moving Average (EMA). Pure function, stdlib only, no side effects."""


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _series(source, *keys):
    """Extract numeric series from candle dicts (keys like close/c, high/h)."""
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


def calculate_ema(candles, period=20):
    """EMA aligned with input; leading None while seeding.

    Accepts a list of candle dicts (keys close/c) or plain numbers.
    None / invalid values are skipped without breaking alignment.
    """
    closes = _series(candles, "close", "c")
    n = len(closes)
    out: list = [None] * n
    if period <= 0:
        return out
    k = 2.0 / (period + 1.0)
    prev = None
    for i, v in enumerate(closes):
        if v is None:
            continue
        if prev is None:
            vals = [x for x in closes[:i + 1] if x is not None]
            if len(vals) < period:
                continue
            prev = sum(vals[-period:]) / period
            out[i] = prev
        else:
            prev = v * k + prev * (1 - k)
            out[i] = prev
    return out