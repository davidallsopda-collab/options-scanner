"""
Call options scanner for the phone page.
Scans TSLA, MRVL (Marvell), MU (Micron), SPCX (SpaceX) and writes docs/results.json.
GitHub runs this automatically (see .github/workflows/scan.yml).
You can also run it on your PC:  python scanner.py
"""

import json
import os
import time
from datetime import date, datetime, timezone

import pandas as pd
import yfinance as yf

# ---------- YOUR SETTINGS (edit these) ----------
TICKERS = ["TSLA", "MRVL", "MU", "SPCX"]

MAX_COST_USD = 1300      # max price for ONE contract in dollars (premium x 100)
MIN_DAYS = 21            # soonest expiry (days from today)
MAX_DAYS = 60            # furthest expiry
MAX_OTM_PCT = 10.0       # strike no more than this % above current price
MIN_OTM_PCT = -2.0       # strike no lower than this % vs price (negative = slightly in the money)
MIN_VOLUME = 50          # contracts traded today
MIN_OPEN_INTEREST = 200  # contracts already open (liquidity check)
MAX_SPREAD_PCT = 15.0    # bid/ask gap as % of the ask
TOP_N_PER_TICKER = 5     # how many matches to keep per stock
# ------------------------------------------------

OUT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", "results.json")

REASON_TEXT = {
    "noprice": "no price quoted",
    "cost": "too expensive for your cost limit",
    "otm": "strike outside your range",
    "liquidity": "too little volume or open interest",
    "spread": "bid/ask gap too wide",
}


def get_spot(tk):
    try:
        return float(tk.fast_info["last_price"])
    except Exception:
        hist = tk.history(period="5d")
        return float(hist["Close"].iloc[-1])


def num(value, default=0.0):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def scan_ticker(symbol):
    tk = yf.Ticker(symbol)
    spot = get_spot(tk)
    today = date.today()
    matches = []
    checked = 0
    blocked = {k: 0 for k in REASON_TEXT}

    for exp in tk.options:
        days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if days < MIN_DAYS or days > MAX_DAYS:
            continue

        calls = tk.option_chain(exp).calls
        for _, c in calls.iterrows():
            checked += 1
            bid = num(c.get("bid"))
            ask = num(c.get("ask"))
            price = ask if ask > 0 else num(c.get("lastPrice"))
            if price <= 0:
                blocked["noprice"] += 1
                continue

            strike = float(c["strike"])
            otm_pct = (strike / spot - 1) * 100
            cost = price * 100
            volume = int(num(c.get("volume")))
            oi = int(num(c.get("openInterest")))
            spread_pct = ((ask - bid) / ask * 100) if ask > 0 else 100.0

            if cost > MAX_COST_USD:
                blocked["cost"] += 1
                continue
            if not (MIN_OTM_PCT <= otm_pct <= MAX_OTM_PCT):
                blocked["otm"] += 1
                continue
            if volume < MIN_VOLUME or oi < MIN_OPEN_INTEREST:
                blocked["liquidity"] += 1
                continue
            if spread_pct > MAX_SPREAD_PCT:
                blocked["spread"] += 1
                continue

            breakeven = strike + price
            matches.append({
                "expiry": exp,
                "days": days,
                "strike": strike,
                "cost_usd": round(cost, 2),
                "breakeven": round(breakeven, 2),
                "needs_rise_pct": round((breakeven / spot - 1) * 100, 1),
                "volume": volume,
                "open_interest": oi,
                "spread_pct": round(spread_pct, 1),
                "iv_pct": round(num(c.get("impliedVolatility")) * 100),
            })

    matches.sort(key=lambda m: (m["needs_rise_pct"], -m["volume"]))
    matches = matches[:TOP_N_PER_TICKER]

    top_reason = None
    if not matches and checked:
        key = max(blocked, key=blocked.get)
        top_reason = f"{blocked[key]} of {checked} contracts were {REASON_TEXT[key]}"

    return {
        "ticker": symbol,
        "spot": round(spot, 2),
        "checked": checked,
        "matches": matches,
        "why_none": top_reason,
        "error": None,
    }


def main():
    tickers = []
    for symbol in TICKERS:
        print(f"Scanning {symbol} ...")
        try:
            result = scan_ticker(symbol)
        except Exception as e:  # keep going if one stock fails
            print(f"  Failed: {e}")
            result = {"ticker": symbol, "spot": None, "checked": 0,
                      "matches": [], "why_none": None, "error": str(e)[:200]}
        print(f"  {len(result['matches'])} matches")
        tickers.append(result)
        time.sleep(1)

    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "settings": {
            "max_cost_usd": MAX_COST_USD,
            "min_days": MIN_DAYS,
            "max_days": MAX_DAYS,
            "max_otm_pct": MAX_OTM_PCT,
            "min_volume": MIN_VOLUME,
            "min_open_interest": MIN_OPEN_INTEREST,
            "max_spread_pct": MAX_SPREAD_PCT,
        },
        "tickers": tickers,
    }

    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    print(f"Saved {OUT_FILE}")


if __name__ == "__main__":
    main()
