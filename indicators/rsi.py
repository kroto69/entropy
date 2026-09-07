"""Relative Strength Index (RSI, Wilder). Pure function, stdlib only."""


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


def _rsi(avg_gain, avg_loss):
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def calculate_rsi(candles, period=14):
    """RSI aligned with input; leading None while seeding.

    Accepts candle dicts (keys close/c) or plain numbers.
    """
    closes = _series(candles, "close", "c")
    n = len(closes)
    out: list = [None] * n
    valid = [v for v in closes if v is not None]
    if period <= 0 or len(valid) < period + 1:
        return out
    gains = []
    losses = []
    for i in range(1, len(valid)):
        d = valid[i] - valid[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi_vals = [None] * len(valid)
    rsi_vals[period] = _rsi(avg_gain, avg_loss)
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi_vals[i + 1] = _rsi(avg_gain, avg_loss)
    # map computed RSI back to original indices (skip None gaps)
    idx = 0
    for i in range(n):
        if closes[i] is not None:
            out[i] = rsi_vals[idx]
            idx += 1
    return out