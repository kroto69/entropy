"""Deterministic fail-closed risk gate."""
from dataclasses import dataclass

@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reasons: tuple[str, ...]


def _strategy_cfg(cfg):
    strat = cfg.get("strategy")
    if isinstance(strat, dict):
        return strat
    # AI-primary: limits come from strategy_params (strategy is the string "ai").
    params = cfg.get("strategy_params")
    if isinstance(params, dict) and (params.get("min_confidence") is not None
                                     or params.get("max_spread_bps") is not None):
        return {"min_confidence": params.get("min_confidence", 0.65),
                "max_spread_bps": params.get("max_spread_bps", 25.0)}
    return {"min_confidence": 0.0, "max_spread_bps": 25.0}


def check(signal, market, book, account, cfg):
    reasons = []
    strategy = _strategy_cfg(cfg)
    pos = cfg["position"]
    if signal.get("decision") != "open": reasons.append("decision_not_open")
    # Side is only meaningful for an open decision; never flag invalid_side on HOLD/SKIP/CLOSE.
    if signal.get("decision") == "open" and signal.get("side") not in ("buy", "sell"):
        reasons.append("invalid_side")
    if signal.get("decision") == "open" and float(signal.get("confidence", 0)) < float(strategy["min_confidence"]):
        reasons.append("low_confidence")
    if market.get("is_delisted"): reasons.append("market_delisted")
    if book.get("spread_bps", 1e9) > float(strategy["max_spread_bps"]): reasons.append("spread_too_wide")
    if market.get("only_isolated") is not True: reasons.append("isolated_mode_not_confirmed")
    if len(account.get("positions", [])) >= int(pos.get("max_positions", 1)): reasons.append("position_limit")
    if any(p.get("coin") == signal.get("coin") for p in account.get("positions", [])): reasons.append("already_positioned")
    free = float(account.get("free_collateral", 0))
    amount = float(pos["amount_usdc"])
    lev = max(1, int(pos.get("leverage", 1)))
    margin_needed = amount / lev
    if free < margin_needed:
        reasons.append("insufficient_free_collateral")
    return RiskDecision(not reasons, tuple(reasons))
