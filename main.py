"""Main loop combining all phases. Default dry-run; no live execution."""
import os, sys, time, json, traceback, threading
from pathlib import Path

from resolver import resolve_market
from market_data import book_view, candles_snapshot, account_summary
from decision import load_config, fallback_decision, validate_ai_decision, build_prompt_context
from ai import decide
from risk import check as risk_check
from planner import build_entry, simulate, as_dict
from executor import Executor, is_live as executor_is_live
from hyperliquid.utils.types import Cloid
from reconciler import reconcile, load_local, save_local
from telegram import (send, send_report, format_decision, format_positions_html,
                      close_buttons, poll_updates, answer_callback)
from learning import record as learning_record
from protection import prices as protection_prices
from executor import load_env as executor_load_env

executor_load_env()
CYCLE_INTERVAL = 10 * 60
_close_lock = threading.Lock()


def confirm_fill(executor, intent_dict, timeout_s=15):
    """Poll order status until a live fill is confirmed. Returns (filled, resp)."""
    import time as _t
    cloid = intent_dict.get("client_id")
    deadline = _t.time() + timeout_s
    while _t.time() < deadline:
        try:
            status = executor.info.query_order_by_cloid(
                executor.account_address, Cloid(cloid))
        except Exception as exc:
            return False, {"error": str(exc)}
        if status and status.get("status") == "filled":
            return True, status
        if status and status.get("status") in ("canceled", "rejected", "error"):
            return False, status
        _t.sleep(2)
    return False, {"error": "timeout"}


def response_fill(result):
    """Extract immediate fill from exchange response; IOC fills are final in response."""
    try:
        statuses = result["response"]["response"]["data"]["statuses"]
        for status in statuses:
            if "filled" in status:
                return True, status
            if "error" in status:
                return False, status
    except (KeyError, TypeError):
        pass
    return False, {}


def telegram_notify(text):
    """Send Telegram and log the delivery result."""
    result = send(text)
    print(f"telegram: {text.splitlines()[0][:80]!r} -> {result}")
    return result


def telegram_notify_html(text, buttons=None):
    """Send HTML report with optional inline buttons and log result."""
    result = send_report(text, buttons)
    print(f"telegram: {text.splitlines()[0][:80]!r} -> {result}")
    return result


POSITIONS_FILE = Path("/entropy/state/positions.json")


def load_position_meta():
    if POSITIONS_FILE.exists():
        try:
            return json.loads(POSITIONS_FILE.read_text())
        except (ValueError, OSError):
            return {}
    return {}


def save_position_meta(coin, info):
    meta = load_position_meta()
    meta[coin] = info
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(meta, indent=2))


def position_report(account):
    meta = load_position_meta()
    rows = []
    for p in account.get("positions", []):
        coin = p.get("coin")
        mark = live_price(coin)
        m = meta.get(coin, {})
        opened_at = m.get("opened_at")
        held_min = int((time.time() - opened_at) / 60) if opened_at else None
        entry = float(p.get("entry_px") or 0)
        szi = float(p.get("szi") or 0)
        px = mark or entry
        # Recompute PnL from live mark so Telegram matches the web (not a stale snapshot).
        pnl = ((px - entry) * szi) if px else float(p.get("unrealized_pnl") or 0)
        rows.append({"coin": coin, "side": "long" if szi > 0 else "short",
                     "size": szi, "entry_px": entry, "mark_px": px,
                     "u_pnl": pnl, "u_pnl_exch": p.get("unrealized_pnl"),
                     "margin_used": p.get("margin_used"),
                     "liq_px": p.get("liquidation_px"),
                     "held_minutes": held_min, "tp": m.get("tp"), "sl": m.get("sl")})
    return {"open_positions": len(rows), "positions": rows,
            "free_collateral": account.get("free_collateral", 0),
            "account_value": account.get("account_value", 0)}


def report_positions(mode, account=None):
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    if not address:
        return {}
    try:
        account = account or account_summary(address)
    except Exception as exc:
        print(f"position report failed: {exc}")
        return {}
    rep = position_report(account)
    lines = [f"[{mode}] POSITIONS {rep['open_positions']} | free collateral {rep['free_collateral']:.2f}"]
    if not rep["positions"]:
        lines.append("no open positions")
    for r in rep["positions"]:
        held = f"{r['held_minutes']}m" if r["held_minutes"] is not None else "unknown"
        lines.append(
            f"{r['coin']} {r['side'].upper()} sz={r['size']} entry={r['entry_px']} "
            f"uPnL={r['u_pnl']:+.4f} margin={r['margin_used']:.4f} held={held} "
            f"TP={r['tp'] or '-'} SL={r['sl'] or '-'}")
    msg = "\n".join(lines)
    print(msg)
    rep = {"open_positions": rep["open_positions"], "positions": rep["positions"],
           "free_collateral": rep["free_collateral"],
           "account_value": rep.get("account_value", rep.get("free_collateral", 0))}
    telegram_notify_html(format_positions_html(rep), close_buttons(rep["positions"]))
    return rep


def monitor_positions(cfg, mode):
    """Report live PnL and close positions exceeding max hold; TP/SL stays native."""
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    if not address:
        return
    account = account_summary(address)
    meta = load_position_meta()
    executor = Executor() if executor_is_live() else None
    max_hold = int(cfg["position"].get("max_hold_minutes", 0))
    for p in account.get("positions", []):
        coin = p.get("coin")
        opened_at = meta.get(coin, {}).get("opened_at")
        held = (time.time() - opened_at) / 60 if opened_at else None
        if executor and max_hold > 0 and held is not None and held >= max_hold:
            sz = abs(float(p.get("szi") or 0))
            side = "buy" if float(p.get("szi") or 0) > 0 else "sell"
            close = executor.submit_reduce_close(coin, side, sz)
            print(json.dumps({"action": "max_hold_close", "coin": coin,
                              "held_minutes": round(held, 1), "result": close}))
            telegram_notify(f"[LIVE] MAX-HOLD CLOSE {coin} held={held:.1f}m uPnL={float(p.get('unrealized_pnl') or 0):+.4f}")
    report_positions(mode, account)


def cycle(coin, cfg, mode, notify=True):
    meta = resolve_market(coin)
    book = book_view(coin)
    candles = candles_snapshot(coin)
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    account = account_summary(address) if address else {"free_collateral": 0, "positions": []}
    sig, decision_source = decide(coin, meta, book, candles, cfg, account)
    rd = risk_check(sig, meta, book, account, cfg)
    if not rd.allowed:
        result = {"coin": coin, "sig": sig, "risk": rd.reasons, "status": "REJECTED",
                  "decision_source": decision_source,
                  "positions": position_report(account)}
        return result

    # AI may request closing an existing position.
    if sig.get("decision") == "close":
        open_for_coin = [p for p in account.get("positions", []) if p.get("coin") == coin]
        if open_for_coin and executor_is_live():
            p = open_for_coin[0]
            sz = abs(float(p.get("szi") or 0))
            side = "buy" if float(p.get("szi") or 0) > 0 else "sell"
            closed = Executor().submit_reduce_close(coin, side, sz)
            result = {"coin": coin, "sig": sig, "status": "CLOSED",
                      "decision_source": decision_source, "close": closed,
                      "positions": position_report(account)}
            telegram_notify(f"[{mode}] CLOSED {coin} uPnL={float(p.get('unrealized_pnl') or 0):+.4f}")
            return result

    intent = build_entry(coin, sig, meta, book, cfg)
    intent_dict = as_dict(intent)
    intent_dict["max_leverage"] = meta.get("max_leverage")

    if not executor_is_live():
        result = Executor().submit_entry(intent_dict)
        result["decision_source"] = decision_source
        result["protection"] = "SKIPPED_DRYRUN"
        return result

    executor = Executor()
    result = executor.submit_entry(intent_dict)
    result["decision_source"] = decision_source
    result["protection"] = "SKIPPED_NO_FILL"

    filled, status = response_fill(result)
    if not filled and "error" not in status:
        filled, status = confirm_fill(executor, intent_dict)
    result["fill"] = "confirmed" if filled else "not_confirmed"
    if not filled:
        result["status"] = "NO_FILL"
        result["protection"] = "SKIPPED_NO_FILL"
        return result

    result["status"] = "FILLED"
    entry_px = float(status.get("filled", {}).get("avgPx", intent_dict["price"]))
    if entry_px <= 0:
        entry_px = float(intent_dict["price"])
    # Submit native reduce-only TP/SL trigger orders on the live position.
    prot = executor.submit_protection(intent_dict, entry_px)
    result["protection"] = prot.get("status", "unknown")
    result["protection_tp"] = prot.get("tp", prot.get("response"))
    if prot.get("status") == "ok":
        save_position_meta(coin, {"opened_at": time.time(), "entry_px": entry_px,
                                  "side": intent_dict["side"], "size": intent_dict["size"],
                                  "tp": prot.get("tp"), "sl": prot.get("sl")})
    else:
        # Protection failed: do not leave position unhedged. Alert loudly.
        telegram_notify(f"[ALERT] {coin} position OPEN but TP/SL protection FAILED: {prot}")
        result["warning"] = "PROTECTION_FAILED_POSITION_UNHEDGED"
    if notify:
        telegram_notify(format_decision(coin, sig, mode))
    result["positions"] = position_report(account)
    return result


def live_price(coin):
    """Latest mark/mid from l2 book. Returns None if market is gone or unreachable."""
    try:
        book = book_view(coin)
        return (book["bid_px"] + book["ask_px"]) / 2
    except Exception:
        return None


def current_positions_report():
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    if not address:
        return None
    try:
        return position_report(account_summary(address))
    except Exception as exc:
        print(f"position report failed: {exc}")
        return None


def handle_update(update, mode):
    """Handle commands only from configured Telegram chat."""
    msg = update.get("message") or {}
    cbq = update.get("callback_query") or {}
    incoming_chat = (msg.get("chat") or {}).get("id") or (cbq.get("message") or {}).get("chat", {}).get("id")
    if str(incoming_chat) != str(os.getenv("TELEGRAM_CHAT_ID")):
        if cbq:
            answer_callback(cbq.get("id"), "Unauthorized chat")
        return
    if msg:
        text = (msg.get("text") or "").strip()
        if text.startswith("/pos"):
            rep = current_positions_report()
            if rep:
                telegram_notify_html(format_positions_html(rep), close_buttons(rep["positions"]))
        elif text.startswith("/close"):
            parts = text.split()
            if len(parts) < 2:
                telegram_notify("Format: /close io:COIN")
                return
            coin = parts[1]
            manual_close(coin, mode)
        elif text.startswith("/status"):
            rep = current_positions_report()
            if rep:
                telegram_notify(f"[{mode}] positions={rep['open_positions']} "
                                f"free={rep['free_collateral']:.2f} "
                                f"coins={[p['coin'] for p in rep['positions']]}")
        elif text.startswith("/help"):
            telegram_notify("Command:\n/pos — posisi + tombol close\n"
                            "/status — ringkas\n/close io:COIN — tutup manual")
    elif cbq:
        data = cbq.get("data") or ""
        answer_callback(cbq.get("id"))
        if data.startswith("close:"):
            manual_close(data.split(":", 1)[1], mode)


def manual_close(coin, mode):
    global _close_lock
    if not _close_lock.acquire(blocking=False):
        telegram_notify("Close sedang diproses...")
        return
    try:
        _close(coin, mode)
    finally:
        _close_lock.release()


def _close(coin, mode):
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    if not address:
        return
    try:
        account = account_summary(address)
    except Exception as exc:
        telegram_notify(f"[ERROR] gagal baca posisi: {exc}")
        return
    open_for_coin = [p for p in account.get("positions", []) if p.get("coin") == coin]
    if not open_for_coin:
        telegram_notify(f"[{mode}] tidak ada posisi {coin}")
        return
    p = open_for_coin[0]
    if not executor_is_live():
        telegram_notify(f"[{mode}] dry-run: close {coin} simulasi saja")
        return
    sz = abs(float(p.get("szi") or 0))
    side = "buy" if float(p.get("szi") or 0) > 0 else "sell"
    closed = Executor().submit_reduce_close(coin, side, sz)
    ok = closed.get("status") == "ok"
    telegram_notify(f"[{mode}] MANUAL CLOSE {coin} sz={sz} "
                    f"uPnL={float(p.get('unrealized_pnl') or 0):+.4f} "
                    f"{'OK' if ok else 'GAGAL: ' + json.dumps(closed)[:200]}")


def telegram_command_thread(mode):
    """Background thread: long-poll Telegram for /pos /close /status /help."""
    offset = None
    print("telegram command listener: aktif")
    while True:
        updates = poll_updates(timeout=25, offset=offset)
        for u in updates:
            offset = u["update_id"] + 1
            try:
                handle_update(u, mode)
            except Exception as exc:
                traceback.print_exc()


def main():
    cfg = load_config()
    live = executor_is_live() and os.getenv("DRY_RUN", "true").lower() == "false"
    mode = "LIVE" if live else "DRY-RUN"
    print(f"mode={mode} interval={cfg['runtime']['entry_interval_minutes']}m markets={cfg['markets']}")
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        threading.Thread(target=telegram_command_thread, args=(mode,), daemon=True).start()
    while True:
        monitor_positions(cfg, mode)
        cycle_results = []
        for coin in cfg["markets"]:
            try:
                result = cycle(coin, cfg, mode, notify=False)
                cycle_results.append(result)
                print(json.dumps(result, indent=2))
            except Exception as exc:
                traceback.print_exc()
                result = {"coin": coin, "status": "ERROR", "error": str(exc)}
                cycle_results.append(result)
                telegram_notify(f"[ERROR] {coin}: {exc}")

        lines = [f"[{mode}] SCAN {len(cycle_results)} markets"]
        for r in cycle_results:
            sig = r.get("sig", {})
            risk = ",".join(r.get("risk", [])) or "-"
            lines.append(
                f"{r.get('coin')} decision={sig.get('decision', '-')} "
                f"side={sig.get('side') or '-'} conf={sig.get('confidence', 0)} "
                f"status={r.get('status')} risk={risk}")
            if r.get("fill") or r.get("protection"):
                lines.append(f"  fill={r.get('fill', '-')} protection={r.get('protection', '-')}")
        telegram_notify("\n".join(lines))

        time.sleep(int(cfg["runtime"]["entry_interval_minutes"]) * 60)


if __name__ == "__main__":
    main()