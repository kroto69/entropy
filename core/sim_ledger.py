"""Virtual dry-run position ledger.

Tracks simulated OPEN → HOLD/TP/SL/AI-CLOSE/MAX-HOLD lifecycle so the AI can
learn from virtual PnL without any exchange order. Marked simulated, never
counted as live volume.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path

LEDGER = Path("/entropy/state/sim_positions.json")
CLOSED = Path("/entropy/state/sim_trades.jsonl")


def _load(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return default


def _save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2))


def open_sim(coin, side, size, entry_px, tp, sl, confidence, reason, source,
             amount_usdc, leverage, max_hold_minutes):
    positions = _load(LEDGER, {})
    positions[coin] = {
        "coin": coin, "side": side, "size": size, "entry_px": entry_px,
        "tp": tp, "sl": sl, "confidence": confidence, "reason": reason,
        "source": source, "opened_at": time.time(),
        "amount_usdc": amount_usdc, "leverage": leverage,
        "max_hold_minutes": max_hold_minutes, "simulated": True,
    }
    _save(LEDGER, positions)
    return positions[coin]


def load_sim():
    return _load(LEDGER, {})


def repair_protection(coin, tp, sl):
    positions = _load(LEDGER, {})
    p = positions.get(coin)
    if not p:
        return
    p["tp"], p["sl"] = str(tp), str(sl)
    _save(LEDGER, positions)


def reset_sim():
    _save(LEDGER, {})
    return {}


def close_sim(coin, mark_px, exit_reason):
    positions = _load(LEDGER, {})
    p = positions.pop(coin, None)
    if not p:
        return None
    side = p["side"]
    size = float(p["size"])
    entry = float(p["entry_px"])
    px = float(mark_px)
    pnl = ((px - entry) * size) if side == "buy" else ((entry - px) * size)
    held_minutes = int((time.time() - p["opened_at"]) / 60)
    _save(LEDGER, positions)
    row = {
        "type": "sim_trade_result", "strategy": p.get("source", "ai_primary"),
        "coin": coin, "side": side, "size": size, "entry_px": entry,
        "exit_px": px, "pnl": round(pnl, 4), "exit_reason": exit_reason,
        "held_minutes": held_minutes, "entry_confidence": p.get("confidence"),
        "entry_reason": p.get("reason"), "is_simulated": True,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    with CLOSED.open("a") as f:
        f.write(json.dumps(row, separators=(",", ":")) + "\n")
    return row


def mark_to_market(coin, mark_px):
    positions = _load(LEDGER, {})
    p = positions.get(coin)
    if not p:
        return None
    side = p["side"]
    size = float(p["size"])
    entry = float(p["entry_px"])
    px = float(mark_px)
    pnl = ((px - entry) * size) if side == "buy" else ((entry - px) * size)
    return {
        "coin": coin, "side": side, "size": size, "entry_px": entry,
        "mark_px": px, "u_pnl": round(pnl, 4),
        "tp": p["tp"], "sl": p["sl"],
        "held_minutes": int((time.time() - p["opened_at"]) / 60),
        "confidence": p.get("confidence"), "reason": p.get("reason"),
        "simulated": True,
    }
