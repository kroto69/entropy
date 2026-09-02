"""Order intent builder. No signing, no network, no /exchange."""
from dataclasses import dataclass, asdict
from decimal import Decimal, ROUND_DOWN
import time, uuid

@dataclass(frozen=True)
class OrderIntent:
    client_id: str
    coin: str
    side: str
    size: str
    price: str
    amount_usdc: str
    take_profit_pct: str
    stop_loss_pct: str
    max_hold_minutes: int
    reduce_only: bool = False
    tif: str = "Ioc"
    expires_after: int = 0
    leverage: int = 1


def _round_down(value, decimals):
    q = Decimal(1).scaleb(-int(decimals))
    return str(Decimal(str(value)).quantize(q, rounding=ROUND_DOWN))


def build_entry(coin, signal, market, book, cfg):
    amount = Decimal(str(cfg["position"]["amount_usdc"]))
    px = Decimal(str(book["ask_px"] if signal["side"] == "buy" else book["bid_px"]))
    size = _round_down(amount / px, market["sz_decimals"])
    if Decimal(size) <= 0: raise ValueError("rounded size is zero")
    lev = int(cfg["position"].get("leverage", 1))
    cloid = "0x" + uuid.uuid4().hex  # valid 16-byte cloid for fill tracking
    return OrderIntent(cloid, coin, signal["side"], size, str(px),
        str(amount), str(cfg["position"]["take_profit_pct"]),
        str(cfg["position"]["stop_loss_pct"]), int(cfg["position"]["max_hold_minutes"]),
        expires_after=int(time.time()*1000)+30_000, leverage=lev)


def as_dict(intent): return asdict(intent)


def simulate(intent):
    return {"status":"SIMULATED", "intent":as_dict(intent),
            "would_call_exchange":False, "would_sign":False}

if __name__ == "__main__":
    print("planner loaded; no network or signing")
# ponytail: one immutable intent model; add richer order types only when live test requires them.
