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


def render_portfolio(port: Portfolio, metrics: dict | None) -> str:
    parts = [f'<section class="portfolio" id="{esc(port.key)}">']
    parts.append(f"<h2>{esc(port.title)}</h2>")
    if port.missing:
        parts.append(f"<p class='missing'>Ledger not found at {esc(port.path)}.</p>")
        parts.append("</section>")
        return "\n".join(parts)
    if port.error:
        parts.append(f"<p class='missing'>Could not read ledger: {esc(port.error)}</p>")
        parts.append("</section>")
        return "\n".join(parts)
    assert metrics is not None
    asof = f" Ledger {esc(port.ledger_date)}." if port.ledger_date else ""
    parts.append('<div class="stats">')
    parts.append(
        f'<div class="stat"><div class="label">Total value</div>'
        f'<div class="value">RM{fmt_rm(metrics["total"])}</div></div>'
    )
    parts.append(
        f'<div class="stat"><div class="label">Cash</div>'
        f'<div class="value">RM{fmt_rm(port.cash)}</div></div>'
    )
    parts.append(
        f'<div class="stat"><div class="label">Contributed</div>'
        f'<div class="value">RM{fmt_rm(port.contributed)}</div></div>'
    )
    parts.append(
        f'<div class="stat"><div class="label">P&amp;L</div>'
        f'<div class="value">{fmt_pnl_rm(metrics["pnl"])}'
        f'<span class="pct"> ({fmt_pct(metrics["pnl_pct"])})</span></div></div>'
    )
    parts.append("</div>")
    parts.append("<table>")
    parts.append(
        "<thead><tr>"
        "<th>Code</th><th>Name</th><th class='num'>Shares</th>"
        "<th class='num'>Cost</th><th class='num'>Last close</th>"
        "<th class='num'>P&amp;L %</th>"
        "</tr></thead>"
    )
    parts.append("<tbody>")
    if not metrics["rows"]:
        parts.append("<tr><td colspan='6'>No holdings.</td></tr>")
    for row in metrics["rows"]:
        close_txt = "\u2014"
        if row["close"] is not None:
            close_txt = fmt_price(row["close"])
            if row["source"] != "yahoo":
                close_txt += "*"
        pnl_txt = "\u2014" if row["pnl_pct"] is None else fmt_pct(row["pnl_pct"])
        parts.append(
            "<tr>"
            f"<td>{esc(row['code'])}</td>"
            f"<td>{esc(row['name'])}</td>"
            f"<td class='num'>{row['shares']:,}</td>"
            f"<td class='num'>{fmt_rm(row['cost'])}</td>"
            f"<td class='num'>{esc(close_txt)}</td>"
            f"<td class='num'>{pnl_txt}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    if any(r["source"] != "yahoo" and r["close"] is not None for r in metrics["rows"]):
        parts.append(f"<p class='note'>* Ledger last close used (Yahoo unavailable).{asof}</p>")
    elif asof:
        parts.append(f"<p class='note'>{asof.strip()}</p>")
    parts.append("</section>")
    return "\n".join(parts)


def render_watchlist(ports: list[tuple[Portfolio, dict | None]], prices: dict[str, tuple[float, str]]) -> str:
    combined: dict[str, dict] = {}
    for port, metrics in ports:
        if not metrics:
            continue
        for row in metrics["rows"]:
            code = row["code"]
            entry = combined.setdefault(
                code,
                {
                    "code": code,
                    "name": row["name"],
                    "close": row["close"],
                    "source": row["source"],
                    "portfolios": [],
                },
            )
            if port.title not in entry["portfolios"]:
                entry["portfolios"].append(port.title)
            if not entry["name"] and row["name"]:
                entry["name"] = row["name"]
    parts = ['<section class="watchlist" id="watchlist">', "<h2>Watchlist</h2>"]
    parts.append("<table>")
    parts.append(
        "<thead><tr>"
        "<th>Code</th><th>Name</th><th class='num'>Last close</th>"
        "<th>Source</th><th>Held in</th>"
        "</tr></thead><tbody>"
    )
    if not combined:
        parts.append("<tr><td colspan='5'>No holdings to list.</td></tr>")
    for code in sorted(combined, key=lambda c: (combined[c]["name"] or c, c)):
        row = combined[code]
        close_txt = "\u2014" if row["close"] is None else fmt_price(row["close"])
        if row["source"] == "yahoo":
            src = "Yahoo"
        elif row["source"] == "ledger":
            src = "Ledger*"
            close_txt += "*"
        else:
            src = "Missing"
        parts.append(
            "<tr>"
            f"<td>{esc(row['code'])}</td>"
            f"<td>{esc(row['name'])}</td>"
            f"<td class='num'>{esc(close_txt)}</td>"
            f"<td>{esc(src)}</td>"
            f"<td>{esc(', '.join(row['portfolios']))}</td>"
            "</tr>"
        )
    parts.append("</tbody></table>")
    parts.append("</section>")
    return "\n".join(parts)


CSS = """
:root { color-scheme: only light; }
* { box-sizing: border-box; }
html, body {
  margin: 0;
  padding: 0;
  background: #fff;
  color: #000;
}
body {
  font-family: Arial, Helvetica, "Noto Sans", "DejaVu Sans", sans-serif;
  font-size: 22px;
  line-height: 1.35;
  padding: 18px 22px 36px;
  max-width: 1072px;
  font-variant-numeric: tabular-nums;
}
h1 {
  font-size: 36px;
  line-height: 1.15;
  margin: 0 0 6px;
  font-weight: 700;
  letter-spacing: 0.01em;
}
h2 {
  font-size: 28px;
  line-height: 1.2;
  margin: 0 0 12px;
  padding: 0 0 8px;
  border-bottom: 3px solid #000;
  font-weight: 700;
}
.updated {
  font-size: 20px;
  margin: 0 0 22px;
}
.sub {
  font-size: 18px;
  margin: 4px 0 0;
}
section.portfolio, section.watchlist {
  margin: 0 0 28px;
  padding: 0 0 8px;
  border-bottom: 3px solid #000;
}
.stats {
  display: block;
  margin: 0 0 14px;
}
.stat {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 16px;
  border-bottom: 2px solid #000;
  padding: 7px 0;
}
.stat .label {
  font-size: 20px;
  font-weight: 700;
}
.stat .value {
  font-size: 28px;
  font-weight: 700;
  text-align: right;
}
.stat .pct { font-size: 22px; font-weight: 700; }
table {
  width: 100%;
  border-collapse: collapse;
  margin: 8px 0 10px;
  font-size: 20px;
}
th, td {
  border: 2px solid #000;
  padding: 8px 8px;
  text-align: left;
  vertical-align: top;
}
th {
  font-weight: 700;
  background: #fff;
}
td.num, th.num { text-align: right; white-space: nowrap; }
.note, .missing, .footnote {
  font-size: 18px;
  margin: 8px 0 12px;
}
.missing { font-weight: 700; }
.footnote { margin-top: 0; }
"""


def render_page(updated: datetime, sections: list[str]) -> str:
    stamp = updated.strftime("%a %-d %b %Y, %-I:%M %p MYT")
    # %-d / %-I are POSIX; keep a portable fallback.
    try:
        stamp = updated.strftime("%a %-d %b %Y, %-I:%M %p") + " MYT"
    except ValueError:
        stamp = updated.strftime("%a %d %b %Y, %I:%M %p").replace(" 0", " ") + " MYT"
    body = "\n".join(sections)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=1072">
  <meta http-equiv="refresh" content="900">
  <meta name="color-scheme" content="light only">
  <title>KLSE Paper Dashboard</title>
  <style>
{CSS}
  </style>
</head>
<body>
  <header>
    <h1>KLSE Paper Dashboard</h1>
    <p class="updated">Last updated: {esc(stamp)}</p>
    <p class="sub">Paper trading only. Two ledgers, same contributed cash.</p>
  </header>
  {body}
  <p class="footnote">Closes from Yahoo Finance (CODE.KL). A price marked * is the ledger last close because Yahoo was unavailable. Page reloads every 15 minutes.</p>
</body>
</html>
"""


def main() -> int:
    moneylens = load_moneylens(MONEYLENS_LEDGER)
    screener = load_screener(SCREENER_LEDGER)
    all_holdings = list(moneylens.holdings) + list(screener.holdings)
    print(f"Loaded MoneyLens: missing={moneylens.missing} holdings={len(moneylens.holdings)} cash={moneylens.cash} contrib={moneylens.contributed}")
    print(f"Loaded Screener: missing={screener.missing} holdings={len(screener.holdings)} cash={screener.cash} contrib={screener.contributed}")

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

    updated = datetime.now(MYT)
    sections = [
        render_portfolio(moneylens, m_metrics),
        render_portfolio(screener, s_metrics),
        render_watchlist([(moneylens, m_metrics), (screener, s_metrics)], prices),
    ]
    OUTPUT.write_text(render_page(updated, sections), encoding="utf-8")
    print(f"Wrote {OUTPUT}")
    if m_metrics:
        print(f"MoneyLens total RM{m_metrics['total']:.2f} P&L {m_metrics['pnl']:+.2f} ({m_metrics['pnl_pct']:+.2f}%)")
    if s_metrics:
        print(f"Screener total RM{s_metrics['total']:.2f} P&L {s_metrics['pnl']:+.2f} ({s_metrics['pnl_pct']:+.2f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
