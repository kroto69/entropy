"""One read-only decision cycle with OHLC raw / 5m filter / final visibility."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.market_data import candles_snapshot, book_view
from core.resolver import resolve_market
from strategy.decision import fallback_decision, load_config
from strategy.indicators.price_action import closed_candles
from strategy.strategies.ohlc import decide as ohlc_decide, check_5m_timing_filter


def main():
    coin = "io:ANTH"
    cfg = load_config()
    meta = resolve_market(coin)
    book = book_view(coin)
    candles = candles_snapshot(coin)          # 15m primary OHLC
    timing = candles_snapshot(coin, interval="5m", lookback_minutes=60)

    params = cfg.get("strategy_params") or {}

    # 1. OHLC 15m raw signal (pure decide, no gate / no timing)
    raw = ohlc_decide(candles, params)

    # 2. 5m timing filter (runs only if OHLC raw = open)
    timing_result = None
    if raw.get("decision") == "open":
        passed, reason = check_5m_timing_filter(timing, raw.get("side"))
        timing_result = {"passed": passed, "reason": reason}

    # 3. Final decision via full router (5m filter + entry_quality_gate + AI veto)
    final = fallback_decision(meta, book, candles, cfg, timing_candles=timing)

    out = {
        "config_loaded": True,
        "dry_run": cfg["runtime"]["dry_run"],
        "ai_mode": cfg.get("ai_mode"),
        "coin": coin,
        "closed_candles_15m": len(closed_candles(candles)),
        "closed_candles_5m": len(closed_candles(timing)),
        "ohlc_15m_raw": raw,
        "5m_timing_filter": timing_result,
        "final_decision_after_filter": final,
    }

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()