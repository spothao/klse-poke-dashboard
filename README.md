# KLSE Poke Dashboard

A black-on-white screensaver image (1072×1448) for a Boox Poke 5, showing the two
KLSE **paper-trading** portfolios (MoneyLens top-20 and KLSE Screener). No real
orders are ever placed.

Termux downloads it from:

```bash
curl -L -o /sdcard/Pictures/screensaver.png \
  https://raw.githubusercontent.com/spothao/klse-poke-dashboard/main/screensaver.png
```

## How it is updated

Nothing in this repo runs on its own any more; the image is pushed here by the
**Paper ledgers** workflow in `spothao/moneylens`:

1. Each weekday after the close, the two paper-trading bots commit their ledger
   JSON to the `paper-data` branch of `spothao/moneylens`.
2. That workflow upserts both ledgers into the MoneyLens Postgres database
   (`papertrading_paperledgersnapshot`), then exports the newest row per bot.
3. It checks out this repo, runs `generate_screensaver.py` on the two exports,
   and commits `screensaver.png` to main only if the image changed (with a
   fine-grained token scoped to this repo).

The database URL stays in moneylens's GitHub secrets; this public repo holds no
ledger data.

## Run it locally

```bash
pip install pillow   # and DejaVu fonts (fonts-dejavu-core) for the intended look
ML_LEDGER=moneylens.json SC_LEDGER=screener.json OUTPUT=screensaver.png \
  python3 generate_screensaver.py
```

`ML_LEDGER` and `SC_LEDGER` are required; the script handles both ledger shapes
(MoneyLens: `holdings` list, `history[]`, `latest_portfolio_total`; Screener:
`holdings` dict, `daily[]`, `total_contributed`).
