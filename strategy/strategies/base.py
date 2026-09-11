"""Normalized strategy signal helpers."""


def _result(decision, side, confidence, reason):
    if decision == "open" and side not in ("buy", "sell"):
        return hold("invalid strategy side")
    try:
        confidence = max(0.0, min(0.90, float(confidence)))
    except (TypeError, ValueError):
        return hold("invalid strategy confidence")
    return {"decision": decision, "side": side if decision == "open" else None,
            "confidence": confidence, "reason": str(reason)[:200]}


def hold(reason, confidence=0.0):
    return _result("hold", None, confidence, reason)


def skip(reason, confidence=0.0):
    return _result("skip", None, confidence, reason)


def open_signal(side, confidence, reason):
    if side not in ("buy", "sell"):
        return hold("invalid strategy side")
    return _result("open", side, confidence, reason)
