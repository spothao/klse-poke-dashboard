# KLSE Poke Dashboard

A static, high-contrast e-ink dashboard for a Boox Poke 5 (6-inch, 1072×1448). It shows two KLSE paper-trading portfolios: MoneyLens and KLSE Screener / Yahoo.

Live page: https://spothao.github.io/klse-poke-dashboard/

The page needs no JavaScript and reloads itself every 15 minutes. Prices come from Yahoo Finance (`CODE.KL`); if Yahoo is unavailable, the generator uses the ledger’s last close and marks it with `*`.

## Rebuild

```bash
python3 build.py
```

`build.py` reads `/workspace/papertrade/ledger.json` and `/workspace/papertrade-screener/ledger.json`, then writes `index.html`.

To rebuild and push any change to `index.html`:

```bash
./publish.sh
```

Do not commit credentials or ledger files.
