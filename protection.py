"""Deterministic protective TP/SL and time-exit planner. No network."""
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from planner import _round_down


def prices(entry, side, tp_pct, sl_pct, price_decimals=2):
    e = Decimal(str(entry)); tp = Decimal(str(tp_pct))/100; sl = Decimal(str(sl_pct))/100
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
