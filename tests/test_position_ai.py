"""Position AI contract and 15m dedupe tests; no exchange/API calls."""
import json
from unittest.mock import patch


def candle(t, close=100):
    return {"t": t - 899000, "T": t, "o": close - .2, "h": close + .3,
            "l": close - .3, "c": close}


def main():
    from strategy.ai import position_monitor_due, position_ai_decision

    positions = [{"coin": "io:ANTH"}]
    candles = {"io:ANTH": [candle(1000), candle(2000)]}
    assert position_monitor_due(positions, {}, candles) == [("io:ANTH", 2000)]
    assert position_monitor_due(positions, {"io:ANTH": 2000}, candles) == []
    assert position_monitor_due(positions, {"io:ANTH": 1000}, candles) == [("io:ANTH", 2000)]

    response = {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps({"decision": "close", "side": "buy",
                                "confidence": .8, "reason": "momentum reversed"})}}]}
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps(response).encode()
    with patch("urllib.request.urlopen", return_value=Resp()):
        out = position_ai_decision(
            "io:ANTH", {"side": "long", "size": .1, "entry_px": 100,
                        "mark_px": 101, "u_pnl": 1, "held_minutes": 20,
                        "tp": 110, "sl": 90},
            {"is_delisted": False}, {"bid_px": 100, "ask_px": 100.01,
                                      "spread_bps": 1, "bids": [], "asks": []},
            [candle(i * 900000 + 900000, 100 + i * .1) for i in range(20)],
            [candle(i * 300000 + 300000, 100 + i * .1) for i in range(6)],
            {"strategy": "ai", "strategy_params": {}, "position": {}},
            account={"positions": []})
    assert out["decision"] == "close"
    assert out["side"] is None
    print("POSITION AI TESTS PASSED")


if __name__ == "__main__":
    main()
