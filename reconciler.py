"""Reconciler: compare exchange state with local record. Exchange is truth."""
import json
import time
from pathlib import Path

LOG = Path("/entropy/state/trade_log.jsonl")
LOCAL_STATE = Path("/entropy/state/local_state.json")


def load_local():
    if LOCAL_STATE.exists():
        return json.loads(LOCAL_STATE.read_text())
    return {"open_intents": {}, "closed": []}


def save_local(state):
    LOCAL_STATE.parent.mkdir(parents=True, exist_ok=True)
    LOCAL_STATE.write_text(json.dumps(state, indent=2))


def reconcile(account, open_orders):
    """Detect: local intent no resting order; exchange position with no local intent."""
    issues = []
    local = load_local()
    live_order_coins = {o.get("coin") for o in open_orders}
    live_position_coins = {p.get("coin") for p in account.get("positions", [])}
    for cid, intent in local["open_intents"].items():
        coin = intent.get("coin")
        if coin not in live_order_coins and coin not in live_position_coins:
            issues.append(f"intent {cid} ({coin}) not found on exchange: stale")
    for coin in live_position_coins:
        if coin not in {i.get("coin") for i in local["open_intents"].values()}:
            issues.append(f"exchange position {coin} has no local intent: orphan")
    return {"issues": issues, "ok": not issues, "ts": time.time()}


if __name__ == "__main__":
    account = {"positions": [{"coin": "io:SNDK"}]}
    open_orders = [{"coin": "io:ANTH"}]
    local = {"open_intents": {"abc": {"coin": "io:SNDK"}}, "closed": []}
    save_local(local)
    print(json.dumps(reconcile(account, open_orders), indent=2))
