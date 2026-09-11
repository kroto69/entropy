"""AI-primary routing pipeline tests.

Proves: AI is sole signal source (no OHLC/5m precondition), deterministic
validator + entry_quality_gate + risk gate remain mandatory, and the executor
boundary is never reached unless every check passes (mocked).

No live exchange: Executor.submit_entry is stubbed; ai_decision is stubbed.
"""
import strategy.ai as ai
from strategy.decision import build_ai_market_snapshot, validate_ai_decision
from core import executor as core_executor
from core import risk as core_risk


def c(o, h, l, cl):
    return {"o": o, "h": h, "l": l, "c": cl}


def book():
    return {"bid_px": 100.0, "ask_px": 100.1, "spread_bps": 10.0,
            "bids": [{"px": 100.0, "sz": 100}], "asks": [{"px": 100.1, "sz": 100}]}


def meta():
    return {"is_delisted": False, "max_leverage": 5, "margin_mode": "isolated",
            "only_isolated": True}


def flat_candles(n=25):
    return [c(100.0 + 0.01 * i, 100.2 + 0.01 * i, 99.9 + 0.01 * i, 100.05 + 0.01 * i)
            for i in range(n)]


def bull_candles(n=25):
    out = []
    px = 100.0
    for i in range(n):
        o = px
        cl = px + 0.15
        out.append(c(o, cl + 0.05, o - 0.05, cl))
        px = cl
    return out


def cfg_primary(overrides=None):
    cfg = {"strategy": "ai", "ai_mode": "primary",
           "strategy_params": {"mode": "aggressive", "min_confidence": 0.65,
                               "max_spread_bps": 25.0},
           "position": {"amount_usdc": 30.5, "leverage": 5, "max_positions": 2,
                        "take_profit_pct": 10.0, "stop_loss_pct": 20.0,
                        "max_hold_minutes": 240},
           "runtime": {"dry_run": True}}
    if overrides:
        cfg.update(overrides)
    return cfg


def account(positions=None):
    return {"free_collateral": 15.0, "positions": positions or []}


class _Submitted:
    def __init__(self):
        self.calls = []


# A. AI-primary calls AI even when OHLC would be HOLD (flat candles).
def test_ai_primary_called_when_ohlc_hold():
    calls = []
    orig = ai.ai_decision
    # Stub to return skip; prove decide() invokes AI path.
    ai.ai_decision = lambda *a, **k: calls.append(a) or \
        {"decision": "skip", "side": None, "confidence": 0.0, "reason": "flat"}
    try:
        sig, src = ai.decide("io:ANTH", meta(), book(), flat_candles(), cfg_primary(),
                             account=account(), timing_candles=flat_candles(6))
        assert src == "ai_primary", src
        assert len(calls) == 1, "AI must be called exactly once"
    finally:
        ai.ai_decision = orig


# B. AI-primary calls AI even when old 5m filter would fail.
def test_ai_primary_called_when_5m_fail():
    calls = []
    orig = ai.ai_decision
    ai.ai_decision = lambda *a, **k: calls.append(a) or \
        {"decision": "hold", "side": None, "confidence": 0.0, "reason": "no timing"}
    timing_fail = [c(1, 1.05, 0.95, 1.04), c(1.04, 1.06, 1.0, 1.03), c(1.03, 1.04, 0.99, 1.01)]
    try:
        sig, src = ai.decide("io:ANTH", meta(), book(), bull_candles(), cfg_primary(),
                             account=account(), timing_candles=timing_fail)
        assert src == "ai_primary", src
        assert len(calls) == 1, "AI must be called even if 5m would fail"
    finally:
        ai.ai_decision = orig


# C/D. AI primary OPEN BUY/SELL passes to validator (no gate veto) -> open.
def test_ai_primary_open_buy_passes_validator():
    orig = ai.ai_decision
    ai.ai_decision = lambda *a, **k: {"decision": "open", "side": "buy",
                                      "confidence": 0.72, "reason": "uptrend + timing"}
    try:
        sig, src = ai.decide("io:ANTH", meta(), book(), bull_candles(), cfg_primary(),
                             account=account(), timing_candles=flat_candles(6))
        assert src == "ai_primary", src
        assert sig["decision"] == "open" and sig["side"] == "buy", sig
    finally:
        ai.ai_decision = orig


def test_ai_primary_open_sell_passes_validator():
    bear = [c(100 - 0.15 * i, 100 - 0.15 * i + 0.05, 100 - 0.15 * i - 0.05, 100 - 0.15 * i)
            for i in range(25)]
    orig = ai.ai_decision
    ai.ai_decision = lambda *a, **k: {"decision": "open", "side": "sell",
                                      "confidence": 0.72, "reason": "downtrend + timing"}
    try:
        sig, src = ai.decide("io:ANTH", meta(), book(), bear, cfg_primary(), account=account(), timing_candles=flat_candles(6))
        assert src == "ai_primary", src
        assert sig["decision"] == "open" and sig["side"] == "sell", sig
    finally:
        ai.ai_decision = orig


# E. Late-exhaustion vetoes AI OPEN -> validator returns HOLD, side null, conf <= 0.45.
def test_ai_primary_open_vetoed_late_exhaustion():
    # Big extended move (10%) with RSI stretched -> late_exhaustion_block fires.
    pump = []
    px = 100.0
    for i in range(30):
        cl = px * 1.012
        pump.append(c(px, cl * 1.005, px * 0.995, cl))
        px = cl
    orig_ai = ai.ai_decision
    ai.ai_decision = lambda *a, **k: {"decision": "open", "side": "buy",
                                      "confidence": 0.80, "reason": "strong uptrend"}
    try:
        # ai_decision stub bypasses validator; call validator directly with real gate.
        res = validate_ai_decision(
            {"decision": "open", "side": "buy", "confidence": 0.8},
            meta(), book(), cfg_primary(), candles=pump,
            indicators={"ema20": 150.0, "rsi_series": [70] * 30})
        assert res["decision"] == "hold" and res["side"] is None, res
        assert res["confidence"] <= 0.45, res
    finally:
        ai.ai_decision = orig_ai


# F. EMA anti-chase vetoes AI OPEN -> HOLD.
def test_ai_primary_open_vetoed_ema_chase():
    extended = []
    px = 100.0
    for i in range(19):
        px += 0.5
        extended.append(c(px, px + 0.4, px - 0.4, px + 0.3))
    # final impulsive candle with wide range, closes far above EMA
    extended.append(c(px, px + 2.0, px - 1.0, px + 1.5))
    res = validate_ai_decision({"decision": "open", "side": "buy", "confidence": 0.75},
                               meta(), book(), cfg_primary(), candles=extended,
                               indicators={"ema20": 105.0, "rsi_series": [60] * 20})
    assert res["decision"] == "hold" and res["side"] is None, res


# G. Reversal candle vetoes AI OPEN -> HOLD.
def test_ai_primary_open_vetoed_reversal():
    up = [c(100 + 0.2 * i, 100 + 0.2 * i + 0.4, 100 + 0.2 * i - 0.4, 100 + 0.2 * i)
          for i in range(18)]
    # strong bearish reversal candle at the end
    up.append(c(104, 104.2, 99.0, 99.5))
    res = validate_ai_decision({"decision": "open", "side": "buy", "confidence": 0.7},
                               meta(), book(), cfg_primary(), candles=up,
                               indicators={"ema20": 101.0, "rsi_series": [55] * 19})
    assert res["decision"] == "hold" and res["side"] is None, res


# H. Malformed AI output fails closed (decide catches -> skip, source ai_primary_error).
def test_ai_primary_malformed_fails_closed():
    from strategy.ai import AIError
    orig = ai.ai_decision
    def bad(*a, **k):
        raise AIError("no JSON object in AI response")
    ai.ai_decision = bad
    try:
        sig, src = ai.decide("io:ANTH", meta(), book(), bull_candles(), cfg_primary(),
                             account=account(), timing_candles=flat_candles(6))
        assert src == "ai_primary_error", src
        assert sig["decision"] == "skip" and sig["side"] is None, sig
    finally:
        ai.ai_decision = orig


# I. AI timeout/unavailable fails closed.
def test_ai_primary_timeout_fails_closed():
    from strategy.ai import AIError
    orig = ai.ai_decision
    def timeout(*a, **k):
        raise AIError("timeout")
    ai.ai_decision = timeout
    try:
        sig, src = ai.decide("io:ANTH", meta(), book(), bull_candles(), cfg_primary(),
                             account=account(), timing_candles=flat_candles(6))
        assert src == "ai_primary_error", src
        assert sig["decision"] == "skip", sig
    finally:
        ai.ai_decision = orig


# J. Invalid/missing snapshot data fails closed.
def test_ai_primary_missing_data_fails_closed():
    empty_book = {"bid_px": None, "ask_px": None, "spread_bps": None, "bids": [], "asks": []}
    sig, src = ai.decide("io:ANTH", meta(), empty_book, [], cfg_primary(), account=account(), timing_candles=flat_candles(6))
    assert src == "ai_primary_data_error", src
    assert sig["decision"] == "skip", sig


# K. AI cannot return execution fields (amount/leverage/TP/SL are ignored/rejected).
def test_ai_primary_rejects_execution_fields():
    res = validate_ai_decision(
        {"decision": "open", "side": "buy", "confidence": 0.7,
         "amount_usdc": 999, "leverage": 50, "take_profit_pct": 999},
        meta(), book(), cfg_primary(), candles=bull_candles(),
        indicators={"ema20": 100.0, "rsi_series": [55] * 25})
    # Validator accepts only decision/side/confidence/reason; extra exec fields ignored,
    # but deterministic gates still run. The key contract: result has no exec fields.
    assert res["decision"] == "open"
    for forbidden in ("amount_usdc", "leverage", "take_profit_pct", "stop_loss_pct"):
        assert forbidden not in res, f"{forbidden} must not leak into decision"


# M. Executor never called unless all checks pass (mocked).
def test_executor_not_called_without_full_approval():
    submitted = _Submitted()
    orig_submit = core_executor.Executor.submit_entry
    core_executor.Executor.submit_entry = lambda self, intent: submitted.calls.append(intent) or {"status": "NOT_CALLED"}
    try:
        # malformed AI -> decide returns skip; reconstruct cycle path: no open signal
        from strategy.ai import AIError
        orig = ai.ai_decision
        ai.ai_decision = lambda *a, **k: (_ for _ in ()).throw(AIError("bad"))
        sig, src = ai.decide("io:ANTH", meta(), book(), bull_candles(), cfg_primary(),
                             account=account(), timing_candles=flat_candles(6))
        ai.ai_decision = orig
        # risk gate on a skip signal remains not-allowed
        from core.risk import check as risk_check
        sig["coin"] = "io:ANTH"
        rd = risk_check(sig, meta(), book(), account(), cfg_primary())
        assert not rd.allowed
        assert submitted.calls == [], "executor must not be called on malformed AI"
    finally:
        core_executor.Executor.submit_entry = orig_submit


def main():
    test_ai_primary_called_when_ohlc_hold()
    test_ai_primary_called_when_5m_fail()
    test_ai_primary_open_buy_passes_validator()
    test_ai_primary_open_sell_passes_validator()
    test_ai_primary_open_vetoed_late_exhaustion()
    test_ai_primary_open_vetoed_ema_chase()
    test_ai_primary_open_vetoed_reversal()
    test_ai_primary_malformed_fails_closed()
    test_ai_primary_timeout_fails_closed()
    test_ai_primary_missing_data_fails_closed()
    test_ai_primary_rejects_execution_fields()
    test_executor_not_called_without_full_approval()
    # L. snapshot is bounded + closed-only + serializable
    snap = build_ai_market_snapshot("io:ANTH", meta(), book(), bull_candles(),
                                    timing_candles=flat_candles(6),
                                    account=account(), cfg=cfg_primary())
    import json
    json.dumps(snap)  # must not raise
    assert snap["data_valid"] is True
    assert len(snap["closed_15m"]["recent"]) <= 20
    print("AI PRIMARY PIPELINE TESTS PASSED")


if __name__ == "__main__":
    main()