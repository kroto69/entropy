"""Self-check for indicators + regime. Run: python3 test_indicators.py"""


def _c(close, high=None, low=None):
    return {"c": close, "h": high if high is not None else close, "l": low if low is not None else close}


def main():
    from indicators import calculate_ema, calculate_rsi, calculate_atr

    # 1. Rising trend
    up = [_c(float(i)) for i in range(1, 61)]
    assert calculate_ema(up, 20)[-1] is not None, "EMA rising should compute"
    assert calculate_ema(up, 20)[-1] > 30, "EMA should trend up"

    # 2. RSI overbought on monotonic rise
    r = calculate_rsi(up, 14)
    assert r[-1] is not None and r[-1] > 70, f"RSI rising should be >70, got {r[-1]}"

    # 3. RSI oversold on monotonic fall
    dn = [_c(float(60 - i)) for i in range(60)]
    r_dn = calculate_rsi(dn, 14)
    assert r_dn[-1] is not None and r_dn[-1] < 30, f"RSI falling should be <30, got {r_dn[-1]}"

    # 4. ATR positive on volatile data
    vol = [_c(100.0 + (i % 3), high=100.0 + (i % 3) + 2.0, low=100.0 + (i % 3) - 2.0) for i in range(60)]
    a = calculate_atr(vol, 14)
    assert a[-1] is not None and a[-1] > 0, "ATR should be positive"

    # 5. Insufficient data → all None (no crash)
    tiny = [_c(1.0) for _ in range(3)]
    assert calculate_ema(tiny, 20)[-1] is None, "EMA insufficient -> None"
    assert calculate_rsi(tiny, 14)[-1] is None, "RSI insufficient -> None"
    assert calculate_atr(tiny, 14)[-1] is None, "ATR insufficient -> None"

    # 6. None / bad values skipped without break
    messy = [_c(float(i)) if i % 2 else {"c": "bad"} for i in range(1, 80)]
    e = calculate_ema(messy, 20)
    assert any(x is not None for x in e), "EMA should skip bad values and still compute"

    print("ALL INDICATOR TESTS PASSED")


if __name__ == "__main__":
    main()