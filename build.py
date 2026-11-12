#!/usr/bin/env python3
"""Generate a static, e-ink-friendly KLSE paper-trading dashboard."""

from __future__ import annotations

import html
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

MYT = ZoneInfo("Asia/Kuala_Lumpur")

ROOT = Path(__file__).resolve().parent
MONEYLENS_LEDGER = Path("/workspace/papertrade/ledger.json")
SCREENER_LEDGER = Path("/workspace/papertrade-screener/ledger.json")
OUTPUT = ROOT / "index.html"

YAHOO_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
YAHOO_TIMEOUT = 12
YAHOO_RETRIES = 3


@dataclass
class Holding:
    code: str
    name: str
    shares: int
    avg_cost: float
    cost: float
    ledger_close: float | None = None


@dataclass
class Portfolio:
    key: str
    title: str
    path: Path
    missing: bool = False
    error: str | None = None
    cash: float = 0.0
    contributed: float = 0.0
    holdings: list[Holding] = field(default_factory=list)
    ledger_date: str | None = None


def short_name(name: str, code: str) -> str:
    if not name:
        return code
    # "HEIM - Heineken Malaysia Berhad" -> HEIM
    if " - " in name:
        return name.split(" - ", 1)[0].strip() or code
    return name.strip()


def _last_closes_from_history(history: list) -> tuple[dict[str, float], str | None]:
    closes: dict[str, float] = {}
    date = None
    if not history:
        return closes, date
    last = history[-1]
    if isinstance(last, dict):
        date = last.get("date")
        raw = last.get("closes") or {}
        for code, val in raw.items():
            if isinstance(val, dict):
                price = val.get("price")
            else:
                price = val
            if price is not None:
                try:
                    closes[str(code)] = float(price)
                except (TypeError, ValueError):
                    pass
        mtm = last.get("holdings_mtm") or {}
        for code, val in mtm.items():
            if str(code) not in closes and isinstance(val, dict) and val.get("close") is not None:
                try:
                    closes[str(code)] = float(val["close"])
                except (TypeError, ValueError):
                    pass
    return closes, date


def load_moneylens(path: Path) -> Portfolio:
    port = Portfolio(key="moneylens", title="MoneyLens", path=path)
    if not path.is_file():
        port.missing = True
        return port
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        port.error = str(exc)
        return port

    port.cash = float(data.get("cash_myr") or 0)
    history = data.get("history") or []
    closes, date = _last_closes_from_history(history)
    port.ledger_date = date
    if history and isinstance(history[-1], dict):
        last = history[-1]
        if last.get("total_contributed") is not None:
            port.contributed = float(last["total_contributed"])
        if last.get("cash") is not None:
            port.cash = float(last["cash"])
    if not port.contributed:
        port.contributed = float(
            sum((day.get("budget_credited") or 0) for day in history if isinstance(day, dict))
        )

    for raw in data.get("holdings") or []:
        code = str(raw.get("code") or "").strip()
        if not code:
            continue
        shares = int(raw.get("shares") or 0)
        cost = float(raw.get("cost") or 0)
        avg = raw.get("buy_price")
        if avg is None and shares:
            avg = cost / shares
        port.holdings.append(
            Holding(
                code=code,
                name=short_name(str(raw.get("name") or ""), code),
                shares=shares,
                avg_cost=float(avg or 0),
                cost=cost,
                ledger_close=closes.get(code),
            )
        )
    return port


def load_screener(path: Path) -> Portfolio:
    port = Portfolio(key="screener", title="KLSE Screener / Yahoo", path=path)
    if not path.is_file():
        port.missing = True
        return port
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        port.error = str(exc)
        return port

    port.cash = float(data.get("cash_myr") or 0)
    port.contributed = float(data.get("total_contributed") or 0)
    if not port.contributed:
        credits = data.get("credits") or {}
        if isinstance(credits, dict):
            port.contributed = float(sum(credits.values()))

    daily = data.get("daily") or []
    closes, date = _last_closes_from_history(daily)
    port.ledger_date = date
    if daily and isinstance(daily[-1], dict) and daily[-1].get("cash") is not None:
        port.cash = float(daily[-1]["cash"])
    if daily and isinstance(daily[-1], dict) and daily[-1].get("contributed") is not None:
        port.contributed = float(daily[-1]["contributed"])

    holdings = data.get("holdings") or {}
    if isinstance(holdings, dict):
        items = holdings.items()
    else:
        items = ((h.get("code"), h) for h in holdings)

    for code, raw in items:
        code = str(code or "").strip()
        if not code or not isinstance(raw, dict):
            continue
        shares = int(raw.get("shares") or 0)
        cost = float(raw.get("cost") or 0)
        avg = raw.get("avg_cost")
        if avg is None and shares:
            avg = cost / shares
        port.holdings.append(
            Holding(
                code=code,
                name=short_name(str(raw.get("name") or ""), code),
                shares=shares,
                avg_cost=float(avg or 0),
                cost=cost,
                ledger_close=closes.get(code),
            )
        )
    return port


def symbol_candidates(code: str, name: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    opts = [f"{code}.KL"]
    stripped = code.lstrip("0") or "0"
    if stripped != code:
        opts.append(f"{stripped}.KL")
    if name and name.upper() not in {code.upper(), stripped.upper()}:
        opts.append(f"{name}.KL")
    for sym in opts:
        key = sym.upper()
        if key not in seen:
            seen.add(key)
            out.append(sym)
    return out


class YahooClient:
    def __init__(self) -> None:
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
        self.crumb: str | None = None
        self.blocked = False
        self._rate_limits = 0

    def _request(self, url: str) -> bytes:
        if self.blocked:
            raise RuntimeError("Yahoo rate-limited; using ledger closes")
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": YAHOO_UA,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Language": "en-MY,en;q=0.9",
            },
        )
        last_err: Exception | None = None
        for attempt in range(1, YAHOO_RETRIES + 1):
            try:
                with self.opener.open(req, timeout=YAHOO_TIMEOUT) as resp:
                    self._rate_limits = 0
                    return resp.read()
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code == 429:
                    self._rate_limits += 1
                    if self._rate_limits >= 2:
                        self.blocked = True
                        print("Yahoo returned 429 twice; falling back to ledger closes.", file=sys.stderr)
                        raise
                if exc.code in (429, 500, 502, 503, 504) and attempt < YAHOO_RETRIES and not self.blocked:
                    time.sleep(2 * attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_err = exc
                if attempt < YAHOO_RETRIES:
                    time.sleep(2 * attempt)
                    continue
                raise
        raise last_err or RuntimeError("Yahoo request failed")

    def warmup(self) -> None:
        try:
            self._request("https://fc.yahoo.com")
        except Exception:
            pass
        try:
            crumb = self._request("https://query1.finance.yahoo.com/v1/test/getcrumb").decode(
                "utf-8", "replace"
            ).strip()
            if crumb and "Too Many" not in crumb and "<" not in crumb and len(crumb) < 80:
                self.crumb = crumb
        except Exception:
            self.crumb = None

    def quote_batch(self, symbols: list[str]) -> dict[str, float]:
        prices: dict[str, float] = {}
        if not symbols:
            return prices
        # Try the v7 quote endpoint first (works with a crumb).
        query = {"symbols": ",".join(symbols)}
        if self.crumb:
            query["crumb"] = self.crumb
        url = "https://query1.finance.yahoo.com/v7/finance/quote?" + urllib.parse.urlencode(query)
        try:
            data = json.loads(self._request(url).decode("utf-8"))
            for row in (data.get("quoteResponse") or {}).get("result") or []:
                sym = row.get("symbol")
                price = row.get("regularMarketPrice")
                if sym and price is not None:
                    prices[str(sym).upper()] = float(price)
        except Exception as exc:
            print(f"Yahoo batch quote failed: {exc}", file=sys.stderr)
        return prices

    def chart_price(self, symbol: str) -> float | None:
        for host in ("query1.finance.yahoo.com", "query2.finance.yahoo.com"):
            url = f"https://{host}/v8/finance/chart/{urllib.parse.quote(symbol)}?interval=1d&range=5d"
            try:
                data = json.loads(self._request(url).decode("utf-8"))
            except Exception as exc:
                print(f"Yahoo chart {symbol} failed: {exc}", file=sys.stderr)
                continue
            results = (data.get("chart") or {}).get("result") or []
            if not results:
                continue
            res = results[0]
            meta = res.get("meta") or {}
            price = meta.get("regularMarketPrice") or meta.get("chartPreviousClose")
            if price is None:
                closes = ((res.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
                price = next((c for c in reversed(closes) if c is not None), None)
            if price is not None:
                return float(price)
        return None


def fetch_yahoo_prices(holdings: Iterable[Holding]) -> dict[str, tuple[float, str]]:
    """Return {code: (price, yahoo_symbol)} for symbols that resolve."""
    items = list(holdings)
    candidates_by_code: dict[str, list[str]] = {}
    all_symbols: list[str] = []
    seen_sym: set[str] = set()
    for h in items:
        cands = symbol_candidates(h.code, h.name)
        candidates_by_code[h.code] = cands
        for sym in cands:
            key = sym.upper()
            if key not in seen_sym:
                seen_sym.add(key)
                all_symbols.append(sym)

    client = YahooClient()
    try:
        client.warmup()
    except Exception as exc:
        print(f"Yahoo warmup failed: {exc}", file=sys.stderr)

    batch: dict[str, float] = {}
    # Chunk to keep URLs short.
    chunk_size = 20
    for i in range(0, len(all_symbols), chunk_size):
        batch.update(client.quote_batch(all_symbols[i : i + chunk_size]))

    resolved: dict[str, tuple[float, str]] = {}
    for code, cands in candidates_by_code.items():
        found = False
        for sym in cands:
            price = batch.get(sym.upper())
            if price is not None:
                resolved[code] = (price, sym)
                found = True
                break
        if found or client.blocked:
            continue
        for sym in cands:
            if client.blocked:
                break
            price = client.chart_price(sym)
            if price is not None:
                resolved[code] = (price, sym)
                break
            time.sleep(0.4)
    return resolved


def fmt_rm(value: float) -> str:
    return f"{value:,.2f}"


def fmt_cost(value: float) -> str:
    if abs(value - round(value)) < 0.001:
        return f"{value:,.0f}"
    return f"{value:,.2f}"


def fmt_price(value: float) -> str:
    if value < 1:
        return f"{value:.3f}"
    text = f"{value:.3f}"
    if text.endswith("0"):
        return f"{value:.2f}"
    return text


def fmt_pct(value: float) -> str:
    if abs(value) < 0.005:
        return "0.00%"
    sign = "+" if value > 0 else "\u2212"
    return f"{sign}{abs(value):.2f}%"


def fmt_pnl_rm(value: float) -> str:
    if abs(value) < 0.005:
        return "RM0.00"
    sign = "+" if value > 0 else "\u2212"
    return f"{sign}RM{abs(value):,.2f}"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def portfolio_metrics(port: Portfolio, prices: dict[str, tuple[float, str]]) -> dict:
    rows = []
    holdings_value = 0.0
    for h in port.holdings:
        if h.code in prices:
            close, _sym = prices[h.code]
            source = "yahoo"
        elif h.ledger_close is not None:
            close = h.ledger_close
            source = "ledger"
        else:
            close = None
            source = "missing"
        market_value = (h.shares * close) if close is not None else None
        if market_value is not None:
            holdings_value += market_value
        pnl_pct = None
        if close is not None and h.avg_cost:
            pnl_pct = (close - h.avg_cost) / h.avg_cost * 100.0
        rows.append(
            {
                "code": h.code,
                "name": h.name,
                "shares": h.shares,
                "cost": h.cost,
                "avg_cost": h.avg_cost,
                "close": close,
                "pnl_pct": pnl_pct,
                "source": source,
            }
        )
    total = port.cash + holdings_value
    pnl = total - port.contributed if port.contributed else 0.0
    pnl_pct = (pnl / port.contributed * 100.0) if port.contributed else 0.0
    return {
        "rows": rows,
        "holdings_value": holdings_value,
        "total": total,
        "pnl": pnl,
        "pnl_pct": pnl_pct,
    }



# ---------------------------------------------------------------------------
# Multi-page, no-scroll layout for a Boox Poke 5
# ---------------------------------------------------------------------------

# Tightest CSS viewport first. Layout uses 100vh/100vw so it also fills 1072x1448.
VIEWPORTS = ((360, 480), (400, 530), (1072, 1448))
DEFAULT_ROWS_PER_PAGE = 12
MIN_ROWS_PER_PAGE = 4

CSS = """
:root { color-scheme: only light; }
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: #fff;
  color: #000;
}
body {
  font-family: Arial, Helvetica, "Noto Sans", "DejaVu Sans", sans-serif;
  font-size: clamp(16px, 4.3vmin, 42px);
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
}
.page {
  width: 100vw;
  height: 100vh;
  max-width: 100vw;
  max-height: 100vh;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  padding: 0.4em 0.45em 0.3em;
}
header, footer, .jump { flex: 0 0 auto; }
main {
  flex: 1 1 auto;
  min-height: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
}
h1 {
  font-size: 1.22em;
  line-height: 1.1;
  margin: 0;
  font-weight: 700;
}
.updated {
  font-size: 1em;
  margin: 0.12em 0 0.15em;
}
.subpage { font-size: 1em; font-weight: 700; margin: 0 0 0.15em; }
.compare {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  margin: 0 0 0.3em;
  flex: 0 0 auto;
}
.compare th, .compare td {
  border: 2px solid #000;
  padding: 0.22em 0.25em;
  text-align: right;
  font-weight: 700;
  vertical-align: middle;
}
.compare th:first-child, .compare td:first-child {
  text-align: left;
  font-weight: 700;
  width: 28%;
}
.compare thead th { text-align: center; }
.ahead {
  border: 3px solid #000;
  font-weight: 700;
  text-align: center;
  padding: 0.3em 0.35em;
  margin: 0 0 0.35em;
  flex: 0 0 auto;
}
.jump {
  display: flex;
  flex-direction: column;
  gap: 0.28em;
  margin: 0 0 0.3em;
}
.jump a, nav.bar a, nav.bar span {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: max(48px, 11vmin);
  border: 3px solid #000;
  color: #000;
  text-decoration: none;
  font-weight: 700;
  text-align: center;
  padding: 0.15em 0.3em;
  background: #fff;
}
nav.bar {
  display: flex;
  gap: 0.28em;
  margin-top: auto;
  padding-top: 0.25em;
}
nav.bar a, nav.bar span { flex: 1; }
nav.bar .home { font-weight: 800; }
table.grid {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
  font-size: 1em;
}
table.grid th, table.grid td {
  border: 2px solid #000;
  padding: 0.12em 0.1em;
  text-align: left;
  vertical-align: middle;
  overflow: hidden;
  white-space: nowrap;
}
table.grid th { font-weight: 700; }
table.grid .num { text-align: right; }
table.grid .c-code { width: 14%; }
table.grid .c-name { width: 24%; }
table.grid .c-shs { width: 11%; }
table.grid .c-cost { width: 17%; }
table.grid .c-close { width: 16%; }
table.grid .c-pnl { width: 18%; }
table.grid.watch .c-code { width: 18%; }
table.grid.watch .c-name { width: 32%; }
table.grid.watch .c-close { width: 22%; }
table.grid.watch .c-held { width: 28%; }
.note, .missing {
  font-size: 1em;
  margin: 0.2em 0 0;
  flex: 0 0 auto;
}
.missing { font-weight: 700; }
"""


def format_stamp(updated: datetime) -> str:
    try:
        return updated.strftime("%a %-d %b %Y, %-I:%M %p") + " MYT"
    except ValueError:
        return updated.strftime("%a %d %b %Y, %I:%M %p").replace("  0", " ").replace(" 0", " ") + " MYT"


def close_text(row: dict) -> str:
    if row.get("close") is None:
        return "\u2014"
    text = fmt_price(row["close"])
    if row.get("source") != "yahoo":
        text += "*"
    return text


def chunk(rows: list, size: int) -> list[list]:
    if not rows:
        return [[]]
    size = max(1, size)
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def filenames_for(stem: str, count: int) -> list[str]:
    if count <= 1:
        return [f"{stem}.html"]
    return [f"{stem}.html"] + [f"{stem}-{i}.html" for i in range(2, count + 1)]


def render_bar(prev_href: str, next_href: str) -> str:
    return (
        '<nav class="bar" aria-label="Page">'
        f'<a href="{esc(prev_href)}">Prev</a>'
        '<a class="home" href="index.html">Home</a>'
        f'<a href="{esc(next_href)}">Next</a>'
        "</nav>"
    )


def render_shell(title: str, stamp: str, main: str, prev_href: str, next_href: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
  <meta http-equiv="refresh" content="900">
  <meta name="color-scheme" content="light only">
  <title>{esc(title)}</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <div class="page">
    <header>
      <h1>{esc(title)}</h1>
      <p class="updated">Last updated: {esc(stamp)}</p>
    </header>
    <main>
{main}
    </main>
    {render_bar(prev_href, next_href)}
  </div>
</body>
</html>
"""


def render_index(moneylens: Portfolio, m: dict | None, screener: Portfolio, s: dict | None, stamp: str, prev_href: str, next_href: str) -> str:
    def cell(metrics: dict | None, port: Portfolio, key: str) -> str:
        if port.missing:
            return "Missing"
        if port.error or not metrics:
            return "Error"
        if key == "total":
            return "RM" + fmt_rm(metrics["total"])
        if key == "cash":
            return "RM" + fmt_rm(port.cash)
        if key == "contributed":
            return "RM" + fmt_rm(port.contributed)
        if key == "pnl":
            return fmt_pnl_rm(metrics["pnl"])
        if key == "pnl_pct":
            return fmt_pct(metrics["pnl_pct"])
        return "\u2014"

    rows = [
        ("Value", "total"),
        ("Cash", "cash"),
        ("Contributed", "contributed"),
        ("P&amp;L", "pnl"),
        ("P&amp;L %", "pnl_pct"),
    ]
    body = ['<table class="compare">',
            "<thead><tr><th></th><th>MoneyLens</th><th>Screener</th></tr></thead>",
            "<tbody>"]
    for label, key in rows:
        body.append(
            f"<tr><td>{label}</td>"
            f"<td>{esc(cell(m, moneylens, key)) if key != 'pnl' and label != 'P&amp;L' else cell(m, moneylens, key)}</td>"
            f"<td>{esc(cell(s, screener, key)) if key != 'pnl' and label != 'P&amp;L' else cell(s, screener, key)}</td></tr>"
        )
    # The above esc logic is messy because fmt_pnl_rm/fmt_pct have unicode minus, no HTML specials except we should still esc.
    # Rebuild cleanly below if needed — actually fmt values can be escaped always. I'll fix in a cleaner loop after writing... 
    body.append("</tbody></table>")

    ahead = "Cannot compare portfolios."
    if m and s and not moneylens.missing and not screener.missing:
        diff = s["total"] - m["total"]
        if abs(diff) < 0.005:
            ahead = "The two portfolios are tied."
        elif diff > 0:
            ahead = f"Screener is ahead by {fmt_pnl_rm(diff).lstrip('+')}."
        else:
            ahead = f"MoneyLens is ahead by {fmt_pnl_rm(-diff).lstrip('+')}."
    body.append(f'<p class="ahead">{esc(ahead)}</p>')
    body.append('<div class="jump">')
    body.append('<a href="moneylens.html">MoneyLens holdings</a>')
    body.append('<a href="screener.html">Screener holdings</a>')
    body.append('<a href="watchlist.html">Watchlist</a>')
    body.append("</div>")
    return render_shell("KLSE Paper Dashboard", stamp, "\n".join(body), prev_href, next_href)


def holdings_table(rows: list[dict], empty_msg: str = "No holdings.") -> str:
    parts = ['<table class="grid">',
             "<colgroup>"
             '<col class="c-code"><col class="c-name"><col class="c-shs">'
             '<col class="c-cost"><col class="c-close"><col class="c-pnl">'
             "</colgroup>",
             "<thead><tr>"
             "<th>Code</th><th>Name</th><th class='num'>Shs</th>"
             "<th class='num'>Cost</th><th class='num'>Close</th>"
             "<th class='num'>P/L</th>"
             "</tr></thead><tbody>"]
    if not rows:
        parts.append(f"<tr><td colspan='6'>{esc(empty_msg)}</td></tr>")
    for row in rows:
        pnl_txt = "\u2014" if row.get("pnl_pct") is None else fmt_pct(row["pnl_pct"])
        parts.append(
            "<tr>"
            f"<td>{esc(row['code'])}</td>"
            f"<td>{esc(row['name'])}</td>"
            f"<td class='num'>{int(row['shares']):,}</td>"
            f"<td class='num'>{fmt_cost(row['cost'])}</td>"
            f"<td class='num'>{esc(close_text(row))}</td>"
            f"<td class='num'>{pnl_txt}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    return "\n".join(parts)


def watchlist_table(rows: list[dict]) -> str:
    parts = ['<table class="grid watch">',
             "<colgroup>"
             '<col class="c-code"><col class="c-name"><col class="c-close"><col class="c-held">'
             "</colgroup>",
             "<thead><tr>"
             "<th>Code</th><th>Name</th><th class='num'>Close</th><th>Held in</th>"
             "</tr></thead><tbody>"]
    if not rows:
        parts.append("<tr><td colspan='4'>No holdings to list.</td></tr>")
    for row in rows:
        parts.append(
            "<tr>"
            f"<td>{esc(row['code'])}</td>"
            f"<td>{esc(row['name'])}</td>"
            f"<td class='num'>{esc(close_text(row))}</td>"
            f"<td>{esc(row['held'])}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    return "\n".join(parts)


def footnote(rows: list[dict], ledger_date: str | None = None) -> str:
    asof = f" Ledger {ledger_date}." if ledger_date else ""
    if any(r.get("source") != "yahoo" and r.get("close") is not None for r in rows):
        return f'<p class="note">* Ledger close (Yahoo unavailable).{esc(asof)}</p>'
    if asof:
        return f'<p class="note">{esc(asof.strip())}</p>'
    return ""


def combine_watchlist(ports: list[tuple[Portfolio, dict | None]]) -> list[dict]:
    combined: dict[str, dict] = {}
    labels = {"moneylens": "MoneyLens", "screener": "Screener"}
    for port, metrics in ports:
        if not metrics:
            continue
        label = labels.get(port.key, port.title)
        for row in metrics["rows"]:
            entry = combined.setdefault(
                row["code"],
                {
                    "code": row["code"],
                    "name": row["name"],
                    "close": row["close"],
                    "source": row["source"],
                    "held_in": [],
                },
            )
            if label not in entry["held_in"]:
                entry["held_in"].append(label)
            if not entry["name"] and row["name"]:
                entry["name"] = row["name"]
    rows = []
    for code in sorted(combined, key=lambda c: (combined[c]["name"] or c, c)):
        item = combined[code]
        held = item["held_in"]
        if len(held) > 1:
            held_txt = "Both"
        else:
            held_txt = held[0] if held else "\u2014"
        rows.append({**item, "held": held_txt})
    return rows


def build_page_list(
    moneylens: Portfolio,
    m_metrics: dict | None,
    screener: Portfolio,
    s_metrics: dict | None,
    watch_rows: list[dict],
    stamp: str,
    rows_per_page: int,
) -> list[dict]:
    pages: list[dict] = []

    def add_table_pages(stem: str, title: str, rows: list[dict], kind: str, note_html: str, empty: str) -> None:
        chunks = chunk(rows, rows_per_page)
        names = filenames_for(stem, len(chunks))
        total = len(chunks)
        for i, (name, part) in enumerate(zip(names, chunks), start=1):
            heading = title if total == 1 else f"{title} ({i}/{total})"
            table = watchlist_table(part) if kind == "watch" else holdings_table(part, empty)
            main = f"{table}\n{note_html}"
            pages.append({"filename": name, "title": heading, "main": main, "kind": kind})

    pages.append({"filename": "index.html", "title": "KLSE Paper Dashboard", "kind": "index"})

    m_rows = (m_metrics or {}).get("rows") or []
    s_rows = (s_metrics or {}).get("rows") or []
    m_note = footnote(m_rows, moneylens.ledger_date)
    s_note = footnote(s_rows, screener.ledger_date)
    w_note = footnote(watch_rows)

    if moneylens.missing:
        pages.append({
            "filename": "moneylens.html",
            "title": "MoneyLens holdings",
            "main": f'<p class="missing">Ledger not found at {esc(moneylens.path)}.</p>',
            "kind": "holdings",
        })
    elif moneylens.error:
        pages.append({
            "filename": "moneylens.html",
            "title": "MoneyLens holdings",
            "main": f'<p class="missing">Could not read ledger: {esc(moneylens.error)}</p>',
            "kind": "holdings",
        })
    else:
        add_table_pages("moneylens", "MoneyLens holdings", m_rows, "holdings", m_note, "No holdings.")

    if screener.missing:
        pages.append({
            "filename": "screener.html",
            "title": "Screener holdings",
            "main": f'<p class="missing">Ledger not found at {esc(screener.path)}.</p>',
            "kind": "holdings",
        })
    elif screener.error:
        pages.append({
            "filename": "screener.html",
            "title": "Screener holdings",
            "main": f'<p class="missing">Could not read ledger: {esc(screener.error)}</p>',
            "kind": "holdings",
        })
    else:
        add_table_pages("screener", "Screener holdings", s_rows, "holdings", s_note, "No holdings.")

    add_table_pages("watchlist", "Watchlist", watch_rows, "watch", w_note, "No holdings to list.")

    # Fill index and prev/next, then render HTML
    n = len(pages)
    rendered: list[dict] = []
    for i, page in enumerate(pages):
        prev_href = pages[(i - 1) % n]["filename"]
        next_href = pages[(i + 1) % n]["filename"]
        if page["kind"] == "index":
            html_out = render_index(moneylens, m_metrics, screener, s_metrics, stamp, prev_href, next_href)
        else:
            html_out = render_shell(page["title"], stamp, page["main"], prev_href, next_href)
        rendered.append({"filename": page["filename"], "html": html_out, "kind": page["kind"]})
    return rendered


# Fix render_index table cells to always escape — replace the messy version.
def render_index(moneylens: Portfolio, m: dict | None, screener: Portfolio, s: dict | None, stamp: str, prev_href: str, next_href: str) -> str:
    def cell(metrics: dict | None, port: Portfolio, key: str) -> str:
        if port.missing:
            return "Missing"
        if port.error or not metrics:
            return "Error"
        if key == "total":
            return "RM" + fmt_rm(metrics["total"])
        if key == "cash":
            return "RM" + fmt_rm(port.cash)
        if key == "contributed":
            return "RM" + fmt_rm(port.contributed)
        if key == "pnl":
            return fmt_pnl_rm(metrics["pnl"])
        if key == "pnl_pct":
            return fmt_pct(metrics["pnl_pct"])
        return "\u2014"

    body = [
        '<table class="compare">',
        "<thead><tr><th></th><th>MoneyLens</th><th>Screener</th></tr></thead>",
        "<tbody>",
    ]
    for label, key in (
        ("Value", "total"),
        ("Cash", "cash"),
        ("Contributed", "contributed"),
        ("P&amp;L", "pnl"),
        ("P&amp;L %", "pnl_pct"),
    ):
        body.append(
            f"<tr><td>{label}</td>"
            f"<td>{esc(cell(m, moneylens, key))}</td>"
            f"<td>{esc(cell(s, screener, key))}</td></tr>"
        )
    body.append("</tbody></table>")

    ahead = "Cannot compare portfolios."
    if m and s and not moneylens.missing and not screener.missing:
        diff = s["total"] - m["total"]
        if abs(diff) < 0.005:
            ahead = "The two portfolios are tied."
        elif diff > 0:
            ahead = f"Screener is ahead by {fmt_pnl_rm(diff).lstrip('+')}."
        else:
            ahead = f"MoneyLens is ahead by {fmt_pnl_rm(-diff).lstrip('+')}."
    body.append(f'<p class="ahead">{esc(ahead)}</p>')
    body.append('<div class="jump">')
    body.append('<a href="moneylens.html">MoneyLens holdings</a>')
    body.append('<a href="screener.html">Screener holdings</a>')
    body.append('<a href="watchlist.html">Watchlist</a>')
    body.append("</div>")
    return render_shell("KLSE Paper Dashboard", stamp, "\n".join(body), prev_href, next_href)


def write_pages(pages: list[dict], out_dir: Path) -> list[Path]:
    # Remove previously generated site pages so stale splits do not linger.
    keep = {p["filename"] for p in pages}
    for existing in out_dir.glob("*.html"):
        if existing.name not in keep:
            existing.unlink()
    written = []
    for page in pages:
        path = out_dir / page["filename"]
        path.write_text(page["html"], encoding="utf-8")
        written.append(path)
        print(f"Wrote {path}")
    return written


def generated_html_files(out_dir: Path) -> list[Path]:
    names = []
    for name in ("index.html", "moneylens.html", "screener.html", "watchlist.html"):
        path = out_dir / name
        if path.is_file():
            names.append(path)
    for path in sorted(out_dir.glob("moneylens-*.html")) + sorted(out_dir.glob("screener-*.html")) + sorted(out_dir.glob("watchlist-*.html")):
        if path not in names:
            names.append(path)
    return names


def main() -> int:
    moneylens = load_moneylens(MONEYLENS_LEDGER)
    screener = load_screener(SCREENER_LEDGER)
    all_holdings = list(moneylens.holdings) + list(screener.holdings)
    print(
        f"Loaded MoneyLens: missing={moneylens.missing} holdings={len(moneylens.holdings)} "
        f"cash={moneylens.cash} contrib={moneylens.contributed}"
    )
    print(
        f"Loaded Screener: missing={screener.missing} holdings={len(screener.holdings)} "
        f"cash={screener.cash} contrib={screener.contributed}"
    )

    prices: dict[str, tuple[float, str]] = {}
    if all_holdings:
        try:
            prices = fetch_yahoo_prices(all_holdings)
        except Exception as exc:
            print(f"Yahoo fetch aborted: {exc}", file=sys.stderr)
    print(f"Yahoo prices resolved: {len(prices)} / {len({h.code for h in all_holdings})}")
    for code, (price, sym) in sorted(prices.items()):
        print(f"  {code} {sym} {price}")

    m_metrics = None if (moneylens.missing or moneylens.error) else portfolio_metrics(moneylens, prices)
    s_metrics = None if (screener.missing or screener.error) else portfolio_metrics(screener, prices)
    watch_rows = combine_watchlist([(moneylens, m_metrics), (screener, s_metrics)])
    updated = datetime.now(MYT)
    stamp = format_stamp(updated)

    rows_per_page = DEFAULT_ROWS_PER_PAGE
    pages = build_page_list(moneylens, m_metrics, screener, s_metrics, watch_rows, stamp, rows_per_page)
    write_pages(pages, ROOT)

    if m_metrics:
        print(f"MoneyLens total RM{m_metrics['total']:.2f} P&L {m_metrics['pnl']:+.2f} ({m_metrics['pnl_pct']:+.2f}%)")
    if s_metrics:
        print(f"Screener total RM{s_metrics['total']:.2f} P&L {s_metrics['pnl']:+.2f} ({s_metrics['pnl_pct']:+.2f}%)")
    print(f"Pages: {', '.join(p['filename'] for p in pages)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
