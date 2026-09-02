"""Phase 2: read-only market/account data adapter. No signing, no /exchange."""
import json
import time
import urllib.request
from datetime import datetime, timedelta, timezone

from resolver import INFO_URL, _info, resolve_market

STALE_SEC = 30


class DataError(Exception):
    pass


def l2_book(coin, timeout=20):
    return _info({"type": "l2Book", "coin": coin}, timeout=timeout)


def all_mids(timeout=20):
    return _info({"type": "allMids"}, timeout=timeout)


def clearinghouse_state(address, dex="io", timeout=20):
    if not address or not address.startswith("0x") or len(address) != 42:
        raise DataError(f"invalid address: {address!r}")
    body = {"type": "clearinghouseState", "user": address}
    if dex:
        body["dex"] = dex
    return _info(body, timeout=timeout)


def open_orders(address, timeout=20):
    if not address or not address.startswith("0x") or len(address) != 42:
        raise DataError(f"invalid address: {address!r}")
    return _info({"type": "frontendOpenOrders", "user": address}, timeout=timeout)


def user_fills(address, timeout=20):
    if not address or not address.startswith("0x") or len(address) != 42:
        raise DataError(f"invalid address: {address!r}")
    return _info({"type": "userFills", "user": address}, timeout=timeout)


def candles_snapshot(coin, interval="15m", lookback_minutes=600, timeout=20):
    end = int(time.time() * 1000)
    start = end - lookback_minutes * 60_000
    return _info(
        {"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval,
            "startTime": start, "endTime": end,
        }},
        timeout=timeout,
    )


def book_view(coin, depth=5):
    """Return fresh top-of-book snapshot with spread, else raise."""
    raw = l2_book(coin)
    ts = time.time()
    levels = raw.get("levels") or []
    if not isinstance(levels, list) or len(levels) < 2:
        raise DataError(f"invalid book levels for {coin}")
    bids = levels[0] or []
    asks = levels[1] or []
    if not bids or not asks:
        raise DataError(f"empty book for {coin}")
    top_b, top_a = bids[0], asks[0]
    bp, ap = float(top_b["px"]), float(top_a["px"])
    if bp <= 0 or ap <= 0 or ap < bp:
        raise DataError(f"invalid book prices for {coin}: {bp}/{ap}")
    return {
        "coin": coin,
        "ts": ts,
        "bid_px": bp,
        "ask_px": ap,
        "bid_sz": float(top_b["sz"]),
        "ask_sz": float(top_a["sz"]),
        "spread": round(ap - bp, 10),
        "spread_bps": round((ap - bp) / ((ap + bp) / 2) * 10000, 4),
        "bids": bids[:depth],
        "asks": asks[:depth],
    }


def account_summary(address, dex="io"):
    """Flatten margin/position state used by risk gate. dex default = Entropy HIP-3."""
    st = clearinghouse_state(address, dex)
    margin = st.get("marginSummary") or {}
    withdrawable = st.get("withdrawable")
    if isinstance(withdrawable, dict):
        free_collateral = withdrawable.get("withdrawable") or 0
    else:
        free_collateral = withdrawable or 0
    positions = []
    for p in st.get("assetPositions", []):
        pos = (p or {}).get("position") or {}
        if float(pos.get("szi") or 0) == 0:
            continue
        positions.append(
            {
                "coin": pos.get("coin"),
                "szi": float(pos.get("szi") or 0),
                "entry_px": float(pos.get("entryPx") or 0),
                "position_value": float(pos.get("positionValue") or 0),
                "unrealized_pnl": float(pos.get("unrealizedPnl") or 0),
                "liquidation_px": pos.get("liquidationPx"),
                "margin_used": float(pos.get("marginUsed") or 0),
            }
        )
    return {
        "address": address,
        "ts": time.time(),
        "account_value": float(margin.get("accountValue") or 0),
        "free_collateral": float(free_collateral),
        "total_raw_usd": float(margin.get("totalRawUsd") or 0),
        "positions": positions,
    }


def main(argv):
    coin = argv[1] if len(argv) > 1 else "io:SNDK"
    out = {"market": resolve_market(coin), "book": book_view(coin)}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(__import__("sys").argv))
