# Entropy Bot

Bot trading otomatis untuk market **Entropy HIP-3** (`io:*` perp DEX) di Hyperliquid.

Live loop: scan market → keputusan AI/heuristik → risk gate → entry IOC → TP/SL native → monitor posisi → laporan Telegram.

## Arsitektur

```
main.py        Loop utama + monitor posisi + Telegram command listener
resolver.py    Resolusi market io:* dari metadata Hyperliquid (read-only)
market_data.py Data market: L2 book, candles, account state (read-only)
decision.py    Konteks prompt + heuristik fallback + validasi keputusan AI
ai.py          Adapter AI ([OI]-compatible chat endpoint, output JSON ketat)
risk.py        Risk gate deterministik, fail-closed
planner.py     Pembuat intent order (size, harga, cloid)
executor.py    Eksekusi order via Hyperliquid Python SDK (perp_dexs=["io"])
protection.py  Kalkulasi harga TP/SL dari entry
reconciler.py  Rekonsiliasi state lokal vs exchange
learning.py    Log belajar append-only
telegram.py    Notifikasi + laporan HTML + inline keyboard + command polling
check_balance.py Cek saldo/posisi DEX io (diagnostik)
```

## Setup

```bash
cd /entropy
cp .env.example .env   # isi key
```

Isi `.env` (jangan pernah commit):

```
DRY_RUN=false                # false = live
LIVE_TRADING_ENABLED=true    # keduanya wajib untuk live
AI_ENDPOINT=...              # chat-completions compatible
AI_MODEL=...
AI_API_KEY=...
HL_API_WALLET_PRIVATE_KEY=0x...   # API wallet, BUKAN master wallet
HL_API_WALLET_ADDRESS=0x...
HL_ACCOUNT_ADDRESS=0x...          # master/subaccount untuk query state
TELEGRAM_BOT_TOKEN=...
TELEGRAM_CHAT_ID=...
```

Menjalankan:

```bash
./run-dryrun.sh              # mode dry-run
pm2 start /usr/local/lib/hermes-agent/venv/bin/python3 \
  --name entropy-bot --cwd /entropy --interpreter none -- -u main.py
```

## Mode

| Mode | Syarat |
|------|--------|
| DRY-RUN | `DRY_RUN=true` (default) — semua order simulasi |
| LIVE | `DRY_RUN=false` **dan** `LIVE_TRADING_ENABLED=true` |

Fail-closed: env kurang satu saja → semua aksi jadi `SIMULATED`.

## Config (`config.json`)

```json
{
  "markets": ["io:ANTH", "io:SNDK", "io:NBIS", "io:GPRO"],
  "strategy": {
    "decision_mode": "trend",
    "trend_threshold_pct": 0.5,
    "min_confidence": 0.65,
    "max_spread_bps": 25.0,
    "min_candle_count": 20
  },
  "position": {
    "amount_usdc": 10.5,
    "leverage": 5,
    "max_positions": 2,
    "take_profit_pct": 100.0,
    "stop_loss_pct": 40.0,
    "max_hold_minutes": 60
  },
  "runtime": {
    "entry_interval_minutes": 10
  }
}
```

Preset:

- **Scalping degen**: `trend_threshold_pct 0.2`, `min_confidence 0.55`, `take_profit_pct 2`, `stop_loss_pct 1`, `max_hold_minutes 30`, interval 5
- **Day trade** (default): lihat atas
- **Swing**: `trend_threshold_pct 1.0`, `min_confidence 0.75`, `take_profit_pct 15`, `stop_loss_pct 5`, `max_hold_minutes 1440`, interval 30

Ubah config → `pm2 restart entropy-bot` (config dibaca saat startup).

Catatan: TP 100% untuk short tidak mungkin (harga 0) → otomatis cap 99%. Order minimum notional exchange = $10.

## Telegram

Laporan otomatis tiap cycle:

- `SCAN n MARKET` — keputusan per market, confidence, alasan, status order
- `PORTOFOLIO` — posisi aktif, mark live, PnL, ROE, duration, TP/SL, tombol Close
- `CLOSED` / `MAX-HOLD CLOSE` — notifikasi posisi tertutup
- `[ALERT]` — posisi terbuka tanpa TP/SL (protection gagal)
- `[ERROR]` — exception cycle

Command (hanya diproses dari `TELEGRAM_CHAT_ID`):

```
/pos              laporan posisi + tombol Close
/status           ringkas posisi & free collateral
/close io:COIN    tutup posisi manual (market reduce-only)
/help             daftar command
```

PnL di Telegram dihitung ulang dari mid bid/ask live, bukan snapshot — supaya cocok dengan web.

## Siklus kerja

1. `monitor_positions` — laporan PnL live, tutup posisi lewat `max_hold_minutes`
2. Scan tiap market di `markets`
3. AI (atau heuristik fallback) → `open|close|hold|skip` + side + confidence
4. Risk gate: confidence, spread, delisted, isolated, max posisi, margin (`amount/leverage`)
5. Entry: limit IOC pada best bid/ask
6. Fill dikonfirmasi dari response (fallback polling cloid)
7. TP/SL: trigger order reduce-only native di exchange — posisi tidak pernah telanjang
8. Laporan scan + posisi dikirim, sleep `entry_interval_minutes`, ulang

AI tidak pernah menentukan amount/leverage/TP/SL/asset — semua dari config. AI hanya trend + side + confidence.

## Operasional

```bash
pm2 logs entropy-bot --lines 50    # lihat log
pm2 restart entropy-bot            # restart
./check_balance.py                 # saldo & posisi DEX io
```

Log delivery Telegram terlihat di stdout:

```
telegram: '...' -> {'sent': True}
```

## Keamanan

- `.env` di `.gitignore`, tidak pernah commit
- Signer = API wallet saja, master wallet tidak sign
- Semua order reduce-only untuk TP/SL dan close
- Manual close pakai lock anti double-submit
- Command Telegram dibatasi chat ID terdaftar
