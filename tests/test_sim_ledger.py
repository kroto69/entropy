"""Virtual dry-run lifecycle tests; no exchange/API calls."""
import tempfile
from pathlib import Path
from unittest.mock import patch


def main():
    from core import sim_ledger as s
    with tempfile.TemporaryDirectory() as d:
        with patch.object(s, "LEDGER", Path(d) / "positions.json"), \
             patch.object(s, "CLOSED", Path(d) / "trades.jsonl"):
            p = s.open_sim("io:OAI", "sell", "0.01", 100, 90, 110,
                           0.62, "bear trend", "ai_primary", 30.5, 5, 240)
            assert p["simulated"] is True
            mtm = s.mark_to_market("io:OAI", 95)
            assert mtm["u_pnl"] == 0.05, mtm
            row = s.close_sim("io:OAI", 95, "simulated_ai_close")
            assert row["pnl"] == 0.05, row
            assert row["is_simulated"] is True
            assert s.load_sim() == {}
    print("SIM LEDGER TESTS PASSED")


if __name__ == "__main__":
    main()
