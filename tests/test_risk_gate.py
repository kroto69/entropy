"""Risk gate decision-aware validation regression."""
from core.risk import check

CFG = {
    "strategy": {"min_confidence": 0.65, "max_spread_bps": 25.0},
    "position": {"amount_usdc": 30.5, "leverage": 5, "max_positions": 2},
}
MARKET = {"is_delisted": False, "only_isolated": True}
BOOK = {"spread_bps": 10.0}
ACCOUNT = {"free_collateral": 100.0, "positions": []}


def main():
    # A. HOLD side=null conf=0.45 -> decision_not_open only
    r = check({"decision": "hold", "side": None, "confidence": 0.45},
              MARKET, BOOK, ACCOUNT, CFG)
    assert r.reasons == ("decision_not_open",), r
    assert "invalid_side" not in r.reasons and "low_confidence" not in r.reasons, r

    # B. SKIP side=null conf=0.0 -> decision_not_open only
    r = check({"decision": "skip", "side": None, "confidence": 0.0},
              MARKET, BOOK, ACCOUNT, CFG)
    assert r.reasons == ("decision_not_open",), r
    assert "invalid_side" not in r.reasons and "low_confidence" not in r.reasons, r

    # HOLD/SKIP/CLOSE even with conf below min -> never low_confidence
    for decision in ("hold", "skip", "close"):
        r = check({"decision": decision, "side": None, "confidence": 0.1},
                  MARKET, BOOK, ACCOUNT, CFG)
        assert "low_confidence" not in r.reasons, (decision, r)
        assert "invalid_side" not in r.reasons, (decision, r)
        assert "decision_not_open" in r.reasons, (decision, r)

    # C. OPEN side=null -> invalid_side still reported
    r = check({"decision": "open", "side": None, "confidence": 0.8},
              MARKET, BOOK, ACCOUNT, CFG)
    assert "invalid_side" in r.reasons, r

    # D. OPEN conf below min -> low_confidence still reported
    r = check({"decision": "open", "side": "buy", "confidence": 0.45},
              MARKET, BOOK, ACCOUNT, CFG)
    assert "low_confidence" in r.reasons, r
    assert "invalid_side" not in r.reasons, r

    # Open with valid side + conf -> passes side/confidence, no decision_not_open
    r = check({"decision": "open", "side": "buy", "confidence": 0.8},
              MARKET, BOOK, ACCOUNT, CFG)
    assert "decision_not_open" not in r.reasons, r
    assert "invalid_side" not in r.reasons, r
    assert "low_confidence" not in r.reasons, r
    assert r.allowed, r

    print("RISK SIDE VALIDATION TEST PASSED")


if __name__ == "__main__":
    main()