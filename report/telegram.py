"""Telegram notifications + inline keyboard reports + command polling."""
import json
import os
import threading
import time
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method, payload, token=None):
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return {"ok": False, "reason": "telegram env not configured"}
    url = API.format(token=token, method=method)
    body = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=body, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.load(response)
    except Exception as exc:  # notifications must never break trading logic
        detail = getattr(exc, "read", lambda: b"")()
        if callable(detail):
            try:
                detail = detail()
            except Exception:
                detail = b""
        return {"ok": False, "reason": f"telegram error: {exc} {detail[:300]}"}


def send(text, token=None, chat_id=None):
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    result = _call("sendMessage", {"chat_id": chat_id, "text": text})
    return {"sent": bool(result.get("ok")), "telegram": result.get("ok"),
            "reason": result.get("reason")}


def send_report(text, buttons=None, token=None, chat_id=None):
    """Send message with optional inline keyboard. buttons = [[(label, callback_data), ...], ...]"""
    chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    result = _call("sendMessage", payload)
    return {"sent": bool(result.get("ok")), "telegram": result.get("ok"),
            "reason": result.get("reason")}


def answer_callback(query_id, text=None):
    return _call("answerCallbackQuery", {"callback_query_id": query_id, "text": text})


def edit_message(chat_id, message_id, text, buttons=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML"}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    return _call("editMessageText", payload)


def scan_report_html(mode, results, rep=None):
    """Build a single informative HTML scan report with position summary."""
    lines = [f"🔍 <b>SCAN {len(results)} MARKET</b> | [{mode}]\n"]
    for r in results:
        sig = r.get("sig", {})
        coin = r.get("coin")
        decision = sig.get("decision", "-")
        conf = sig.get("confidence", 0)
        status = r.get("status")
        icon = {"open": "🟢", "close": "🔴", "skip": "⚪️", "hold": "🟡"}.get(decision, "❓")
        st = {"FILLED": "✅", "NO_FILL": "⏳", "REJECTED": "⏸️", "CLOSED": "🔻", "ERROR": "🚨"}.get(status, "•")
        if status == "ERROR":
            lines.append(f"{icon} <b>{coin}</b>\n   {st} Error: <code>{r.get('error', '')[:80]}</code>")
            continue
        conf_bar = "🟩" * max(1, int(conf * 5)) + "▫️" * (5 - max(1, int(conf * 5)))
        reason = (sig.get("reason") or "").strip()
        risk = ", ".join(r.get("risk", []))
        entry = (f"\n   ↳ <b>{sig.get('side', '').upper()}</b> @ {r.get('entry_px', '-')} | fill: {r.get('fill', '-')} | TP/SL: {r.get('protection', '-')}") if r.get("fill") or r.get("protection") else ""
        lines.append(
            f"{icon} <b>{coin}</b> → {decision} ({st} {status})\n"
            f"   Conf: {conf_bar} {conf:.2f}\n"
            f"   Alas: {reason[:120]}\n"
            f"   Risk: {risk or '-'}{entry}")
    if rep:
        free = float(rep.get("free_collateral", 0))
        total = sum(float(p.get("u_pnl") or 0) for p in rep["positions"])
        pos = ", ".join(f"{p['coin']} {p['side']} {float(p.get('u_pnl') or 0):+.2f}" for p in rep["positions"]) or "tidak ada"
        lines.append(f"\n📊 <b>Posisi:</b> {pos}\n💵 Free: {free:.2f} | PnL: {total:+.2f}")
    return "\n".join(lines)


def format_decision(coin, decision, mode="DRY-RUN"):
    reason = str(decision.get("reason", ""))[:300]
    return f"[{mode}] {coin}\nDecision: {position_emoji(decision.get('decision'))} {decision.get('decision')}\nSide: {side_emoji(decision.get('side'))} {decision.get('side') or '-'}\nConfidence: {decision.get('confidence', 0)}\nReason: {reason}"


def position_emoji(d):
    return {"open": "🟢", "close": "🔴", "skip": "⚪️", "hold": "🟡"}.get(d, "⚪️")


def side_emoji(s):
    return {"buy": "📈", "sell": "📉"}.get(s, "")


def pnl_emoji(pnl):
    try:
        return "🟢" if float(pnl) >= 0 else "🔴"
    except (TypeError, ValueError):
        return "⚪️"


def format_positions_html(rep):
    """rep = position_report() output from main.py"""
    total_pnl = sum(float(r.get("u_pnl") or 0) for r in rep["positions"])
    acv = float(rep.get("account_value") or 0)
    lines = [f"📊 <b>PORTOFOLIO</b>  |  💵 {acv:.2f} USDC\n"]
    if not rep["positions"]:
        lines.append("📭 Belum ada posisi.\n⏳ Menunggu sinyal entry berikutnya...")
        return "\n".join(lines)
    lines[0] = (
        f"📊 <b>PORTOFOLIO</b>  |  💵 {acv:.2f} USDC\n"
        f"📈 Posisi: <b>{rep['open_positions']}</b>  |  "
        f"🎯 Equity PnL: <b>{total_pnl:+.4f} USDC</b> {pnl_emoji(total_pnl)}\n"
        f"{'─' * 22}")
    for r in rep["positions"]:
        pnl = float(r["u_pnl"] or 0)
        entry = float(r["entry_px"] or 0)
        sz = abs(float(r["size"] or 0))
        mark = float(r.get("mark_px") or entry)
        margin = float(r["margin_used"] or 0)
        roe = (pnl / margin * 100) if margin else 0
        move = ((mark - entry) / entry * 100) if entry else 0
        arrow = "🟢" if pnl >= 0 else "🔴"
        held = f"{r['held_minutes']}m" if r["held_minutes"] is not None else "?"
        liq = r.get("liq_px")
        liq_txt = f" | 💀 {float(liq):.4g}" if liq else ""
        lines.append(
            f"\n{arrow} <b>{r['coin']}</b> {r['side'].upper()} x{sz:g}\n"
            f"   ↳ Entry <code>{entry:.4g}</code> → Mark <code>{mark:.4g}</code> "
            f"({move:+.2f}%)\n"
            f"   💰 PnL: <b>{pnl:+.4f} USDC</b> ({roe:+.1f}% ROE)\n"
            f"   ⏱ {held} | 🧱 {margin:.2f} | 📦 {entry * sz:.2f} USDC{liq_txt}\n"
            f"   🎯 TP <code>{r['tp'] or '-'}</code> | 🛑 SL <code>{r['sl'] or '-'}</code>")
    lines.append(f"\n💵 Free: <b>{rep['free_collateral']:.2f} USDC</b>")
    return "\n".join(lines)


def close_buttons(positions, refresh=True):
    """Build inline keyboard rows: close per position + optional hard refresh."""
    rows = []
    for r in positions:
        rows.append([{"text": "❌ Close " + r["coin"], "callback_data": "close:" + r["coin"]}])
    if refresh:
        rows.append([{"text": "🔄 Refresh", "callback_data": "refresh"}])
    return rows


def poll_updates(token=None, timeout=25, offset=None):
    """Long-poll getUpdates. Returns list of updates."""
    token = token or os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        return []
    url = API.format(token=token, method="getUpdates")
    payload = {"timeout": timeout}
    if offset:
        payload["offset"] = offset
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(), method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as response:
            data = json.load(response)
        return data.get("result", [])
    except Exception:
        return []


if __name__ == "__main__":
    print("telegram notifier loaded; no message sent")
# ponytail: notifications only; add control callbacks only after auth and two-step confirmation are specified.
