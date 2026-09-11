"""Self-check for fallback_decision heuristics. Run: python3 test_fallback.py"""


def _c(close, high=None, low=None, o=None):
    return {"o": o if o is not None else close, "c": close,
            "h": high if high is not None else close,
            "l": low if low is not None else close}


CFG = {
    "strategy": {"trend_threshold_pct": 0.5, "min_confidence": 0.65,
                 "max_spread_bps": 25.0, "min_candle_count": 20,
                 "max_ema_extension_pct": 2.0},
    "position": {},
    "runtime": {},
}


def book(spread=10.0):
    return {"bid_px": 100.0, "ask_px": 100.0 + spread / 10000 * 100.0, "spread_bps": spread,
            "bids": [], "asks": []}


def meta(delisted=False):
    return {"is_delisted": delisted}


def main():
    from strategy.decision import fallback_decision

    # 1. Gentle bull: rising with pullbacks keeps RSI below 75, momentum up -> open buy
    raw = [100.0]
    for i in range(39):
        step = 0.5 if i % 3 != 2 else -0.45  # 2 up, 1 down = net rise, balanced RSI
        raw.append(raw[-1] + step)
    up = [_c(v) for v in raw]
    d = fallback_decision(meta(), book(), up, CFG)
    assert d["decision"] == "open" and d["side"] == "buy", d
    assert d["confidence"] >= 0.65, d

    # 2. Gentle bear: falling with pullbacks keeps RSI above 25, momentum down -> open sell
    raw = [130.0]
    for i in range(39):
        step = -0.5 if i % 3 != 2 else 0.45  # 2 down, 1 up = net fall
        raw.append(raw[-1] + step)
    dn = [_c(v) for v in raw]
    d = fallback_decision(meta(), book(), dn, CFG)
    assert d["decision"] == "open" and d["side"] == "sell", d

    # 3. Flat: below momentum threshold -> hold (ATR also low but hold fires first? no:
    #    low_vol -> skip). Flat data: ATR tiny -> expect skip "volatility too low"
    flat = [_c(100.0 + 0.01 * ((-1) ** i)) for i in range(40)]
    d = fallback_decision(meta(), book(), flat, CFG)
    assert d["decision"] in ("hold", "skip"), d
    print("case3 flat:", d["decision"], "-", d["reason"])

    # 4. Wide spread -> skip (fail-closed, fires before anything else)
    up = [_c(100.0 + i * 0.4 + (0.3 if i % 2 else 0.0)) for i in range(40)]
    d = fallback_decision(meta(), book(spread=40.0), up, CFG)
    assert d["decision"] == "skip", d

    # 5. Delisted -> skip
    d = fallback_decision(meta(delisted=True), book(), up, CFG)
    assert d["decision"] == "skip", d

    # 6. Overbought ramp: RSI 100 -> hold "RSI extreme"
    ob = [_c(100.0 + i * 1.5) for i in range(40)]
    d = fallback_decision(meta(), book(), ob, CFG)
    assert d["decision"] == "hold" and "RSI extreme" in d["reason"], d

    # 7. Conflict: overall momentum down but last price above EMA20 -> hold conflict
    mix = [_c(140.0 - i * 1.0) for i in range(30)] + [_c(110.0 + i * 0.8) for i in range(1, 15)]
    d = fallback_decision(meta(), book(), mix, CFG)
    print("case7 conflict:", d["decision"], d["confidence"], "-", d["reason"])
    assert d["decision"] == "hold", d

    print("ALL FALLBACK TESTS PASSED")


if __name__ == "__main__":
    main()