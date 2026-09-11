"""Telegram HTML formatting stays valid without making API calls."""
from report.telegram import scan_report_html, format_positions_html, esc


def result(reason):
    return {"coin": "io:ANTH", "status": "REJECTED", "sig": {
        "decision": "hold", "side": None, "confidence": 0.45, "reason": reason},
        "risk": ["decision_not_open", "EMA20 < EMA50"]}


def main():
    text = scan_report_html("DRY-RUN", [result("EMA20 < EMA50 & RSI > 70")])
    assert "EMA20 &lt; EMA50 &amp; RSI &gt; 70" in text, text
    assert "<b>SCAN 1 MARKET</b>" in text
    assert "<b>io:ANTH</b>" in text

    special = scan_report_html("DRY-RUN", [result('x > y & z="q" and it\'s unsafe')])
    assert "x &gt; y &amp; z=&quot;q&quot;" in special, special
    assert "it&#x27;s unsafe" in special, special
    assert "<b>" in special and "</b>" in special

    positions = format_positions_html({
        "open_positions": 1, "account_value": 10, "free_collateral": 5,
        "positions": [{"coin": "io:<ANTH>", "side": "long", "u_pnl": 0,
                       "entry_px": 100, "size": 0.1, "mark_px": 100,
                       "margin_used": 2, "held_minutes": 1,
                       "liq_px": None, "tp": "101<&", "sl": "99>"}],
    })
    assert "io:&lt;ANTH&gt;" in positions
    assert "101&lt;&amp;" in positions and "99&gt;" in positions
    assert "<b>PORTOFOLIO</b>" in positions

    assert esc(None) == ""
    print("TELEGRAM HTML TESTS PASSED")


if __name__ == "__main__":
    main()