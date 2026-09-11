"""Closed-candle price-action gates for entry quality."""
import time


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def candle_ohlc(candle):
    """Return (open, high, low, close) or None if the candle is invalid."""
    if not isinstance(candle, dict):
        return None
    o = _num(candle.get("o", candle.get("open")))
    h = _num(candle.get("h", candle.get("high")))
    l = _num(candle.get("l", candle.get("low")))
    c = _num(candle.get("c", candle.get("close")))
    if o is None or h is None or l is None or c is None:
        return None
    if o <= 0 or h < l or c <= 0:
        return None
    return o, h, l, c


def closed_candles(candles, now=None):
    """Return valid closed candles; exclude only an explicitly future candle.

    Behavior chosen: Hyperliquid candleSnapshot CAN include the still-forming
    candle. We treat a candle as open only when its ``T`` (close timestamp, ms)
    is in the future. Candles without a ``T`` (deterministic tests, historical
    data) are treated as closed.
    """
    now_ms = (time.time() if now is None else now) * 1000
    out = []
    for candle in candles or []:
        if candle_ohlc(candle) is None:
            continue
        close_time = _num(candle.get("T"))
        if close_time is not None and close_time > now_ms:
            continue
        out.append(candle)
    return out


def _body_ratio(ohlc):
    o, h, l, c = ohlc
    return abs(c - o) / max(h - l, 1e-12)


def strong_bearish_reversal(candle, min_body_ratio=0.55):
    x = candle_ohlc(candle)
    if x is None:
        return False
    return x[3] < x[0] and _body_ratio(x) >= min_body_ratio


def strong_bullish_reversal(candle, min_body_ratio=0.55):
    x = candle_ohlc(candle)
    if x is None:
        return False
    return x[3] > x[0] and _body_ratio(x) >= min_body_ratio


def ema_extension_pct(close, ema):
    close, ema = _num(close), _num(ema)
    if close is None or ema is None or ema <= 0:
        return None
    return (close - ema) / ema * 100


def recent_support_resistance(candles, lookback=20):
    xs = [candle_ohlc(c) for c in (candles or [])[-lookback:]]
    xs = [x for x in xs if x is not None]
    return {
        "resistance": max(x[1] for x in xs) if xs else None,
        "support": min(x[2] for x in xs) if xs else None,
    }


def _rsi_values(rsi_series):
    if isinstance(rsi_series, (int, float)):
        return [float(rsi_series)]
    return [float(x) for x in (rsi_series or []) if x is not None]


def _confirmed_reclaim(candles, side):
    """True when a confirmation candle is followed by a later closed break.

    LONG  (side='buy'):  a bullish candle whose next candle CLOSES above its high.
    SHORT (side='sell'): a bearish candle whose next candle CLOSES below its low.
    """
    xs = [candle_ohlc(c) for c in (candles or [])]
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return False
    for i in range(1, len(xs) - 1):
        prev, cur, nxt = xs[i - 1], xs[i], xs[i + 1]
        # Confirmation must follow a pullback candle; this prevents the initial
        # pump candle from being misread as a post-pump reclaim.
        if side == "buy" and prev[3] < prev[0] and cur[3] > cur[0] and nxt[3] > cur[1]:
            return True
        if side == "sell" and prev[3] > prev[0] and cur[3] < cur[0] and nxt[3] < cur[2]:
            return True
    return False


def _pullback_bearish(xs):
    """Latest close lower, or recent lower low, or recent lower high."""
    if len(xs) < 2:
        return False
    if xs[-1][3] < xs[-2][3]:  # latest close < previous close
        return True
    if xs[-1][2] < xs[-2][2]:  # lower low
        return True
    return False


def _bounce_bullish(xs):
    if len(xs) < 2:
        return False
    if xs[-1][3] > xs[-2][3]:  # latest close > previous close
        return True
    if xs[-1][1] > xs[-2][1]:  # higher high
        return True
    return False


def post_overbought_long_block(candles, rsi_series, ema20=None, config=None):
    cfg = config or {}
    rsis = _rsi_values(rsi_series)
    lookback = int(cfg.get("post_extreme_lookback_candles", 5))
    if not any(r >= float(cfg.get("rsi_overbought", 75)) for r in rsis[-lookback:]):
        return None
    xs = [candle_ohlc(c) for c in (candles or [])]
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    if _pullback_bearish(xs) and not _confirmed_reclaim(candles, "buy"):
        return "post-overbought cooldown; wait for support hold and bullish reclaim"
    return None


def post_oversold_short_block(candles, rsi_series, ema20=None, config=None):
    cfg = config or {}
    rsis = _rsi_values(rsi_series)
    lookback = int(cfg.get("post_extreme_lookback_candles", 5))
    if not any(r <= float(cfg.get("rsi_oversold", 25)) for r in rsis[-lookback:]):
        return None
    xs = [candle_ohlc(c) for c in (candles or [])]
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    if _bounce_bullish(xs) and not _confirmed_reclaim(candles, "sell"):
        return "post-oversold cooldown; wait for resistance hold and bearish breakdown"
    return None


def late_exhaustion_block(side, candles, rsi_series, cfg=None):
    """Block late entries after an extended directional move unless a retest confirms.

    Applied only when a large move AND a stretched RSI combine. Not an absolute
    directional block: a healthy continuation remains allowed after a valid
    retest/reclaim. Defaults are backward-compatible via ``cfg.get()``.
    """
    cfg = cfg or {}
    xs = [candle_ohlc(c) for c in (candles or [])]
    xs = [x for x in xs if x is not None]
    if len(xs) < 2:
        return None
    rsis = _rsi_values(rsi_series)
    if not rsis:
        return None
    rsi = rsis[-1]
    lookback = cfg.get("late_move_lookback_candles")
    window = xs[-int(lookback):] if lookback else xs
    if len(window) < 2:
        window = xs
    first_close, last_close = window[0][3], window[-1][3]
    if first_close <= 0:
        return None
    momentum = (last_close - first_close) / first_close * 100.0
    threshold = float(cfg.get("late_move_threshold_pct", 8.0))
    if side == "buy":
        if momentum >= threshold and rsi >= float(cfg.get("late_long_rsi_ceiling", 65.0)):
            if not _confirmed_reclaim(candles, "buy"):
                return "late long blocked: extended move; wait for breakout retest confirmation"
    elif side == "sell":
        if momentum <= -threshold and rsi <= float(cfg.get("late_short_rsi_floor", 35.0)):
            if not _confirmed_reclaim(candles, "sell"):
                return "late short blocked: extended dump; wait for breakdown retest confirmation"
    return None


def entry_quality_gate(side, candles, indicators, book=None, cfg=None):
    """Return a short reject reason, or None if the entry passes.

    Read-only: never places orders, never mutates account/config state.
    indicators may carry ``ema20`` named keys or ``rsi14`` fallback.
    """
    cfg = cfg or {}
    xs = closed_candles(candles)
    if len(xs) < 2:
        return "entry blocked: insufficient valid closed candles"
    ind = indicators or {}
    close = candle_ohlc(xs[-1])[3]
    ema20 = ind.get("ema20")
    extension = ema_extension_pct(close, ema20)
    max_ext = float(cfg.get("max_ema_extension_pct", 0.8))
    body = float(cfg.get("reversal_min_body_ratio", 0.55))
    rsi_series = ind.get("rsi_series", ind.get("rsi14"))

    # EMA distance is an anti-chase guard for impulsive moves. A smooth trend
    # can sit >0.8% above EMA20 without being late; require recent range/body
    # expansion so this does not reject healthy gradual trends.
    prev_x = candle_ohlc(xs[-2])
    last_x = candle_ohlc(xs[-1])
    recent_impulse = bool(prev_x and last_x and
                          (last_x[1] - last_x[2]) >= (prev_x[1] - prev_x[2]) * 1.8)
    if side == "buy":
        reason = late_exhaustion_block(side, xs, rsi_series, cfg)
        if reason:
            return reason
        reason = post_overbought_long_block(xs, rsi_series, ema20, cfg)
        if reason:
            return reason
        if extension is not None and extension > max_ext and recent_impulse:
            return f"long blocked: price extended {extension:.2f}% above EMA20; avoid chasing"
        if strong_bearish_reversal(xs[-1], body):
            return "long blocked: strong bearish reversal candle"
    elif side == "sell":
        reason = late_exhaustion_block(side, xs, rsi_series, cfg)
        if reason:
            return reason
        reason = post_oversold_short_block(xs, rsi_series, ema20, cfg)
        if reason:
            return reason
        if extension is not None and extension < -max_ext and recent_impulse:
            return f"short blocked: price extended {abs(extension):.2f}% below EMA20; avoid chasing"
        if strong_bullish_reversal(xs[-1], body):
            return "short blocked: strong bullish reversal candle"
    else:
        return "entry blocked: invalid side"

    sr = recent_support_resistance(xs[:-1], int(cfg.get("sr_lookback_candles", 20)))
    room = float(cfg.get("sr_min_room_pct", 0.35))
    # Exclude the last candle: its own high/low is the current breakout level, not
    # resistance/support. Prior candles' extremes are the real S/R levels.
    if side == "buy" and sr["resistance"] is not None and sr["resistance"] > close:
        if (sr["resistance"] - close) / close * 100 < room:
            return "long blocked: nearby resistance leaves insufficient TP room"
    if side == "sell" and sr["support"] is not None and sr["support"] < close:
        if (close - sr["support"]) / close * 100 < room:
            return "short blocked: nearby support leaves insufficient TP room"
    return None