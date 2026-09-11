"""Main loop combining all phases. Default dry-run; no live execution."""
import os, sys, time, json, traceback, threading
from pathlib import Path

from core.resolver import resolve_market
from core.market_data import book_view, candles_snapshot, account_summary, tick_decimals, exchange_protection
from strategy.decision import load_config, fallback_decision, validate_ai_decision, build_prompt_context
from strategy.ai import decide, position_ai_decision, ai_configured
from strategy.ai import recent_context as ai_recent_context
from strategy.indicators.price_action import closed_candles
from strategy.indicators.price_action import candle_ohlc
from core.risk import check as risk_check
from core.planner import build_entry, simulate, as_dict
from core.executor import Executor, is_live as executor_is_live
from hyperliquid.utils.types import Cloid
from core.reconciler import reconcile, load_local, save_local
from report.telegram import (send, send_report, format_decision, format_positions_html,
                      close_buttons, poll_updates, answer_callback, scan_report_html,
                      edit_message, esc)
from report.learning import record as learning_record
from core.protection import prices as protection_prices
from core.env import load_env as executor_load_env

executor_load_env()
CYCLE_INTERVAL = 10 * 60
SCREENING_INTERVAL_SECONDS = 20 * 60
POSITION_MONITOR_TICK_SECONDS = 60
_close_lock = threading.Lock()
_position_last_candle = {}
_position_close_lock = threading.Lock()  # prevents screening/monitor double-close races
_position_last_report = 0.0


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


def set_position_meta(meta):
    POSITIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    POSITIONS_FILE.write_text(json.dumps(meta, indent=2))


def position_report(account):
    meta = load_position_meta()
    addr = os.getenv("HL_ACCOUNT_ADDRESS")
    exch_prot = []
    if addr:
        try:
            exch_prot = exchange_protection(addr, dex="io")
        except Exception:
            exch_prot = []
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
        # TP/SL from live exchange reduce-only orders (truth), not local meta.
        tp = sl = None
        levels = [o["limit_px"] for o in exch_prot if o.get("coin") == coin]
        if levels:
            above = sorted([x for x in levels if x >= entry])
            below = sorted([x for x in levels if x < entry])
            if szi > 0:  # long: TP above, SL below
                tp = above[-1] if above else None
                sl = below[0] if below else None
            else:        # short: TP below, SL above
                tp = below[0] if below else None
                sl = above[-1] if above else None
        if tp is None: tp = m.get("tp")
        if sl is None: sl = m.get("sl")
        rows.append({"coin": coin, "side": "long" if szi > 0 else "short",
                     "size": szi, "entry_px": entry, "mark_px": px,
                     "u_pnl": pnl, "u_pnl_exch": p.get("unrealized_pnl"),
                     "margin_used": p.get("margin_used"),
                     "liq_px": p.get("liquidation_px"),
                     "held_minutes": held_min, "tp": tp, "sl": sl})
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


def ensure_live_protection(cfg, account):
    """Every open position MUST have full-size native TP/SL. Repair if missing/undersized."""
    if not executor_is_live():
        return
    executor = Executor()
    pos_cfg = cfg["position"]
    tp_pct = str(pos_cfg["take_profit_pct"])
    sl_pct = str(pos_cfg["stop_loss_pct"])
    leverage = int(pos_cfg.get("leverage", 1))
    try:
        prot = exchange_protection(os.getenv("HL_ACCOUNT_ADDRESS"), dex="io")
    except Exception as exc:
        print(f"ensure_live_protection: cannot read exchange orders: {exc}")
        return
    for p in account.get("positions", []):
        coin = p.get("coin")
        szi = abs(float(p.get("szi") or 0))
        if szi <= 0:
            continue
        entry = float(p.get("entry_px") or 0)
        orders = [o for o in prot if o.get("coin") == coin]
        correct_tp = [o for o in orders if o["limit_px"] >= entry and abs(o["sz"] - szi) < 1e-6]
        correct_sl = [o for o in orders if o["limit_px"] < entry and abs(o["sz"] - szi) < 1e-6]
        if correct_tp and correct_sl and len(orders) == 2:
            continue  # exactly one full-size TP above + one full-size SL below
        side = "buy" if float(p.get("szi") or 0) > 0 else "sell"
        intent = {"coin": coin, "side": side, "size": str(szi),
                  "take_profit_pct": tp_pct, "stop_loss_pct": sl_pct,
                  "leverage": leverage}
        out = executor.submit_protection(intent, entry, price_decimals=tick_decimals(coin))
        print(json.dumps({"action": "auto_repair_protection", "coin": coin,
                          "position_sz": szi, "orders": orders, "result": out}))


def _position_ai_tick(cfg, mode, account):
    """Run Position AI once per newly closed 15m candle; close remains reduce-only."""
    global _position_last_candle
    if cfg.get("strategy") != "ai" or cfg.get("ai_mode") != "primary":
        return
    for p in position_report(account).get("positions", []):
        coin = p["coin"]
        try:
            candles = candles_snapshot(coin)
            closed = closed_candles(candles)
            if not closed:
                continue
            candle_id = closed[-1].get("T", closed[-1].get("t"))
            if candle_id is None or candle_id == _position_last_candle.get(coin):
                continue
            timing = candles_snapshot(coin, interval="5m", lookback_minutes=60)
            market = resolve_market(coin)
            book = book_view(coin)
            sig = position_ai_decision(coin, p, market, book, candles, timing, cfg,
                                       account=account, context=ai_recent_context(coin))
            _position_last_candle[coin] = candle_id
            row = {"type": "position_decision", "strategy": "ai_position", "coin": coin,
                   "decision": sig["decision"], "confidence": sig["confidence"],
                   "reason": sig["reason"], "u_pnl": p.get("u_pnl"),
                   "entry_px": p.get("entry_px"), "mark_px": p.get("mark_px"),
                   "held_minutes": p.get("held_minutes"), "candle_id": candle_id}
            learning_record(row)
            print(json.dumps({"position_pipeline": True, **row,
                              "hard_tp": p.get("tp"), "hard_sl": p.get("sl"),
                              "executor_eligible": sig["decision"] == "close"}))
            telegram_notify_html(
                f"📊 <b>POSITION AI</b>\n<b>{esc(coin)}</b> {esc(str(p['side']).upper())}\n"
                f"Decision: <b>{esc(sig['decision'].upper())}</b> | uPnL: {float(p.get('u_pnl') or 0):+.4f}\n"
                f"Reason: {esc(sig['reason'])}\nHard TP/SL: {esc(str(p.get('tp') or '-'))} / {esc(str(p.get('sl') or '-'))}\n"
                f"Action: {esc('reduce-only close' if sig['decision'] == 'close' else 'keep position')}")
            if sig["decision"] != "close":
                continue
            # Re-read state under one lock. Never reverse, add, or close stale size.
            with _position_close_lock:
                fresh = account_summary(os.getenv("HL_ACCOUNT_ADDRESS"))
                live_pos = next((x for x in fresh.get("positions", []) if x.get("coin") == coin), None)
                if not live_pos:
                    continue
                size = abs(float(live_pos.get("szi") or 0))
                if size <= 0:
                    continue
                side = "buy" if float(live_pos.get("szi") or 0) > 0 else "sell"
                if not executor_is_live():
                    close_result = {"status": "SIMULATED", "would_call_exchange": False,
                                    "would_sign": False, "reduce_only": True}
                else:
                    close_result = Executor().submit_reduce_close(coin, side, size)
                learning_record({"type": "position_close_intent", "strategy": "ai_position",
                                 "coin": coin, "decision": "close", "reason": sig["reason"],
                                 "pnl": live_pos.get("unrealized_pnl"),
                                 "close_status": close_result.get("status"),
                                 "simulated": not executor_is_live()})
                print(json.dumps({"position_close": True, "coin": coin,
                                  "result": close_result, "no_order": not executor_is_live()}))
        except Exception as exc:
            print(f"[position_ai] ERROR for {coin}: {type(exc).__name__}: {exc}")
            learning_record({"type": "position_error", "strategy": "ai_position",
                             "coin": coin, "reason": f"{type(exc).__name__}: {exc}"})
            _position_last_candle[coin] = locals().get("candle_id", _position_last_candle.get(coin))


def monitor_positions(cfg, mode):
    """Tick every minute: native protection + Position AI on each new 15m close."""
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    if not address:
        return
    account = account_summary(address)
    meta = load_position_meta()
    executor = Executor() if executor_is_live() else None
    max_hold = int(cfg["position"].get("max_hold_minutes", 0))
    ensure_live_protection(cfg, account)
    if account.get("positions"):
        _position_ai_tick(cfg, mode, account)
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
    report_positions(mode, account)


def cycle(coin, cfg, mode, notify=True):
    meta = resolve_market(coin)
    book = book_view(coin)
    candles = candles_snapshot(coin)
    try:
        timing_candles = candles_snapshot(coin, interval="5m", lookback_minutes=60)
    except Exception:
        # Missing 5m data must fail closed after any primary open signal.
        timing_candles = []
    address = os.getenv("HL_ACCOUNT_ADDRESS")
    account = account_summary(address) if address else {"free_collateral": 0, "positions": []}
    strategy_mode = cfg.get("strategy")
    ai_mode = cfg.get("ai_mode")
    sig, decision_source = decide(coin, meta, book, candles, cfg, account,
                                  timing_candles=timing_candles)
    sig["coin"] = coin  # risk gate per-coin position check
    ai_called = decision_source in ("ai", "ai_primary", "ai_veto", "ai_veto_agree",
                                    "ai_veto_error", "ai_primary_error")
    rd = risk_check(sig, meta, book, account, cfg)
    print(json.dumps({"pipeline": True, "coin": coin, "strategy": strategy_mode,
                      "ai_mode": ai_mode, "ai_called": ai_called,
                      "ai_decision": sig.get("decision"), "ai_side": sig.get("side"),
                      "validator_source": decision_source,
                      "risk_allowed": rd.allowed, "risk_reasons": list(rd.reasons),
                      "final_decision": sig.get("decision") if rd.allowed else "REJECTED",
                      "executor_eligible": bool(rd.allowed and sig.get("decision") == "open"),
                      "fail_closed_reason": (None if rd.allowed else list(rd.reasons)) or
                                            (sig.get("reason") if sig.get("decision") != "open" else None)}))
    if not rd.allowed:
        result = {"coin": coin, "sig": sig, "risk": rd.reasons, "status": "REJECTED",
                  "decision_source": decision_source,
                  "positions": position_report(account)}
        learning_record({"type": "decision", "coin": coin, "decision": sig.get("decision"),
                         "side": sig.get("side"), "confidence": sig.get("confidence"),
                         "reason": sig.get("reason"), "risk": rd.reasons,
                         "source": decision_source})
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
            m = load_position_meta()
            opened = m.get(coin, {})
            learning_record({"type": "close", "coin": coin, "pnl": float(p.get("unrealized_pnl") or 0),
                             "entry_px": opened.get("entry_px", p.get("entry_px")),
                             "reason": "ai:" + str(sig.get("reason", ""))[:120],
                             "close_status": closed.get("status"),
                             "held_minutes": int((time.time() - opened["opened_at"]) / 60) if opened.get("opened_at") else None})
            m.pop(coin, None)
            set_position_meta(m)
            telegram_notify(f"[{mode}] CLOSED {coin} uPnL={float(p.get('unrealized_pnl') or 0):+.4f}")
            return result

    intent = build_entry(coin, sig, meta, book, cfg)
    intent_dict = as_dict(intent)
    intent_dict["max_leverage"] = meta.get("max_leverage")

    if not executor_is_live():
        result = Executor().submit_entry(intent_dict)
        result["coin"] = coin
        result["sig"] = sig
        result["decision_source"] = decision_source
        result["protection"] = "SKIPPED_DRYRUN"
        return result

    executor = Executor()
    result = executor.submit_entry(intent_dict)
    result["coin"] = coin
    result["sig"] = sig
    result["decision_source"] = decision_source
    result["protection"] = "SKIPPED_NO_FILL"

    filled, status = response_fill(result)
    if not filled and "error" not in status:
        filled, status = confirm_fill(executor, intent_dict)
    result["fill"] = "confirmed" if filled else "not_confirmed"
    if not filled:
        result["status"] = "NO_FILL"
        result["protection"] = "SKIPPED_NO_FILL"
        learning_record({"type": "no_fill", "coin": coin, "side": sig.get("side"),
                         "reason": str(sig.get("reason", ""))[:150]})
        return result

    result["status"] = "FILLED"
    filled_obj = status.get("filled", {})
    entry_px = float(filled_obj.get("avgPx") or intent_dict["price"] or 0)
    if entry_px <= 0:
        entry_px = float(intent_dict["price"])
    result["entry_px"] = entry_px
    # Protect the ACTUAL filled size, not the request size (IOC can partial-fill).
    actual_sz = filled_obj.get("totalSz")
    if actual_sz:
        intent_dict["size"] = str(actual_sz)
    learning_record({"type": "open", "coin": coin, "side": sig.get("side"),
                     "entry_px": entry_px, "size": intent_dict.get("size"),
                     "confidence": sig.get("confidence"), "reason": str(sig.get("reason", ""))[:150],
                     "source": decision_source, "protection": "pending"})
    # Submit native reduce-only TP/SL trigger orders on the live position.
    price_decimals = tick_decimals(coin)
    prot = executor.submit_protection(intent_dict, entry_px, price_decimals=price_decimals)
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
        elif data == "refresh":
            rep = current_positions_report()
            if rep:
                edit_message(incoming_chat, cbq["message"]["message_id"],
                             format_positions_html(rep), close_buttons(rep["positions"]))


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
    m = load_position_meta()
    opened = m.get(coin, {})
    learning_record({"type": "close", "coin": coin, "pnl": float(p.get("unrealized_pnl") or 0),
                     "entry_px": opened.get("entry_px"), "reason": "manual_telegram",
                     "close_status": closed.get("status"),
                     "held_minutes": int((time.time() - opened["opened_at"]) / 60) if opened.get("opened_at") else None})
    if ok:
        m.pop(coin, None); set_position_meta(m)
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


def screening_cycle(cfg, mode):
    """20m screening pass: scan all markets, then one scan report."""
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
    rep = current_positions_report()
    telegram_notify_html(scan_report_html(mode, cycle_results, rep))


def main():
    cfg = load_config()
    live = bool(cfg.get("runtime", {}).get("dry_run") is False) and executor_is_live() and os.getenv("DRY_RUN", "true").lower() == "false"
    mode = "LIVE" if live else "DRY-RUN"
    print(f"mode={mode} screening={SCREENING_INTERVAL_SECONDS // 60}m "
          f"position_tick={POSITION_MONITOR_TICK_SECONDS}s markets={cfg['markets']}")
    if os.getenv("TELEGRAM_BOT_TOKEN"):
        threading.Thread(target=telegram_command_thread, args=(mode,), daemon=True).start()
    next_screening = 0.0
    while True:
        now = time.time()
        try:
            monitor_positions(cfg, mode)
        except Exception:
            traceback.print_exc()
        if now >= next_screening:
            try:
                screening_cycle(cfg, mode)
            except Exception:
                traceback.print_exc()
            next_screening = time.time() + SCREENING_INTERVAL_SECONDS
        time.sleep(POSITION_MONITOR_TICK_SECONDS)


if __name__ == "__main__":
    main()