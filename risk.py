"""Deterministic fail-closed risk gate."""
from dataclasses import dataclass

@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]


def check(signal, market, book, account, cfg):
    reasons = []
    strategy = cfg["strategy"]
    pos = cfg["position"]
    if signal.get("decision") != "open": reasons.append("decision_not_open")
    if signal.get("side") not in ("buy", "sell"): reasons.append("invalid_side")
    if float(signal.get("confidence", 0)) < float(strategy["min_confidence"]): reasons.append("low_confidence")
    if market.get("is_delisted"): reasons.append("market_delisted")
    if book.get("spread_bps", 1e9) > float(strategy["max_spread_bps"]): reasons.append("spread_too_wide")
    if market.get("only_isolated") is not True: reasons.append("isolated_mode_not_confirmed")
    if len(account.get("positions", [])) >= int(pos.get("max_positions", 1)): reasons.append("position_limit")
    free = float(account.get("free_collateral", 0))
    amount = float(pos["amount_usdc"])
    lev = max(1, int(pos.get("leverage", 1)))
    margin_needed = amount / lev
    if free < margin_needed:
        reasons.append("insufficient_free_collateral")
    return RiskDecision(not reasons, tuple(reasons))
