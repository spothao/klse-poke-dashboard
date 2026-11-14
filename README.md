# KLSE Poke Dashboard

A black-on-white screensaver image (1072×1448) for a Boox Poke 5, showing the two
KLSE **paper-trading** portfolios (MoneyLens top-20 and KLSE Screener). No real
orders are ever placed.

The image is served from two places; both stay current:

```bash
# Termux on the Boox
curl -L -o /sdcard/Pictures/screensaver.png \
  https://raw.githubusercontent.com/spothao/klse-poke-dashboard/main/screensaver.png
```

- Pages: https://spothao.github.io/klse-poke-dashboard/screensaver.png (and a
  one-image page at https://spothao.github.io/klse-poke-dashboard/ that reloads
  every 15 minutes)

The old HTML pages (`compare.html`, `moneylens.html`, `watchlist.html`, …) are
gone: they were built from ledger copies in `data/` that went stale.

## How it is updated

Nothing in this repo renders on its own any more; the image is pushed here by the
**Paper ledgers** workflow in `spothao/moneylens`:

1. Each weekday after the close, the two paper-trading bots commit their ledger
   JSON to the `paper-data` branch of `spothao/moneylens`.
2. That workflow upserts both ledgers into the MoneyLens Postgres database
   (`papertrading_paperledgersnapshot`), then exports the newest row per bot.
   It also re-renders on weekdays at 09:00, 13:00 and 18:45 MYT.
3. It checks out this repo, runs `generate_screensaver.py` on the two exports,
   and commits `screensaver.png` to main only if the image changed (with a
   fine-grained token scoped to this repo).
4. That push runs `.github/workflows/pages.yml` here, which only copies
   `screensaver.png` to GitHub Pages.

The database URL stays in moneylens's GitHub secrets; this public repo holds no
ledger data. The Tolino e-reader pages (which include real portfolios) are
rendered and stored privately in `spothao/moneylens`, never here.

## Run it locally

```bash
pip install pillow   # and DejaVu fonts (fonts-dejavu-core) for the intended look
ML_LEDGER=moneylens.json SC_LEDGER=screener.json OUTPUT=screensaver.png \
  python3 generate_screensaver.py
```

`ML_LEDGER` and `SC_LEDGER` are required; the script handles both ledger shapes
(MoneyLens: `holdings` list, `history[]`, `latest_portfolio_total`; Screener:
`holdings` dict, `daily[]`, `total_contributed`).
