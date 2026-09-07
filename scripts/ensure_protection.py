"""One-off: place full-size native TP/SL on every live io:* position.

Usage: python3 -m scripts.ensure_protection
Reads live positions from the exchange, then for each position submits
reduce-only TP/SL trigger orders sized to the FULL position. Existing
stale reduce-only orders on the coin are cancelled first (see submit_protection).
"""
import json
import os

from core.env import load_env
from core.market_data import clearinghouse_state, tick_decimals
from core.executor import Executor, is_live

load_env()


def main():
    if not is_live():
        print("NOT LIVE — refusing to submit orders (DRY_RUN/LIVE_TRADING_ENABLED/keys).")
        return 2
    cfg = json.load(open("/entropy/config.json"))
    pos_cfg = cfg["position"]
    tp_pct = str(pos_cfg["take_profit_pct"])
    sl_pct = str(pos_cfg["stop_loss_pct"])
    leverage = int(pos_cfg.get("leverage", 5))

    addr = os.getenv("HL_ACCOUNT_ADDRESS")
    st = clearinghouse_state(addr, dex="io")
    positions = [p.get("position") for p in st.get("assetPositions", [])
                 if float((p.get("position") or {}).get("szi") or 0) != 0]

    ex = Executor()
    for pos in positions:
        coin = pos["coin"]
        szi = float(pos["szi"])
        side = "buy" if szi > 0 else "sell"
        size = str(abs(szi))
        entry = float(pos["entryPx"])
        decimals = tick_decimals(coin)
        intent = {
            "coin": coin,
            "side": side,
            "size": size,
            "take_profit_pct": tp_pct,
            "stop_loss_pct": sl_pct,
            "leverage": leverage,
        }
        out = ex.submit_protection(intent, entry, price_decimals=decimals)
        print(json.dumps({"coin": coin, "size": size, "entry": entry,
                          "decimals": decimals, "result": out}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())