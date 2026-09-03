"""Deterministic protective TP/SL and time-exit planner. No network."""
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from planner import _round_down


def prices(entry, side, tp_pct, sl_pct, price_decimals=2, leverage=1):
    """TP/SL levels from ROE percentages (return on margin).

    tp_pct/sl_pct are % of MARGIN (ROE), not % of price.
    Price move = ROE / leverage. E.g. ROE 100% at 5x = price moves 20%.
    Guard: SL is capped at 60% of the liquidation distance (1/leverage),
    so SL always triggers before liquidation.
    """
    e = Decimal(str(entry)); lev = max(1, int(leverage))
    tp = Decimal(str(tp_pct)) / 100 / lev
    sl = Decimal(str(sl_pct)) / 100 / lev
    # liquidation approx distance = 1/leverage; SL must stay well inside it
    max_sl = Decimal("0.60") / lev
    if sl > max_sl:
        sl = max_sl
    if side == "buy":
        take = e * (1 + tp); stop = e * (1 - sl)
    elif side == "sell":
        take = e * (1 - tp); stop = e * (1 + sl)
        if take <= 0:
            take = e * Decimal("0.01")  # ponytail: TP 100% short = price 0 impossible; cap at 99%
    else: raise ValueError("side must be buy|sell")
    q=Decimal(1).scaleb(-price_decimals)
    return {"take_profit": str(take.quantize(q, rounding=ROUND_DOWN)), "stop_loss": str(stop.quantize(q, rounding=ROUND_UP))}


def close_intent(coin, side, size, price=None):
    if side not in ("buy", "sell"): raise ValueError("side must be buy|sell")
    return {"coin": coin, "side": "sell" if side == "buy" else "buy", "size": str(size),
            "price": None if price is None else str(price), "reduce_only": True, "tif": "Ioc"}
