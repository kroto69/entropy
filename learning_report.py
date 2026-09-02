"""Learning log analyzer: summarize decisions, outcomes, and reasons."""
import sys
from collections import Counter
from learning import read_all


def main():
    rows = read_all()
    if not rows:
        print("learning log kosong")
        return
    types = Counter(r.get("type") for r in rows)
    print("Total event:", len(rows))
    print("By type:", dict(types))

    opens = [r for r in rows if r.get("type") == "open"]
    if opens:
        print("\nOPEN:", len(opens))
        print("  reasons:", Counter(r.get("reason", "")[:50] for r in opens).most_common(5))

    closes = [r for r in rows if r.get("type") == "close"]
    if closes:
        pnls = [r.get("pnl", 0) for r in closes]
        total = sum(pnls)
        wins = sum(1 for p in pnls if p > 0)
        print(f"\nCLOSE: {len(closes)} | total PnL: {total:+.4f} USDC")
        print(f"  wins: {wins} | losses: {len(pnls) - wins}")
        print("  reasons:", Counter(r.get("reason", "")[:50] for r in closes).most_common(5))

    decisions = [r for r in rows if r.get("type") == "decision"]
    if decisions:
        print("\nDECISIONS (signal + risk gate):", len(decisions))
        dc = Counter(r.get("decision") for r in decisions)
        print("  signal:", dict(dc))
        risky = Counter(t for r in decisions for t in (r.get("risk") or []))
        print("  risk rejections:", risky.most_common(5))


if __name__ == "__main__":
    main()
