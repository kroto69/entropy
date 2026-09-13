"""Virtual account for dry-run risk and PnL. Never touches exchange funds."""
import json
from pathlib import Path

PATH = Path('/entropy/state/sim_account.json')
INITIAL = 1000.0


def load():
    if not PATH.exists():
        return {'initial_balance': INITIAL, 'realized_pnl': 0.0}
    try:
        value = json.loads(PATH.read_text())
        value.setdefault('initial_balance', INITIAL)
        value.setdefault('realized_pnl', 0.0)
        return value
    except (OSError, ValueError):
        return {'initial_balance': INITIAL, 'realized_pnl': 0.0}


def save(value):
    PATH.parent.mkdir(parents=True, exist_ok=True)
    PATH.write_text(json.dumps(value, indent=2))


def account(positions, leverage):
    value = load()
    used = sum(float(p.get('amount_usdc', 0)) / max(1, leverage) for p in positions.values())
    unrealized = sum(float(p.get('u_pnl', 0)) for p in positions.values())
    equity = float(value['initial_balance']) + float(value['realized_pnl']) + unrealized
    return {'free_collateral': equity - used, 'account_value': equity,
            'virtual_balance': equity, 'used_margin': used,
            'positions': list(positions.values())}


def record_close(pnl):
    value = load()
    value['realized_pnl'] = round(float(value.get('realized_pnl', 0)) + float(pnl), 8)
    save(value)
    return value


def reset():
    value = {'initial_balance': INITIAL, 'realized_pnl': 0.0}
    save(value)
    return value
