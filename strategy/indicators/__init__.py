"""Technical indicators for Entropy Bot. Pure stdlib — no numpy/pandas.

Each indicator is a pure function returning a list aligned with the input
candles, with leading None where the indicator is not yet defined.
"""
from .ema import calculate_ema
from .rsi import calculate_rsi
from .atr import calculate_atr
from .price_action import (candle_ohlc, closed_candles, entry_quality_gate,
                            ema_extension_pct, recent_support_resistance,
                            strong_bearish_reversal, strong_bullish_reversal,
                            late_exhaustion_block)

__all__ = ["calculate_ema", "calculate_rsi", "calculate_atr", "candle_ohlc",
           "closed_candles", "entry_quality_gate", "ema_extension_pct",
           "recent_support_resistance", "strong_bearish_reversal",
           "strong_bullish_reversal", "late_exhaustion_block"]