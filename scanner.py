"""
Call options data grabber for the phone page.

Reads the stock list from tickers.json, pulls call option prices (about 1 month
out to about 2 years out) from Yahoo, and saves them to docs/results.json.
The phone page (docs/index.html) does all the picking and maths itself, so your
budget and targets can change without waiting for a new scan.

GitHub runs this 3 times a day on weekdays (see .github/workflows/scan.yml).
You can also run it on your PC:  python scanner.py
"""

import json
import os
import sys
import time
from datetime import date, datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
TICKERS_FILE = os.path.join(HERE, "tickers.json")
OUT_FILE = os.path.join(HERE, "docs", "results.json")

MIN_DAYS = 20        # ignore expiries sooner than this
MAX_DAYS = 800       # ignore expiries further than this (about 2 years plus a bit)
MIN_STRIKE = 0.6     # keep strikes from 60% of the share price...
MAX_STRIKE = 3.0     # ...up to 300% of the share price

# Scheduled runs only count if US Eastern time is inside one of these windows.
# (GitHub's clock is UTC, so each run is scheduled twice - once for summer time
# and once for winter time - and the wrong one is skipped here.)
ET_WINDOWS = [(9 * 60 + 20, 10 * 60 + 30),    # just after the open
              (12 * 60, 13 * 60),             # midday
              (15 * 60, 16 * 60)]             # near the close


def in_window_now():
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    mins = now.hour * 60 + now.minute
    return any(a <= mins <= b for a, b in ET_WINDOWS)


def num(value, default=0.0):
    try:
        import pandas as pd
        if value is None or pd.isna(value):
            return default
        return float(value)
    except Exception:
        return default


def get_spot(tk):
    try:
        return float(tk.fast_info["last_price"])
    except Exception:
        hist = tk.history(period="5d")
        return float(hist["Close"].iloc[-1])


def scan_ticker(yf, symbol):
    tk = yf.Ticker(symbol)
    spot = get_spot(tk)
    today = date.today()
    expiries = []

    for exp in tk.options:
        days = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        if days < MIN_DAYS or days > MAX_DAYS:
            continue
        calls = tk.option_chain(exp).calls
        rows = []
        for _, c in calls.iterrows():
            strike = float(c["strike"])
            if strike < spot * MIN_STRIKE or strike > spot * MAX_STRIKE:
                continue
            bid = num(c.get("bid"))
            ask = num(c.get("ask"))
            last = num(c.get("lastPrice"))
            if ask <= 0 and last <= 0:
                continue
            # [strike, bid, ask, last, implied volatility, volume, open interest]
            rows.append([
                round(strike, 2), round(bid, 2), round(ask, 2), round(last, 2),
                round(num(c.get("impliedVolatility")), 3),
                int(num(c.get("volume"))), int(num(c.get("openInterest"))),
            ])
        if rows:
            expiries.append({"date": exp, "days": days, "calls": rows})
        time.sleep(0.3)

    return {"ticker": symbol, "spot": round(spot, 2), "expiries": expiries, "error": None}


def main():
    is_scheduled = os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    if is_scheduled and not in_window_now():
        print("Outside the update windows (DST double-run) - skipping.")
        return 0

    import yfinance as yf

    with open(TICKERS_FILE, encoding="utf-8") as f:
        stocks = json.load(f)

    tickers = []
    for s in stocks:
        symbol = s["symbol"].strip().upper()
        print(f"Scanning {symbol} ...")
        try:
            result = scan_ticker(yf, symbol)
        except Exception as e:  # keep going if one stock fails
            print(f"  Failed: {e}")
            result = {"ticker": symbol, "spot": None, "expiries": [], "error": str(e)[:200]}
        result["name"] = s.get("name", symbol)
        n = sum(len(x["calls"]) for x in result["expiries"])
        print(f"  {len(result['expiries'])} expiries, {n} calls")
        tickers.append(result)
        time.sleep(1)

    if not any(t["expiries"] for t in tickers):
        print("Nothing came back for any stock - keeping the old results.")
        return 1

    payload = {
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fields": ["strike", "bid", "ask", "last", "iv", "volume", "open_interest"],
        "tickers": tickers,
    }
    os.makedirs(os.path.dirname(OUT_FILE), exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"Saved {OUT_FILE} ({os.path.getsize(OUT_FILE) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
