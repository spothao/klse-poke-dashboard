# KLSE Poke Dashboard

A static, high-contrast e-ink dashboard for a Boox Poke 5 (6-inch, 1072×1448). It shows two KLSE paper-trading portfolios: MoneyLens and KLSE Screener / Yahoo.

Intended GitHub Pages URL: https://spothao.github.io/klse-poke-dashboard/

Pages is not enabled yet (the Actions token cannot create a Pages site). Enable it once:

1. Open https://github.com/spothao/klse-poke-dashboard/settings/pages
2. Under **Build and deployment**, set **Source** to **GitHub Actions**
3. Re-run the **Deploy GitHub Pages** workflow, or push any commit

Until Pages is enabled, this public URL serves the same `index.html`:

https://raw.githack.com/spothao/klse-poke-dashboard/main/index.html

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
