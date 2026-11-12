#!/usr/bin/env python3
"""
Generate a black-on-white screensaver PNG for Boox Poke 5 (1072x1448).
Reads the two ledger files and writes screensaver.png.

Usage:
  python3 generate_screensaver.py
"""

import json
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required: pip install Pillow")
    raise

ML_LEDGER = Path("/workspace/papertrade/ledger.json")
SC_LEDGER = Path("/workspace/papertrade-screener/ledger.json")
OUTPUT = Path("screensaver.png")

WIDTH, HEIGHT = 1072, 1448
BG = (255, 255, 255)
FG = (0, 0, 0)
GRAY = (80, 80, 80)

def load_ledger(path: Path):
    if not path.exists():
        return None
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

def get_portfolio_value(ledger):
    if not ledger:
        return 0
    # MoneyLens style
    if "latest_portfolio_total" in ledger:
        return ledger["latest_portfolio_total"]
    # Screener style
    if "daily" in ledger and ledger["daily"]:
        return ledger["daily"][-1].get("portfolio_value", 0)
    return ledger.get("portfolio_total", 0)

def get_contributed(ledger):
    if not ledger:
        return 0
    return ledger.get("total_contributed") or ledger.get("contributed") or 0

def get_cash(ledger):
    if not ledger:
        return 0
    return ledger.get("cash_myr") or ledger.get("cash") or 0

def get_holdings(ledger):
    if not ledger:
        return []
    h = ledger.get("holdings", {})
    if isinstance(h, dict):
        # Screener: dict keyed by code
        return list(h.values())[:6]
    if isinstance(h, list):
        return h[:6]
    return []

def main():
    ml = load_ledger(ML_LEDGER)
    sc = load_ledger(SC_LEDGER)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 48)
        font_big = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40)
        font_med = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 28)
        font_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
    except Exception:
        font_title = font_big = font_med = font_sm = ImageFont.load_default()

    y = 40
    draw.text((40, y), "KLSE Paper Dashboard", font=font_title, fill=FG)
    now = datetime.now().strftime("%d %b %H:%M")
    draw.text((WIDTH - 300, y + 8), now, font=font_med, fill=GRAY)
    y += 80

    ml_val = get_portfolio_value(ml)
    sc_val = get_portfolio_value(sc)
    total_val = ml_val + sc_val

    ml_contrib = get_contributed(ml)
    sc_contrib = get_contributed(sc)
    total_contrib = ml_contrib + sc_contrib
    total_pnl = total_val - total_contrib
    pnl_pct = (total_pnl / total_contrib * 100) if total_contrib else 0

    cash = get_cash(ml) + get_cash(sc)

    draw.text((40, y), "TOTAL VALUE", font=font_sm, fill=GRAY)
    draw.text((40, y + 30), fmt_rm(total_val), font=font_big, fill=FG)
    y += 100

    draw.text((40, y), "TOTAL P&L", font=font_sm, fill=GRAY)
    pnl_text = f"{fmt_rm(total_pnl)}   {fmt_pct(pnl_pct)}"
    draw.text((40, y + 30), pnl_text, font=font_big, fill=FG)
    y += 100

    draw.text((40, y), "CASH", font=font_sm, fill=GRAY)
    draw.text((40, y + 30), fmt_rm(cash), font=font_med, fill=FG)
    y += 90

    draw.line([(40, y), (WIDTH - 40, y)], fill=FG, width=2)
    y += 30

    def draw_portfolio(x, title, val, contrib, cash_l):
        pnl = val - contrib
        pnl_p = (pnl / contrib * 100) if contrib else 0
        draw.text((x, y), title, font=font_med, fill=FG)
        draw.text((x, y + 45), f"Value  {fmt_rm(val)}", font=font_sm, fill=FG)
        draw.text((x, y + 80), f"P&L    {fmt_rm(pnl)}  {fmt_pct(pnl_p)}", font=font_sm, fill=FG)
        draw.text((x, y + 115), f"Cash   {fmt_rm(cash_l)}", font=font_sm, fill=FG)

    col_w = (WIDTH - 100) // 2
    draw_portfolio(40, "MoneyLens", ml_val, ml_contrib, get_cash(ml))
    draw_portfolio(40 + col_w + 20, "Screener", sc_val, sc_contrib, get_cash(sc))
    y += 180

    draw.line([(40, y), (WIDTH - 40, y)], fill=FG, width=2)
    y += 30
    draw.text((40, y), "HOLDINGS", font=font_med, fill=FG)
    y += 50

    holdings = get_holdings(ml) + get_holdings(sc)
    for i, h in enumerate(holdings[:6]):
        name = h.get("name") or h.get("code") or "?"
        close = float(h.get("close") or h.get("price") or 0)
        avg = float(h.get("avg_cost") or h.get("avg") or close)
        pct = ((close - avg) / avg * 100) if avg else 0
        line = f"{name:<12} {fmt_pct(pct)}"
        x = 40 if i % 2 == 0 else WIDTH // 2
        yy = y + (i // 2) * 42
        draw.text((x, yy), line, font=font_sm, fill=FG)

    img.save(OUTPUT, "PNG")
    print(f"Saved {OUTPUT} ({WIDTH}x{HEIGHT})")

if __name__ == "__main__":
    main()
