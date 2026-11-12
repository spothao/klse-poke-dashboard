# KLSE Poke Dashboard

A static, high-contrast e-ink dashboard for a Boox Poke 5 (6-inch, 1072×1448). It is split into one-screen pages so nothing scrolls.

Live site: https://spothao.github.io/klse-poke-dashboard/

| Page | URL |
| --- | --- |
| Overview | https://spothao.github.io/klse-poke-dashboard/ |
| MoneyLens holdings | https://spothao.github.io/klse-poke-dashboard/moneylens.html |
| Screener holdings | https://spothao.github.io/klse-poke-dashboard/screener.html |
| Watchlist (1/2) | https://spothao.github.io/klse-poke-dashboard/watchlist.html |
| Watchlist (2/2) | https://spothao.github.io/klse-poke-dashboard/watchlist-2.html |

Each page has large Prev / Home / Next buttons, a Last updated line in MYT, and reloads every 15 minutes. No JavaScript is required. Tables that would overflow a Poke screen are split (for example `watchlist-2.html`).

Prices come from Yahoo Finance (`CODE.KL`); if Yahoo is unavailable, the generator uses the ledger’s last close and marks it with `*`.

## Rebuild

```bash
python3 build.py
```

`build.py` reads `/workspace/papertrade/ledger.json` and `/workspace/papertrade-screener/ledger.json`, then writes the HTML pages.

To check that every page fits 360×480, 400×530, and 1072×1448 CSS pixels:

```bash
node measure_viewports.mjs index.html moneylens.html screener.html watchlist.html watchlist-2.html
```

To rebuild and push any change to the HTML pages:

```bash
./publish.sh
```

Do not commit credentials or ledger files.
