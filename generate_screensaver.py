#!/usr/bin/env python3
"""
Generate a black-on-white screensaver PNG for Boox Poke 5 (1448x1072).
Reads the two ledger files and writes screensaver.png.

Usage:
  python3 generate_screensaver.py
"""

import json
import os
from datetime import datetime
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    print("Pillow is required: pip install Pillow")
    raise

# Paths (same as build.py expects)
ML_LEDGER = Path("/workspace/papertrade/ledger.json")
SC_LEDGER = Path("/workspace/papertrade-screener/ledger.json")
OUTPUT = Path("screensaver.png")

WIDTH, HEIGHT = 1072, 1448  # Portrait for Poke 5 screensaver
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
    return f"RM {val:,.2f}"

def fmt_pct(val):
    if val is None:
        return "-"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.2f}%"

def main():
    ml = load_ledger(ML_LEDGER)
    sc = load_ledger(SC_LEDGER)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Try common fonts; fall back to default
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
    draw.text((WIDTH - 280, y + 8), now, font=font_med, fill=GRAY)
    y += 80

    # Totals
    total_val = 0
    total_pnl = 0
    total_contrib = 0
    cash = 0
    if ml:
        total_val += ml.get("portfolio_total", 0)
        total_pnl += ml.get("portfolio_total", 0) - ml.get("total_contributed", 0)
        total_contrib += ml.get("total_contributed", 0)
        cash += ml.get("cash_myr", 0)
    if sc:
        total_val += sc.get("portfolio_total", 0)
        total_pnl += sc.get("portfolio_total", 0) - sc.get("total_contributed", 0)
        total_contrib += sc.get("total_contributed", 0)
        cash += sc.get("cash_myr", 0)

    pnl_pct = (total_pnl / total_contrib * 100) if total_contrib else 0

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

    # Divider
    draw.line([(40, y), (WIDTH - 40, y)], fill=FG, width=2)
    y += 30

    # Two columns
    col_w = (WIDTH - 100) // 2
    x1, x2 = 40, 40 + col_w + 20

    def draw_portfolio(x, title, ledger):
        nonlocal y
        draw.text((x, y), title, font=font_med, fill=FG)
        if not ledger:
            draw.text((x, y + 40), "No data", font=font_sm, fill=GRAY)
            return
        val = ledger.get("portfolio_total", 0)
        contrib = ledger.get("total_contributed", 0)
        pnl = val - contrib
        pnl_p = (pnl / contrib * 100) if contrib else 0
        cash_l = ledger.get("cash_myr", 0)

        draw.text((x, y + 45), f"Value  {fmt_rm(val)}", font=font_sm, fill=FG)
        draw.text((x, y + 80), f"P&L    {fmt_rm(pnl)}  {fmt_pct(pnl_p)}", font=font_sm, fill=FG)
        draw.text((x, y + 115), f"Cash   {fmt_rm(cash_l)}", font=font_sm, fill=FG)

    draw_portfolio(x1, "MoneyLens", ml)
    draw_portfolio(x2, "Screener", sc)
    y += 180

    # Top movers (simple: list a few holdings from both)
    draw.line([(40, y), (WIDTH - 40, y)], fill=FG, width=2)
    y += 30
    draw.text((40, y), "HOLDINGS (sample)", font=font_med, fill=FG)
    y += 50

    holdings = []
    if ml and "holdings" in ml:
        holdings.extend(ml["holdings"][:4])
    if sc and "holdings" in sc:
        holdings.extend(sc["holdings"][:4])

    for i, h in enumerate(holdings[:6]):
        code = h.get("name", h.get("code", "?"))
        close = h.get("close", 0)
        avg = h.get("avg_cost", 0)
        pct = ((close - avg) / avg * 100) if avg else 0
        line = f"{code:<10} {fmt_pct(pct)}"
        x = 40 if i % 2 == 0 else WIDTH // 2
        yy = y + (i // 2) * 40
        draw.text((x, yy), line, font=font_sm, fill=FG)

    img.save(OUTPUT, "PNG")
    print(f"Saved {OUTPUT}")

if __name__ == "__main__":
    main()
