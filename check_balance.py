"""Read-only Hyperliquid balance checker. Never signs or calls /exchange."""
import os
from hyperliquid.info import Info
from hyperliquid.utils import constants


def load_env(path="/entropy/.env"):
    if os.path.exists(path):
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())


def mask(address):
    return address[:6] + "..." + address[-4:] if address else "EMPTY"


def main():
    load_env()
    account = os.getenv("HL_ACCOUNT_ADDRESS", "")
    agent = os.getenv("HL_API_WALLET_ADDRESS", "")
    if not account.startswith("0x") or len(account) != 42:
        raise SystemExit("HL_ACCOUNT_ADDRESS invalid or empty")

    info = Info(constants.MAINNET_API_URL, skip_ws=True)
    state = info.user_state(account, "io")  # Entropy HIP-3 dex
    margin = state.get("marginSummary") or {}
    withdrawable = state.get("withdrawable", 0)
    if isinstance(withdrawable, dict):
        withdrawable = withdrawable.get("withdrawable", 0)

    print("account address:", account)
    print("api wallet address:", agent)
    print("account value:", margin.get("accountValue", "0"))
    print("total raw USD:", margin.get("totalRawUsd", "0"))
    print("total margin used:", margin.get("totalMarginUsed", "0"))
    print("withdrawable/free collateral:", withdrawable)
    positions = []
    for item in state.get("assetPositions", []):
        position = item.get("position", {})
        if float(position.get("szi", 0) or 0) != 0:
            positions.append((position.get("coin"), position.get("szi"), position.get("positionValue")))
    print("open positions:", positions or "none")


if __name__ == "__main__":
    main()
