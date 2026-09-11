"""One complete read-only aggressive OHLC + 5m + AI veto cycle."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.market_data import candles_snapshot, book_view
from core.resolver import resolve_market
from strategy.ai import ai_decision, decide as ai_route
from strategy.decision import load_config
from strategy.indicators.price_action import closed_candles
from strategy.strategies.ohlc import decide as ohlc_decide, check_5m_timing_filter


def redact_payload(payload):
    payload = dict(payload)
    for msg in payload.get("messages", []):
        if msg.get("role") == "user":
            try:
                ctx = json.loads(msg.get("content", "{}"))
                if "account" in ctx:
                    acct = dict(ctx["account"] or {})
                    acct.pop("address", None)
                    ctx["account"] = acct
                msg["content"] = json.dumps(ctx, separators=(",", ":"))
            except Exception:
                pass
    return payload


def main():
    coin = "io:ANTH"
    cfg = load_config()
    assert cfg["strategy"] == "ohlc"
    assert cfg["ai_mode"] == "veto"
    assert cfg["strategy_params"]["mode"] == "aggressive"

    meta = resolve_market(coin)
    book = book_view(coin)
    candles = candles_snapshot(coin)
    timing = candles_snapshot(coin, interval="5m", lookback_minutes=60)
    params = cfg["strategy_params"]

    raw = ohlc_decide(candles, params)
    timing_result = None
    if raw.get("decision") == "open":
        passed, reason = check_5m_timing_filter(timing, raw["side"], mode="aggressive")
        timing_result = {"passed": passed, "reason": reason}

    out = {
        "config": {"strategy": cfg["strategy"], "ai_mode": cfg["ai_mode"],
                   "strategy_params": params, "dry_run": cfg["runtime"]["dry_run"]},
        "coin": coin,
        "ohlc_aggressive_raw": raw,
        "5m_filter": timing_result,
        "ai_called": False,
        "ai_request_payload": None,
        "ai_response": None,
        "ai_result": "not_called",
        "entry_quality_gate": "not_reached",
        "final_decision": None,
        "order_sent": False,
    }

    if raw.get("decision") != "open" or not timing_result or not timing_result["passed"]:
        out["final_decision"] = {
            "decision": "hold" if raw.get("decision") == "open" else raw.get("decision"),
            "side": None,
            "confidence": min(float(raw.get("confidence", 0)), 0.45) if raw.get("decision") == "open" else raw.get("confidence", 0),
            "reason": "5m timing filter failed" if raw.get("decision") == "open" else raw.get("reason"),
        }
        print(json.dumps(out, indent=2, default=str))
        return

    # Build/log exact request shape without exposing bearer token.
    from strategy.decision import build_prompt_context
    from strategy.ai import SYSTEM_PROMPT
    import os
    import json as _json
    from report.learning import read_all
    ctx = build_prompt_context(coin, meta, book, candles,
                               {"free_collateral": 0, "positions": []}, None)
    ctx["active_strategy"] = cfg["strategy"]
    ctx["strategy_params"] = params
    payload = {
        "model": os.getenv("AI_MODEL"),
        "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                     {"role": "user", "content": _json.dumps(ctx)}],
        "temperature": 0.2,
        "max_tokens": 200,
    }
    out["ai_called"] = True
    out["ai_request_payload"] = redact_payload(payload)

    try:
        ai = ai_decision(coin, meta, book, candles, cfg,
                         account={"free_collateral": 0, "positions": []},
                         context=[])
        out["ai_response"] = ai
        agreed = ai.get("decision") == "open" and ai.get("side") == raw.get("side")
        out["ai_result"] = "agreed" if agreed else "vetoed"
        out["entry_quality_gate"] = "passed by AI validator" if agreed else "not reached: AI veto"
        out["final_decision"] = ai if agreed else {
            "decision": "hold", "side": None, "confidence": 0.0,
            "reason": "AI veto: disagreement with OHLC candidate",
        }
    except Exception as exc:
        out["ai_response"] = {"error_type": type(exc).__name__, "error": str(exc)}
        out["ai_result"] = "unavailable/error"
        out["entry_quality_gate"] = "not reached: AI unavailable"
        out["final_decision"] = {
            "decision": "hold", "side": None, "confidence": 0.0,
            "reason": "AI veto: unavailable",
        }

    print(json.dumps(out, indent=2, default=str))


if __name__ == "__main__":
    main()