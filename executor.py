"""SDK executor wiring. Fail-closed: requires DRY_RUN=false AND LIVE_TRADING_ENABLED=true
AND complete env. Otherwise every action returns SIMULATED or raises."""
import json
import os
from decimal import Decimal

from eth_account import Account
from hyperliquid.exchange import Exchange
from hyperliquid.info import Info
from hyperliquid.utils import constants
from hyperliquid.utils.types import Cloid


class ExecutorError(RuntimeError):
    pass


def load_env(path="/entropy/.env"):
    vals = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip()
    # .env is the source of truth (PM2 may carry stale shell env).
    for k, v in vals.items():
        os.environ[k] = v


def is_live():
    return (
        os.getenv("DRY_RUN", "true").lower() == "false"
        and os.getenv("LIVE_TRADING_ENABLED", "false").lower() == "true"
        and bool(os.getenv("HL_API_WALLET_PRIVATE_KEY"))
        and bool(os.getenv("HL_ACCOUNT_ADDRESS"))
    )


load_env()


class Executor:
    """Single SDK boundary. Only this class signs or calls /exchange."""

    def __init__(self):
        self._exchange = None
        self._info = None
        self.account_address = os.getenv("HL_ACCOUNT_ADDRESS")

    @property
    def exchange(self):
        if self._exchange is None:
            if not is_live():
                raise ExecutorError("executor disabled: not live mode")
            key = os.getenv("HL_API_WALLET_PRIVATE_KEY")
            self._exchange = Exchange(
                Account.from_key(key),
                constants.MAINNET_API_URL,
                account_address=self.account_address,
                perp_dexs=["io"],
            )
        return self._exchange

    @property
    def info(self):
        if self._info is None:
            self._info = Info(constants.MAINNET_API_URL, skip_ws=True)
        return self._info

    def read_account_state(self):
        """Read-only check used in Phase 8 verification."""
        return self.info.user_state(self.account_address)

    def submit_entry(self, intent):
        if not is_live():
            return {"status": "SIMULATED", "intent": intent,
                    "would_call_exchange": False, "would_sign": False}
        # Final validation before signing.
        if intent["tif"] not in ("Alo", "Ioc", "Gtc"):
            raise ExecutorError(f"invalid tif {intent['tif']}")
        sz = float(intent["size"])
        if sz <= 0:
            raise ExecutorError("size must be positive")
        px = float(intent["price"])
        if px <= 0:
            raise ExecutorError("price must be positive")
        leverage = int(intent.get("leverage", 1))
        if leverage < 1 or leverage > int(intent.get("max_leverage", 100)):
            raise ExecutorError(f"invalid leverage {leverage}")
        self.exchange.update_leverage(leverage, intent["coin"], is_cross=False)
        resp = self.exchange.order(
            name=intent["coin"],
            is_buy=intent["side"] == "buy",
            sz=sz,
            limit_px=px,
            order_type={"limit": {"tif": intent["tif"]}},
            reduce_only=False,
            cloid=Cloid(intent["client_id"]),
        )
        return self._checked(resp, "entry")

    def submit_protection(self, intent, entry_price, price_decimals=2):
        """Submit native TP/SL trigger orders after confirmed entry fill."""
        if not is_live():
            return {"status": "SIMULATED", "coin": intent["coin"], "reduce_only": True,
                    "would_call_exchange": False, "would_sign": False}
        from protection import prices
        levels = prices(entry_price, intent["side"], intent["take_profit_pct"],
                        intent["stop_loss_pct"], price_decimals,
                        leverage=intent.get("leverage", 1))
        is_long = intent["side"] == "buy"
        orders = []
        for label, trigger, tpsl in (("tp", levels["take_profit"], "tp"),
                                     ("sl", levels["stop_loss"], "sl")):
            orders.append({"coin": intent["coin"], "is_buy": not is_long,
                           "sz": float(intent["size"]), "limit_px": float(trigger),
                           "order_type": {"trigger": {"triggerPx": float(trigger),
                                                         "isMarket": True, "tpsl": tpsl}},
                           "reduce_only": True})
        out = self._checked(self.exchange.bulk_orders(orders), "protection")
        out["tp"] = levels["take_profit"]
        out["sl"] = levels["stop_loss"]
        return out

    def submit_reduce_close(self, coin, side, sz, px=None, slippage=0.05):
        """Close position reduce-only. side = original position side."""
        if not is_live():
            return {"status": "SIMULATED", "coin": coin, "sz": sz,
                    "reduce_only": True, "would_call_exchange": False}
        if side not in ("buy", "sell"):
            raise ExecutorError("side must be buy|sell")
        if float(sz) <= 0:
            raise ExecutorError("size must be positive")
        resp = self.exchange.market_close(coin, sz=sz, slippage=slippage)
        return self._checked(resp, "reduce_close")

    def _checked(self, resp, action):
        status = resp.get("status")
        out = {"status": status, "action": action, "response": resp}
        if status not in ("ok",):
            out["error"] = resp.get("response", resp)
        return out


# Module-level default used by main.py.
_executor = None


def get_executor():
    global _executor
    if _executor is None:
        _executor = Executor()
    return _executor


def submit(intent):
    return get_executor().submit_entry(intent)


if __name__ == "__main__":
    load_env()
    e = get_executor()
    print("is_live:", is_live())
    if is_live():
        st = e.read_account_state()
        print("account state keys:", sorted(st.keys())[:12])
        print("account_value:", (st.get("marginSummary") or {}).get("accountValue"))
    else:
        print("executor in dry-run: no signing, no /exchange")
# ponytail: single SDK boundary; add TP/SL trigger orders here after entry fill path is verified live-tiny.
