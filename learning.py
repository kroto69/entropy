"""Append-only learning log. Never changes live config."""
import json
from datetime import datetime, timezone
from pathlib import Path

LOG = Path('/entropy/state/learning.jsonl')


def record(event):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    row = dict(event)
    row['recorded_at'] = datetime.now(timezone.utc).isoformat()
    with LOG.open('a') as f:
        f.write(json.dumps(row, separators=(',', ':')) + '\n')
    return row


def read_all():
    if not LOG.exists():
        return []
    return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]

if __name__ == '__main__':
    print(f'learning log: {LOG}')
# ponytail: append-only lessons; promote changes only after manual review/backtest.
