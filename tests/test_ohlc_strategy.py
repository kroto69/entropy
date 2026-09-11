"""Deterministic OHLC strategy + router + 5m timing filter tests."""
from strategy.strategies.ohlc import decide as ohlc_decide, DEFAULTS
from strategy.strategies.ohlc import check_5m_timing_filter
from strategy.decision import fallback_decision


def c(o, h, l, close):
    return {"o": o, "h": h, "l": l, "c": close}


def book():
    return {"spread_bps": 10.0, "bid_px": 100.0, "ask_px": 100.1,
            "bids": [], "asks": []}


def meta():
    return {"is_delisted": False}


# --- signal helpers -----------------------------------------------------

def bull_reclaim_series(n=24):
    """Rising closes with a pullback then bullish reclaim confirmation."""
    out = []
    base = 100.0
    for i in range(n - 6):
        out.append(c(base, base + 1, base - 1, base + 0.5))
        base += 0.5
    # pullback candle (bearish)
    out.append(c(base, base + 0.2, base - 1.2, base - 1.0))
    # bullish confirmation
    out.append(c(base - 1.0, base - 0.5, base - 1.5, base - 0.6))
    # close above confirmation high
    out.append(c(base - 0.6, base + 0.6, base - 0.8, base + 0.4))
    out.append(c(base + 0.4, base + 1.2, base + 0.2, base + 1.0))
    out.append(c(base + 1.0, base + 1.6, base + 0.9, base + 1.4))
    return out


def bear_reclaim_series(n=24):
    out = []
    base = 130.0
    for i in range(n - 6):
        out.append(c(base, base + 1, base - 1, base - 0.5))
        base -= 0.5
    # bounce candle (bullish)
    out.append(c(base, base + 1.2, base - 0.2, base + 1.0))
    # bearish confirmation
    out.append(c(base + 1.0, base + 1.5, base + 0.5, base + 0.6))
    # close below confirmation low
    out.append(c(base + 0.6, base + 0.8, base - 0.6, base - 0.4))
    out.append(c(base - 0.4, base - 0.2, base - 1.2, base - 1.0))
    out.append(c(base - 1.0, base - 0.9, base - 1.4, base - 1.4))
    return out


def main():
    # 1. Valid bullish OHLC trend -> OPEN BUY
    d = ohlc_decide(bull_reclaim_series())
    assert d["decision"] == "open" and d["side"] == "buy", d
    assert 0.60 <= d["confidence"] <= 0.85, d

    # 2. Valid bearish OHLC trend -> OPEN SELL
    d = ohlc_decide(bear_reclaim_series())
    assert d["decision"] == "open" and d["side"] == "sell", d

    # 3. Flat market -> HOLD
    flat = [c(100.0 + 0.01 * ((-1) ** i), 100.2, 99.8, 100.0 + 0.01 * ((-1) ** i))
            for i in range(25)]
    d = ohlc_decide(flat)
    assert d["decision"] in ("hold", "skip") and d["decision"] != "open", d

    # 4. Conflicting: rolling gain but latest candles bearish / no reclaim -> HOLD
    gain_then_drop = [c(100 + i * 0.5, 100 + i * 0.5 + 1, 100 + i * 0.5 - 1, 100 + i * 0.5)
                      for i in range(20)]
    # now 4 bearish closes with no reclaim
    base = gain_then_drop[-1]["c"]
    for step, lo in [( -1.0, -1.5), (-1.0, -1.5), (-0.8, -1.2), (-0.5, -1.0)]:
        base = base + step
        gain_then_drop.append(c(base + 0.3, base + 0.4, base + lo, base))
    d = ohlc_decide(gain_then_drop)
    assert d["decision"] == "hold", d

    # 5. Insufficient candles -> HOLD/SKIP
    d = ohlc_decide([c(100, 101, 99, 100.5), c(100.5, 101.5, 100, 101)])
    assert d["decision"] in ("hold", "skip") and d["decision"] != "open", d

    # 6. Invalid OHLC -> fail closed
    invalids = [
        [c(None, 1, 1, 1)] * 25,
        [c(1, 0, 0, 0)] * 25,
        [c(-1, -2, -3, -4)] * 25,
        [{"o": "a", "h": "b", "l": "c", "c": "d"}] * 25,
        [{"open": 1, "high": 2}] * 25,  # missing low/close
    ]
    for inv in invalids:
        d = ohlc_decide(inv)
        assert d["decision"] in ("hold", "skip") and d["decision"] != "open", (d, inv[:1])

    # 7. Active candle ignored (T future), same as filtered
    good = bull_reclaim_series()
    future = list(good) + [{"o": 1, "h": 2, "l": 1, "c": 1.5, "T": 2 * 10**15}]
    d1 = ohlc_decide(good)
    d2 = ohlc_decide(future)
    assert d1["decision"] == d2["decision"], (d1, d2)

    # --- 8. Router -----------------------------------------------------

    # string "ohlc" -> OHLC strategy
    cfg_ohlc = {"strategy": "ohlc", "strategy_params": {}}
    d = fallback_decision(meta(), book(), bull_reclaim_series(), cfg_ohlc)
    assert d["decision"] == "open" and d["side"] == "buy", d

    # unknown strategy -> fail closed
    cfg_unknown = {"strategy": "banana"}
    d = fallback_decision(meta(), book(), bull_reclaim_series(), cfg_unknown)
    assert d["decision"] == "skip" and "unknown strategy" in d["reason"], d

    # legacy object config -> still works
    cfg_legacy = {
        "strategy": {"trend_threshold_pct": 0.5, "min_confidence": 0.65,
                     "max_spread_bps": 25.0, "min_candle_count": 20,
                     "max_ema_extension_pct": 2.0},
        "position": {}, "runtime": {},
    }
    raw = [100.0]
    for i in range(39):
        step = 0.5 if i % 3 != 2 else -0.45
        raw.append(raw[-1] + step)
    up = [c(v, v + 1, v - 1, v) for v in raw]
    d = fallback_decision(meta(), book(), up, cfg_legacy)
    assert d["decision"] == "open" and d["side"] == "buy", d

    # 10. strategy_params defaults + override
    d_no_params = ohlc_decide(bull_reclaim_series(), None)
    assert d_no_params["decision"] == "open"
    # override threshold absurdly high -> flat hold
    d_override = ohlc_decide(flat, {"trend_threshold_pct": 99.0})
    assert d_override["decision"] == "hold"
    # invalid override type ignored (falls back to default), no crash
    d_bad_type = ohlc_decide(bull_reclaim_series(), {"lookback_candles": "zzz"})
    assert d_bad_type["decision"] == "open"

    # --- 11. 5m timing filter -----------------------------------------

    # BUY pass: >=3 bullish, latest close > prior close
    bull5 = [
        c(1.00, 1.05, 0.95, 1.04),
        c(1.04, 1.10, 1.02, 1.09),
        c(1.09, 1.12, 1.06, 1.08),  # bearish candle (ok as long as >=3 bullish)
        c(1.08, 1.14, 1.07, 1.13),
        c(1.13, 1.18, 1.11, 1.16),
    ]
    p, r = check_5m_timing_filter(bull5, "buy")
    assert p, (r, bull5)

    # SELL pass: >=3 bearish, latest close < prior close
    bull5_for_sell = [
        c(2.00, 2.05, 1.95, 1.96),
        c(1.96, 1.98, 1.90, 1.92),
        c(1.92, 1.96, 1.88, 1.95),  # bullish candle (ok)
        c(1.95, 1.96, 1.87, 1.89),
        c(1.89, 1.90, 1.82, 1.84),
    ]
    p, r = check_5m_timing_filter(bull5_for_sell, "sell")
    assert p, (r, bull5_for_sell)

    # BUY fail: latest close not above prior close
    buy_fail_last = [
        c(1.00, 1.05, 0.95, 1.04),
        c(1.04, 1.10, 1.02, 1.09),
        c(1.09, 1.12, 1.06, 1.11),
        c(1.11, 1.16, 1.10, 1.14),
        c(1.14, 1.16, 1.10, 1.13),  # close < prior close (1.14)
    ]
    p, r = check_5m_timing_filter(buy_fail_last, "buy")
    assert not p and "latest close" in r, (r,)

    # BUY fail: fewer than 3 bullish
    buy_fail_count = [
        c(1.00, 1.05, 0.95, 1.02),
        c(1.02, 1.04, 0.98, 1.00),
        c(0.99, 1.01, 0.96, 0.98),
        c(0.98, 1.00, 0.95, 0.97),
        c(0.97, 1.05, 0.96, 1.03),
        c(1.03, 1.05, 0.98, 1.04),
    ]
    p, r = check_5m_timing_filter(buy_fail_count, "buy")
    assert not p and "bullish candles" in r, (r,)

    # SELL fail: latest close not below prior close
    sell_fail_last = [
        c(2.00, 2.05, 1.95, 1.96),
        c(1.96, 1.98, 1.90, 1.92),
        c(1.92, 1.96, 1.88, 1.89),
        c(1.89, 1.90, 1.82, 1.84),
        c(1.84, 1.86, 1.80, 1.86),  # close above prior close (1.84)
    ]
    p, r = check_5m_timing_filter(sell_fail_last, "sell")
    assert not p and "latest close" in r, (r,)

    # missing/invalid -> fail closed
    p, r = check_5m_timing_filter([], "buy")
    assert not p and "insufficient" in r, (r,)
    p, r = check_5m_timing_filter([c(1, 1, 1, 1)] * 3, "buy")
    assert not p and "insufficient" in r, (r,)
    p, r = check_5m_timing_filter([c(None, 1, 1, 1)] * 5, "buy")
    assert not p and "insufficient" in r, (r,)
    p, r = check_5m_timing_filter(bull5, "flip")
    assert not p and "invalid input" in r, (r,)

    # --- 12. Router applies 5m filter only after open -------------------

    cfg_ohlc = {"strategy": "ohlc", "ai_mode": "veto", "strategy_params": {}}
    # no timing candles -> OHLC open passes through unchanged
    d = fallback_decision(meta(), book(), bull_reclaim_series(), cfg_ohlc)
    assert d["decision"] == "open", d
    # timing candles fail -> HOLD with cap 0.45
    d = fallback_decision(meta(), book(), bull_reclaim_series(), cfg_ohlc, timing_candles=buy_fail_last)
    assert d["decision"] == "hold" and d["side"] is None, d
    assert d["confidence"] <= 0.45, d
    assert "5m timing filter failed" in d["reason"], d
    # timing candles pass -> open
    d = fallback_decision(meta(), book(), bull_reclaim_series(), cfg_ohlc, timing_candles=bull5)
    assert d["decision"] == "open" and d["side"] == "buy", d

    # --- 13. Aggressive mode vs default --------------------------------

    # 0.3% momentum over 10 candles: default holds, aggressive opens.
    def monotonic_rise(n=10, step=0.03, start=100.0):
        out = []
        price = start
        for _ in range(n):
            o = price
            cl = price + step
            out.append(c(o, cl + 0.02, o - 0.02, cl))
            price = cl
        return out

    rise_small = monotonic_rise()
    d = ohlc_decide(rise_small)                             # default
    assert d["decision"] == "hold", d
    d = ohlc_decide(rise_small, {"mode": "aggressive"})     # aggressive
    assert d["decision"] == "open" and d["side"] == "buy", d
    assert d["confidence"] <= 0.80, d
    assert 0.55 <= d["confidence"] <= 0.80, d

    # Aggressive opens without reclaim (monotonic rise = no pullback).
    assert "no reclaim" in d["reason"], d
    # Default requires reclaim -> monotonic rise without reclaim is hold even
    # if momentum is big enough. Use 20 candles to satisfy default lookback.
    rise_big = monotonic_rise(n=22, step=0.15)              # ~3% move
    d = ohlc_decide(rise_big)                               # default, no reclaim
    assert d["decision"] == "hold" and "reclaim" in d["reason"], d
    d = ohlc_decide(rise_big, {"mode": "aggressive"})
    assert d["decision"] == "open", d

    # Aggressive confidence: >=0.4% move gets the +0.10 momentum boost.
    d = ohlc_decide(monotonic_rise(n=10, step=0.06), {"mode": "aggressive"})  # ~0.6%
    assert d["decision"] == "open" and d["confidence"] >= 0.65, d

    # Default mode unchanged when mode missing/None.
    d_none = ohlc_decide(bull_reclaim_series())             # default
    d_explicit = ohlc_decide(rise_small)["decision"]
    assert d_none["decision"] == "open"
    assert d_explicit == "hold"

    # --- 14. 5m timing filter: aggressive (2 of 3) ----------------------

    agg_bull_pass = [
        c(1.00, 1.05, 0.95, 1.04),   # bullish
        c(1.04, 1.06, 1.00, 1.03),   # bearish
        c(1.03, 1.10, 1.02, 1.09),   # bullish, latest close > open
    ]
    p, r = check_5m_timing_filter(agg_bull_pass, "buy", mode="aggressive")
    assert p, (r, agg_bull_pass)

    agg_sell_pass = [
        c(2.00, 2.05, 1.95, 1.96),   # bearish
        c(1.96, 1.98, 1.90, 1.99),   # bullish
        c(1.99, 2.00, 1.86, 1.88),   # bearish, latest close < open
    ]
    p, r = check_5m_timing_filter(agg_sell_pass, "sell", mode="aggressive")
    assert p, (r, agg_sell_pass)

    # Aggressive fail: only 1 bullish of last 3.
    agg_bull_fail = [
        c(1.00, 1.05, 0.95, 1.04),   # bullish
        c(1.04, 1.06, 1.00, 1.03),   # bearish
        c(1.03, 1.04, 0.99, 1.01),   # bearish (latest)
    ]
    p, r = check_5m_timing_filter(agg_bull_fail, "buy", mode="aggressive")
    assert not p and "bullish candles" in r, (r,)

    # Aggressive requires latest candle direction match (no prior-close rule).
    agg_bull_latest_bearish = [
        c(1.00, 1.05, 0.95, 1.04),   # bullish
        c(1.04, 1.10, 1.02, 1.09),   # bullish
        c(1.09, 1.12, 1.06, 1.07),   # bearish (2 bullish of 3, but latest bearish)
    ]
    p, r = check_5m_timing_filter(agg_bull_latest_bearish, "buy", mode="aggressive")
    assert not p and "latest candle" in r, (r,)

    # Default mode with only 3 candles -> fail closed (needs 5).
    p, r = check_5m_timing_filter(agg_bull_pass, "buy", mode="default")
    assert not p and "insufficient" in r, (r,)

    # --- 15. Safety gate still vetoes aggressive entries ----------------

    agg_pump = []
    price = 100.0
    # 20 candles for default RSI lookback + a clear extended pump.
    for i in range(22):
        o = price
        cl = price + 0.8
        agg_pump.append(c(o, cl + 0.4, o - 0.1, cl))
        price = cl

    cfg_agg = {"strategy": "ohlc", "ai_mode": "off",
               "strategy_params": {"mode": "aggressive"},
               "position": {}, "runtime": {}}
    # Raw aggressive signal is open...
    raw = ohlc_decide(agg_pump, {"mode": "aggressive"})
    assert raw["decision"] == "open", raw
    # ...but router's entry_quality_gate vetoes the extended pump.
    d = fallback_decision(meta(), book(), agg_pump, cfg_agg,
                           timing_candles=[c(1, 1.1, 0.9, 1.05)] * 5)
    assert d["decision"] == "hold" and d["side"] is None, d

    print("ALL OHLC STRATEGY TESTS PASSED")


if __name__ == "__main__":
    main()