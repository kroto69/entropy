"""Self-check for fallback_decision heuristics. Run: python3 test_fallback.py"""


def _c(close, high=None, low=None):
    return {"c": close, "h": high if high is not None else close, "l": low if low is not None else close}


CFG = {
    "strategy": {"trend_threshold_pct": 0.5, "min_confidence": 0.65,
                 "max_spread_bps": 25.0, "min_candle_count": 20},
    "position": {},
    "runtime": {},
}


def book(spread=10.0):
    return {"bid_px": 100.0, "ask_px": 100.0 + spread / 10000 * 100.0, "spread_bps": spread,
            "bids": [], "asks": []}


def meta(delisted=False):
    return {"is_delisted": delisted}


def main():
    from decision import fallback_decision

    # 1. Strong bull: momentum up + regime bull + RSI < 70 -> open buy
    up = [_c(100.0 + i * 0.5) for i in range(40)]
    d = fallback_decision(meta(), book(), up, CFG)
    assert d["decision"] == "open" and d["side"] == "buy", d
    assert d["confidence"] >= 0.65, d

    # 2. Strong bear: momentum down + regime bear + RSI > 30 -> open sell
    dn = [_c(120.0 - i * 0.5) for i in range(40)]
    d = fallback_decision(meta(), book(), dn, CFG)
    assert d["decision"] == "open" and d["side"] == "sell", d

    # 3. Flat: below threshold -> hold
    flat = [_c(100.0 + 0.01 * ((-1) ** i)) for i in range(40)]
    d = fallback_decision(meta(), book(), flat, CFG)
    assert d["decision"] == "hold", d

    # 4. Wide spread -> skip (fail-closed)
    up = [_c(100.0 + i * 0.5) for i in range(40)]
    d = fallback_decision(meta(), book(spread=40.0), up, CFG)
    assert d["decision"] == "skip", d

    # 5. Delisted -> skip
    d = fallback_decision(meta(delisted=True), book(), up, CFG)
    assert d["decision"] == "skip", d

    # 6. Overbought bull: RSI > 70 on aggressive rise -> momentum+regime agree
    #    but RSI overextended -> signals=2, conf 0.75 -> still open but capped
    ob = [_c(100.0 + i * 1.5) for i in range(40)]
    d = fallback_decision(meta(), book(), ob, CFG)
    # RSI on monotonic ramp = 100 (overextended). signals = momentum+regime = 2
    assert d["decision"] == "open", d
    assert d["confidence"] == 0.75, d

    # 7. Conflict case: momentum up but regime bear -> signals=2? momentum + rsi maybe
    #    Construct: rising recent, falling overall -> regime uses EMA20 fallback
    mix = [_c(120.0 - i * 0.8) for i in range(30)] + [_c(100.0 + i * 0.5) for i in range(1, 15)]
    d = fallback_decision(meta(), book(), mix, CFG)
    # change over full window is down, but recent up. Momentum side = sell (change<0 overall)
    # regime: last > ema20 -> bull (conflict with sell)
    # RSI likely < 30 on that crash -> overextended for sell
    # Expect hold (signals < 2) OR open sell; print for visibility
    print("case7 conflict:", d["decision"], d["confidence"], "-", d["reason"])

    print("ALL FALLBACK TESTS PASSED")


if __name__ == "__main__":
    main()