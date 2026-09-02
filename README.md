# Entropy Bot

Read-only Phase 1 scaffold for Entropy HIP-3 market discovery.

## Current scope

- Query Hyperliquid `POST https://api.hyperliquid.xyz/info`
- Resolve allowlisted `io:*` markets dynamically from `perpDexs` and `meta`
- Validate asset ID, size decimals, price decimals, max leverage, and market status
- No `/exchange` calls
- No signing
- No live orders

## Run

```bash
python3 resolver.py --market io:SNDK
python3 -m unittest -v test_resolver.py
```

Network resolution uses live Hyperliquid metadata. Tests use fake responses and do not access network.

## Safety

`DRY_RUN=true` is required by default. This phase contains no executor or private-key handling.

## References

- https://github.com/hyperliquid-dex/hyperliquid-python-sdk
- https://api.hyperliquid.xyz/info
- https://docs.entropy.io
- https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api
