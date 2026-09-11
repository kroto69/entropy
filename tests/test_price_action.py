"""Deterministic entry-quality gate tests."""
from strategy.indicators.price_action import (
    candle_ohlc, closed_candles, entry_quality_gate,
    strong_bearish_reversal, strong_bullish_reversal,
)
from strategy.decision import validate_ai_decision

CFG = {
    "post_extreme_lookback_candles": 5, "rsi_overbought": 75,
    "rsi_oversold": 25, "max_ema_extension_pct": 0.8,
    "reversal_min_body_ratio": 0.55, "sr_lookback_candles": 20,
    "sr_min_room_pct": 0.35,
}
BOOK = {"spread_bps": 10.0}
META = {"is_delisted": False}


def c(o, h, l, close):
    return {"o": o, "h": h, "l": l, "c": close}


def main():
    # Healthy bull: no recent extreme, close near EMA20, room above.
    candles = [c(100 + i * .1, 100.2 + i * .1, 99.9 + i * .1, 100.1 + i * .1) for i in range(20)]
    assert entry_quality_gate("buy", candles, {"ema20": 101.8, "rsi_series": [55] * 20}, BOOK, CFG) is None

    # Pump then two bearish pullback candles: long blocked.
    pump = [c(100, 101, 99.8, 100.8), c(100.8, 110, 100.5, 109),
            c(109, 109.5, 106, 106.8), c(106.8, 107, 104, 104.5)]
    reason = entry_quality_gate("buy", pump, {"ema20": 103, "rsi_series": [60, 80, 70, 55]}, BOOK, CFG)
    assert reason and "post-overbought cooldown" in reason, reason

    # RSI cooled to 55, lower lows remain: still blocked.
    reason = entry_quality_gate("buy", pump, {"ema20": 103, "rsi_series": [60, 80, 60, 55]}, BOOK, CFG)
    assert reason and "post-overbought cooldown" in reason, reason

    # Bullish confirmation followed by close above confirmation high: allowed.
    reclaim = [c(100, 101, 98.5, 99), c(99, 100, 97, 98),
               c(98, 102, 97.5, 101), c(101, 104, 100, 103)]
    assert entry_quality_gate("buy", reclaim, {"ema20": 103, "rsi_series": [80, 60, 58, 55]}, BOOK, CFG) is None

    # Dump then bullish bounce: short blocked.
    dump = [c(100, 100.5, 90, 91), c(91, 96, 90.5, 95)]
    reason = entry_quality_gate("sell", dump, {"ema20": 95, "rsi_series": [20, 45]}, BOOK, CFG)
    assert reason and "post-oversold cooldown" in reason, reason

    bearish = c(100, 101, 90, 91)
    bullish = c(90, 101, 89, 100)
    assert strong_bearish_reversal(bearish)
    assert strong_bullish_reversal(bullish)
    assert "strong bearish" in entry_quality_gate("buy", [c(100, 101, 99, 100), bearish], {"ema20": 95, "rsi_series": [50, 50]}, BOOK, CFG)
    assert "strong bullish" in entry_quality_gate("sell", [c(100, 101, 99, 100), bullish], {"ema20": 95, "rsi_series": [50, 50]}, BOOK, CFG)

    # EMA anti-chase with an impulse (range expansion >= 1.8x prior), both directions.
    long_ext = [c(100, 101, 99, 100), c(100, 105, 99.5, 104.5)]
    short_ext = [c(100, 101, 99, 100), c(100, 100.5, 95, 95.5)]
    assert "extended" in (entry_quality_gate("buy", long_ext, {"ema20": 100}, BOOK, CFG) or "")
    assert "extended" in (entry_quality_gate("sell", short_ext, {"ema20": 100}, BOOK, CFG) or "")

    # Future active candle excluded; invalid data fails closed.
    assert len(closed_candles([c(100, 101, 99, 100), {"o": 1, "h": 2, "l": 1, "c": 1.5, "T": 2 * 10**15}], now=10**12)) == 1
    assert entry_quality_gate("buy", [{"c": 1}], {"ema20": 1}, BOOK, CFG)

    # AI open cannot bypass deterministic veto.
    ai = validate_ai_decision({"decision": "open", "side": "buy", "confidence": .9}, META, BOOK,
                              {"strategy": {"min_confidence": .65, "max_spread_bps": 25}},
                              candles=pump, indicators={"ema20": 103, "rsi_series": [60, 80, 70, 55]})
    assert ai["decision"] == "hold" and ai["confidence"] <= .45, ai

    # -- Late-exhaustion gate: bear dump, no retest -> SELL blocked.
    bear_dump = [c(100, 101, 98, 98.5), c(98.5, 99, 97, 97.2),
                 c(97.2, 97.5, 94, 94.6), c(94.6, 95, 93, 93.4),
                 c(93.4, 94, 91, 91.6), c(91.6, 92, 89, 89.4)]
    r = entry_quality_gate("sell", bear_dump, {"rsi_series": [50, 45, 40, 35, 32, 30]}, BOOK, CFG)
    assert r and "late short blocked" in r, r

    # -- Late-exhaustion gate: bull pump, no retest -> BUY blocked.
    bull_pump = [c(100, 102, 99, 101.5), c(101.5, 104, 101, 103.5),
                 c(103.5, 106, 103, 105.2), c(105.2, 108, 105, 107.0),
                 c(107, 110, 107, 109.5), c(109.5, 112, 109, 111.8)]
    r = entry_quality_gate("buy", bull_pump, {"rsi_series": [50, 55, 60, 62, 65, 70]}, BOOK, CFG)
    assert r and "late long blocked" in r, r

    # -- Valid breakdown-retest -> SELL allowed despite RSI 30 + dump.
    breakdown_retest = [c(100, 101, 98, 98.5), c(98.5, 99, 97.5, 97.8),
                        c(97.8, 98, 94, 94.6), c(94.6, 96, 93.5, 95.8),
                        c(95.8, 96, 91, 91.4), c(91.4, 91.8, 89, 89.2)]
    assert entry_quality_gate("sell", breakdown_retest, {"rsi_series": [60, 50, 45, 35, 32, 30]}, BOOK, CFG) is None

    # -- Valid breakout-retest -> BUY allowed despite RSI 70 + pump.
    breakout_retest = [c(100, 101, 98, 98.5), c(98.5, 99.5, 97, 97.8),
                       c(97.8, 99, 94, 94.6), c(94.6, 95, 93.5, 93.8),
                       c(93.8, 99, 93, 98.6), c(98.6, 106, 98, 105.0),
                       c(105, 108, 104, 107.5), c(107.5, 112, 107, 111.0)]
    assert entry_quality_gate("buy", breakout_retest, {"rsi_series": [55, 58, 60, 62, 65, 68, 69, 70]}, BOOK, CFG) is None

    print("ALL PRICE ACTION TESTS PASSED")


if __name__ == "__main__":
    main()
