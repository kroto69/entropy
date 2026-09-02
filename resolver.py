"""Resolve Entropy (io) HIP-3 markets dynamically from Hyperliquid metadata.

Phase 1: read-only. No /exchange, no signing, no live orders.
"""
import json
import sys
import urllib.request

INFO_URL = "https://api.hyperliquid.xyz/info"


class EntropyError(Exception):
    pass


def _info(request_body, timeout=20):
    req = urllib.request.Request(
        INFO_URL,
        data=json.dumps(request_body).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _find_dex(dexs, name):
    for dex in dexs:
        if dex and dex.get("name") == name:
            return dex
    raise EntropyError(f"dex '{name}' not found in perpDexs")


def fetch_entropy_dex(timeout=20):
    dexs = _info({"type": "perpDexs"}, timeout=timeout)
    return _find_dex(dexs, "io")


def resolve_market(coin, dexs=None, meta=None, timeout=20):
    """Return metadata for coin (e.g. 'io:SNDK').

    Validates that the coin exists in the io dex and in the perp universe.
    """
    if dexs is None:
        dexs = _info({"type": "perpDexs"}, timeout=timeout)
    dex = _find_dex(dexs, "io")

    if not coin.startswith("io:"):
        raise EntropyError(f"coin '{coin}' is not in io dex namespace")

    # Find perp_dex_index of 'io' in the perpDexs list.
    if dexs is None:
        dexs = _info({"type": "perpDexs"}, timeout=timeout)
    perp_dex_index = None
    for i, d in enumerate(dexs):
        if d and d.get("name") == "io":
            perp_dex_index = i
            break
    if perp_dex_index is None:
        raise EntropyError("io dex not found")

    if meta is None:
        meta = _info({"type": "meta", "dex": "io"}, timeout=timeout)

    universe = meta.get("universe", [])
    if not universe:
        raise EntropyError("io dex universe empty")

    idx_in_meta = None
    for i, m in enumerate(universe):
        if m.get("name") == coin:
            idx_in_meta = i
            break
    if idx_in_meta is None:
        raise EntropyError(f"coin '{coin}' not found in io perp universe")

    asset = 100000 + perp_dex_index * 10000 + idx_in_meta

    oi_caps = {k: v for k, v in dex.get("assetToStreamingOiCap", [])}
    funding_m = {k: v for k, v in dex.get("assetToFundingMultiplier", [])}
    coin_meta = universe[idx_in_meta]

    return {
        "dex": "io",
        "coin": coin,
        "asset_id": asset,
        "perp_dex_index": perp_dex_index,
        "index_in_meta": idx_in_meta,
        "sz_decimals": coin_meta.get("szDecimals"),
        "max_leverage": coin_meta.get("maxLeverage"),
        "margin_mode": coin_meta.get("marginMode"),
        "is_delisted": coin_meta.get("isDelisted", False),
        "growth_mode": coin_meta.get("growthMode"),
        "oi_cap": oi_caps.get(coin),
        "funding_multiplier": funding_m.get(coin),
        "only_isolated": coin_meta.get("onlyIsolated"),
    }


def main(argv):
    if len(argv) != 2:
        print("usage: resolver.py <coin>  e.g. io:SNDK")
        return 2
    coin = argv[1]
    try:
        info = resolve_market(coin)
        print(json.dumps(info, indent=2))
        return 0
    except Exception as e:  # noqa: BLE001 - CLI error surface
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
