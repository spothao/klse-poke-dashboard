#!/usr/bin/env python3
"""
Generate a black-on-white screensaver PNG for Boox Poke 5 (1072x1448).
Reads the two paper-trading ledger files and writes screensaver.png.

In production this runs inside spothao/moneylens's "Paper ledgers" workflow,
with the ledgers exported from the MoneyLens database (export_paper_ledger).

Env vars:
  ML_LEDGER  MoneyLens ledger JSON (required)
  SC_LEDGER  Screener ledger JSON  (required)
  OUTPUT     Output PNG path       (default: screensaver.png next to this script)

Usage:
  ML_LEDGER=moneylens.json SC_LEDGER=screener.json python3 generate_screensaver.py
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required: pip install Pillow")
    raise

_SCRIPT_DIR = Path(__file__).resolve().parent
MYT = ZoneInfo("Asia/Kuala_Lumpur")


def _path_from_env(name, default=None):
    value = os.environ.get(name)
    if value:
        return Path(value)
    if default is None:
        raise SystemExit(f"{name} is required: path to that bot's ledger JSON")
    return Path(default)


ML_LEDGER = _path_from_env("ML_LEDGER")
SC_LEDGER = _path_from_env("SC_LEDGER")
OUTPUT = _path_from_env("OUTPUT", _SCRIPT_DIR / "screensaver.png")

WIDTH, HEIGHT = 1072, 1448
BG = (255, 255, 255)
FG = (0, 0, 0)
GRAY = (80, 80, 80)

# Contribution rule used only if the ledger has no contributed total.
RULE_START = date(2026, 10, 14)
RULE_RATE_CHANGE = date(2026, 11, 9)  # RM1,000/week before this Monday, RM500 from this date


def load_ledger(path: Path):
    # A missing ledger used to render an empty half-screen and push it; fail instead.
    if not path.exists():
        raise SystemExit(f"Ledger not found: {path}")
    with open(path) as f:
        return json.load(f)


def fmt_rm(val):
    if val is None:
        return "-"
    return f"RM {float(val):,.2f}"


def fmt_pct(val):
    if val is None:
        return "-"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"


def _as_date(value):
    if not value:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    text = str(value)[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def ledger_as_of(ledger):
    if not ledger:
        return date.today()
    for key in ("last_updated",):
        d = _as_date(ledger.get(key))
        if d:
            return d
    history = ledger.get("history") or []
    if history:
        d = _as_date(history[-1].get("date"))
        if d:
            return d
    daily = ledger.get("daily") or []
    if daily:
        d = _as_date(daily[-1].get("date"))
        if d:
            return d
    return date.today()


def contributed_from_rule(ledger=None, as_of=None):
    """RM1,000 credited on the start date, then each following Monday;
    RM1,000 per credit before 9 Nov 2026, RM500 from 9 Nov onward."""
    start = RULE_START
    if ledger and ledger.get("start_date"):
        parsed = _as_date(ledger.get("start_date"))
        if parsed:
            start = parsed
    as_of = as_of or ledger_as_of(ledger)
    if as_of < start:
        return 0.0

    def amount(day):
        return 500.0 if day >= RULE_RATE_CHANGE else 1000.0

    total = amount(start)
    days_until_monday = (7 - start.weekday()) % 7
    monday = start + timedelta(days=7 if days_until_monday == 0 else days_until_monday)
    while monday <= as_of:
        total += amount(monday)
        monday += timedelta(days=7)
    return float(total)


def get_portfolio_value(ledger):
    if not ledger:
        return 0.0
    if "latest_portfolio_total" in ledger and ledger["latest_portfolio_total"] is not None:
        return float(ledger["latest_portfolio_total"])
    if "daily" in ledger and ledger["daily"]:
        return float(ledger["daily"][-1].get("portfolio_value") or 0)
    history = ledger.get("history") or []
    if history:
        last = history[-1]
        for key in ("portfolio_total", "portfolio_market_value"):
            if last.get(key) is not None:
                return float(last[key])
    return float(ledger.get("portfolio_total") or 0)


def get_contributed(ledger):
    if not ledger:
        return 0.0
    if ledger.get("total_contributed"):
        return float(ledger["total_contributed"])
    if ledger.get("contributed"):
        return float(ledger["contributed"])
    daily = ledger.get("daily") or []
    if daily and daily[-1].get("contributed"):
        return float(daily[-1]["contributed"])
    history = ledger.get("history") or []
    for entry in reversed(history):
        if entry.get("total_contributed"):
            return float(entry["total_contributed"])
    credited = sum(float(entry.get("budget_credited") or 0) for entry in history)
    if credited:
        return float(credited)
    credits = ledger.get("credits")
    if isinstance(credits, dict) and credits:
        return float(sum(float(v) for v in credits.values()))
    return contributed_from_rule(ledger)


def get_cash(ledger):
    if not ledger:
        return 0.0
    if ledger.get("cash_myr") is not None:
        return float(ledger["cash_myr"])
    if ledger.get("cash") is not None:
        return float(ledger["cash"])
    history = ledger.get("history") or []
    if history and history[-1].get("cash") is not None:
        return float(history[-1]["cash"])
    daily = ledger.get("daily") or []
    if daily and daily[-1].get("cash") is not None:
        return float(daily[-1]["cash"])
    return 0.0


def get_latest_closes(ledger):
    """Map stock code -> close price. MoneyLens stores {code: {price}}, Screener stores {code: float}."""
    if not ledger:
        return {}
    raw = None
    history = ledger.get("history") or []
    if history and history[-1].get("closes"):
        raw = history[-1]["closes"]
    elif ledger.get("daily") and ledger["daily"][-1].get("closes"):
        raw = ledger["daily"][-1]["closes"]
    if not isinstance(raw, dict):
        return {}
    out = {}
    for code, value in raw.items():
        if isinstance(value, dict):
            price = value.get("price")
            if price is None:
                price = value.get("close")
            if price is not None:
                out[str(code)] = float(price)
        elif value is not None:
            out[str(code)] = float(value)
    return out


def get_holdings(ledger):
    if not ledger:
        return []
    h = ledger.get("holdings", {})
    if isinstance(h, dict):
        rows = []
        for code, item in h.items():
            rec = dict(item) if isinstance(item, dict) else {"value": item}
            rec.setdefault("code", str(code))
            rows.append(rec)
        return rows
    if isinstance(h, list):
        return list(h)
    return []


def holding_avg_cost(h):
    if h.get("avg_cost") not in (None, "", 0, 0.0):
        return float(h["avg_cost"])
    if h.get("buy_price") not in (None, ""):
        return float(h["buy_price"])
    shares = float(h.get("shares") or 0)
    cost = h.get("cost")
    if shares and cost not in (None, ""):
        return float(cost) / shares
    return 0.0


def holding_close(h, closes):
    code = str(h.get("code") or "")
    if code and closes and code in closes:
        return float(closes[code])
    for key in ("close", "price"):
        if h.get(key) not in (None, ""):
            return float(h[key])
    return 0.0


def display_name(h):
    name = str(h.get("name") or "").strip()
    if " - " in name:
        name = name.split(" - ", 1)[0].strip()
    return name or str(h.get("code") or "?")


def pnl_pct(value, contributed):
    if not contributed:
        return 0.0
    return (value - contributed) / contributed * 100.0


def fit_text(draw, text, font, max_width):
    if max_width <= 0:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    ell = "…"
    if draw.textlength(ell, font=font) > max_width:
        return ""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if draw.textlength(text[:mid] + ell, font=font) <= max_width:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo] + ell


def load_fonts():
    try:
        return {
            "title": ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48),
            "big": ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40),
            "med": ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28),
            "sm": ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22),
        }
    except Exception:
        fallback = ImageFont.load_default()
        return {"title": fallback, "big": fallback, "med": fallback, "sm": fallback}


def main():
    ml = load_ledger(ML_LEDGER)
    sc = load_ledger(SC_LEDGER)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    fonts = load_fonts()

    margin = 40
    right = WIDTH - margin

    y = 36
    title = "KLSE Paper Dashboard"
    as_of = max(ledger_as_of(ml), ledger_as_of(sc))
    now = datetime.now(MYT)
    date_text = (
        f"Close {as_of.strftime('%-d %b')} · "
        f"rendered {now.strftime('%-d %b %H:%M')} MYT"
    )
    draw.text((margin, y), title, font=fonts["title"], fill=FG)
    date_w = draw.textlength(date_text, font=fonts["med"])
    title_w = draw.textlength(title, font=fonts["title"])
    date_x = right - date_w
    if date_x < margin + title_w + 16:
        # Not enough room on the title row; put the date underneath on the right.
        draw.text((date_x, y + 56), date_text, font=fonts["med"], fill=GRAY)
        y += 96
    else:
        draw.text((date_x, y + 12), date_text, font=fonts["med"], fill=GRAY)
        y += 76

    ml_val = get_portfolio_value(ml)
    sc_val = get_portfolio_value(sc)
    total_val = ml_val + sc_val
    ml_contrib = get_contributed(ml)
    sc_contrib = get_contributed(sc)
    total_contrib = ml_contrib + sc_contrib
    total_pnl = total_val - total_contrib
    total_pnl_p = pnl_pct(total_val, total_contrib)
    ml_cash = get_cash(ml)
    sc_cash = get_cash(sc)
    cash = ml_cash + sc_cash

    def draw_stat(x, y0, label, value, font=fonts["big"]):
        draw.text((x, y0), label, font=fonts["sm"], fill=GRAY)
        draw.text((x, y0 + 28), value, font=font, fill=FG)

    mid_x = WIDTH // 2
    draw_stat(margin, y, "TOTAL VALUE", fmt_rm(total_val))
    draw_stat(mid_x, y, "TOTAL P&L", f"{fmt_rm(total_pnl)}   {fmt_pct(total_pnl_p)}")
    y += 90
    draw_stat(margin, y, "CASH", fmt_rm(cash), font=fonts["med"])
    y += 72

    draw.line([(margin, y), (right, y)], fill=FG, width=2)
    y += 24

    def draw_portfolio(x, col_right, title, val, contrib, cash_l):
        pnl = val - contrib
        pnl_p = pnl_pct(val, contrib)
        draw.text((x, y), title, font=fonts["med"], fill=FG)
        lines = [
            f"Value  {fmt_rm(val)}",
            f"P&L    {fmt_rm(pnl)}  {fmt_pct(pnl_p)}",
            f"Cash   {fmt_rm(cash_l)}",
        ]
        yy = y + 42
        for line in lines:
            draw.text((x, yy), fit_text(draw, line, fonts["sm"], col_right - x), font=fonts["sm"], fill=FG)
            yy += 34

    gap = 40
    col_w = (WIDTH - 2 * margin - gap) // 2
    draw_portfolio(margin, margin + col_w, "MoneyLens", ml_val, ml_contrib, ml_cash)
    draw_portfolio(margin + col_w + gap, right, "Screener", sc_val, sc_contrib, sc_cash)
    y += 160

    draw.line([(margin, y), (right, y)], fill=FG, width=2)
    y += 22

    def draw_holdings_group(title, holdings, closes):
        nonlocal y
        draw.text((margin, y), title, font=fonts["med"], fill=FG)
        y += 40
        close_col_w = 120
        pct_col_w = 130
        pct_x = right
        close_x = right - pct_col_w
        name_max = close_x - close_col_w - margin - 8
        for h in holdings:
            name = fit_text(draw, display_name(h), fonts["sm"], name_max)
            close = holding_close(h, closes)
            avg = holding_avg_cost(h)
            pct = ((close - avg) / avg * 100) if avg else 0.0
            close_text = f"{close:,.3f}" if close else "-"
            pct_text = fmt_pct(pct)
            close_w = draw.textlength(close_text, font=fonts["sm"])
            pct_w = draw.textlength(pct_text, font=fonts["sm"])
            draw.text((margin, y), name, font=fonts["sm"], fill=FG)
            draw.text((close_x - close_w, y), close_text, font=fonts["sm"], fill=FG)
            draw.text((pct_x - pct_w, y), pct_text, font=fonts["sm"], fill=FG)
            y += 36

    ml_holdings = get_holdings(ml)
    sc_holdings = get_holdings(sc)
    ml_closes = get_latest_closes(ml)
    sc_closes = get_latest_closes(sc)

    # Keep a little bottom margin; tighten row height if needed.
    groups = [("MONEYLENS", ml_holdings, ml_closes), ("SCREENER", sc_holdings, sc_closes)]
    rows = sum(len(h) for _, h, _ in groups) + len(groups)
    remaining = HEIGHT - 28 - y
    if rows and remaining / rows < 36:
        # Should still fit at 36px; if not, the loop uses 36 and we clip visually later.
        pass

    draw_holdings_group("MONEYLENS", ml_holdings, ml_closes)
    y += 10
    draw_holdings_group("SCREENER", sc_holdings, sc_closes)

    img.save(OUTPUT, "PNG")
    print(f"Saved {OUTPUT} ({WIDTH}x{HEIGHT})")
    print(
        f"MoneyLens  value={ml_val:.2f} contrib={ml_contrib:.2f} "
        f"pnl={ml_val - ml_contrib:.2f} ({pnl_pct(ml_val, ml_contrib):.2f}%) cash={ml_cash:.2f}"
    )
    print(
        f"Screener   value={sc_val:.2f} contrib={sc_contrib:.2f} "
        f"pnl={sc_val - sc_contrib:.2f} ({pnl_pct(sc_val, sc_contrib):.2f}%) cash={sc_cash:.2f}"
    )
    print(
        f"Total      value={total_val:.2f} contrib={total_contrib:.2f} "
        f"pnl={total_pnl:.2f} ({total_pnl_p:.2f}%) cash={cash:.2f}"
    )
    for label, holdings, closes in (("ML", ml_holdings, ml_closes), ("SC", sc_holdings, sc_closes)):
        for h in holdings:
            close = holding_close(h, closes)
            avg = holding_avg_cost(h)
            pct = ((close - avg) / avg * 100) if avg else 0.0
            print(f"  {label} {display_name(h):12} close={close:.4f} avg={avg:.4f} {fmt_pct(pct)}")


if __name__ == "__main__":
    main()
