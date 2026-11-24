"""
Tesla vs SpaceX pair tracker - data for the phone page (docs/pair.html).

Pulls daily prices for TSLA and SPCX since SpaceX listed (12 June 2026),
plus the latest live-ish price, and works out:
  - how closely the two move together and how big SpaceX's moves are vs Tesla's
  - today's gap: did SpaceX move as much as it "should" have given Tesla's move?
  - the stretch score: how far the SPCX/TSLA price ratio is from its usual level
  - history tests of the "when one's up, buy the other" ideas

Saves docs/pairs.json. GitHub runs it every 15 minutes while the US market is open
(see .github/workflows/scan.yml). You can also run it on your PC:  python pairs.py
"""

import json
import math
import os
import sys
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_FILE = os.path.join(HERE, "docs", "pairs.json")

A, B = "TSLA", "SPCX"          # A = Tesla (the leader we watch), B = SpaceX
NAMES = {A: "Tesla", B: "SpaceX"}
START = "2026-06-12"           # SpaceX's first day of trading
WINDOW = 20                    # trading days used for the "usual" ratio (about a month)
Z_SIGNAL = 2.0                 # stretch score that counts as a signal
BIG_MOVE = 2.0                 # % daily move that counts as "jumped"
LAG_Z = 1.0                    # how unusual today's gap must be to count as lagging


# ---------- when to run ----------

def market_window_now():
    """True on weekdays from 9:25am to 4:30pm New York time."""
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    if now.weekday() >= 5:
        return False
    mins = now.hour * 60 + now.minute
    return 9 * 60 + 25 <= mins <= 16 * 60 + 30


# ---------- data ----------

def load_prices():
    import pandas as pd
    import yfinance as yf
    from zoneinfo import ZoneInfo

    raw = yf.download([A, B], start=START, interval="1d", auto_adjust=True, progress=False)
    df = raw["Close"][[A, B]].dropna()
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()

    # swap in the latest price for today so the page is live during the day
    live = {}
    for sym in (A, B):
        try:
            live[sym] = float(yf.Ticker(sym).fast_info["last_price"])
        except Exception:
            pass
    if len(live) == 2 and market_window_now():
        today = pd.Timestamp(datetime.now(ZoneInfo("America/New_York")).date())
        df.loc[today, A] = live[A]
        df.loc[today, B] = live[B]
        df = df.sort_index()
    return df


# ---------- maths ----------

def clean(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except Exception:
        return None
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


def verdict(n, win, avg, base):
    if n < 8:
        return "few", "Not enough cases yet to tell"
    if win >= 60 and avg > base:
        return "works", "Has worked so far"
    if win <= 40 and avg < base:
        return "opposite", "Has done the opposite so far"
    return "flip", "No real edge so far - about a coin flip"


def test(name, explain, mask, outcome, baseline, win_word):
    """mask: days the idea fires. outcome: % result you'd have got after each day."""
    hits = outcome[mask].dropna()
    n = int(len(hits))
    win = float((hits > 0).mean() * 100) if n else 0.0
    avg = float(hits.mean()) if n else 0.0
    base = float(baseline.dropna().mean()) if baseline.dropna().size else 0.0
    tone, text = verdict(n, win, avg, base)
    last = [d.strftime("%Y-%m-%d") for d in hits.index[-3:]]
    return {
        "name": name, "explain": explain, "n": n, "win_rate": clean(win, 0),
        "avg": clean(avg), "baseline": clean(base), "win_word": win_word,
        "tone": tone, "verdict": text, "recent": last,
    }


def compute(df):
    import numpy as np

    ra = df[A].pct_change() * 100            # Tesla daily % move
    rb = df[B].pct_change() * 100            # SpaceX daily % move
    both = ra.notna() & rb.notna()

    xa, xb = ra[both], rb[both]

    def beta_of(x, y):
        return float(np.cov(y, x)[0, 1] / np.var(x, ddof=1)) if len(x) > 5 else float("nan")

    beta = beta_of(xa, xb)
    beta30 = beta_of(xa.tail(30), xb.tail(30))
    corr = float(xa.corr(xb))
    corr30 = float(xa.tail(30).corr(xb.tail(30)))

    # today's gap: what SpaceX did vs what Tesla's move says it "should" have done
    resid = rb - beta * ra
    resid_sd = float(resid.std())
    gap_z = resid / resid_sd

    # stretch score: how far the SPCX/TSLA price ratio is from its usual level
    ratio = df[B] / df[A]
    z = (ratio - ratio.rolling(WINDOW).mean()) / ratio.rolling(WINDOW).std()

    # forward results (from that day's close)
    fwd_b1 = rb.shift(-1)
    fwd_a1 = ra.shift(-1)
    fwd_b3 = (df[B].shift(-3) / df[B] - 1) * 100
    rel5 = ((df[B].shift(-5) / df[B]) - (df[A].shift(-5) / df[A])) * 100   # SpaceX beat Tesla by, over 5 days
    rel1 = fwd_b1 - fwd_a1

    tests = [
        test("Tesla jumps - buy SpaceX next day",
             f"Days Tesla closed up {BIG_MOVE:g}% or more. Result = SpaceX's move the next day.",
             ra >= BIG_MOVE, fwd_b1, fwd_b1, "SpaceX up next day"),
        test("Tesla jumps - hold SpaceX 3 days",
             f"Same days, but holding SpaceX for 3 days instead of 1.",
             ra >= BIG_MOVE, fwd_b3, fwd_b3, "SpaceX up after 3 days"),
        test("SpaceX jumps - buy Tesla next day",
             f"Days SpaceX closed up {BIG_MOVE:g}% or more. Result = Tesla's move the next day.",
             rb >= BIG_MOVE, fwd_a1, fwd_a1, "Tesla up next day"),
        test("SpaceX lagged Tesla - does it catch up?",
             "Days SpaceX did noticeably worse than Tesla's move said it should. "
             "Result = how much SpaceX beat Tesla the next day.",
             gap_z <= -LAG_Z, rel1, rel1, "SpaceX beat Tesla next day"),
        test("Stretch score very low - SpaceX cheap vs Tesla",
             f"Days the stretch score was -{Z_SIGNAL:g} or lower. "
             "Result = how much SpaceX beat Tesla over the next 5 days.",
             z <= -Z_SIGNAL, rel5, rel5, "SpaceX beat Tesla over 5 days"),
        test("Stretch score very high - SpaceX dear vs Tesla",
             f"Days the stretch score was +{Z_SIGNAL:g} or higher. "
             "Result = how much Tesla beat SpaceX over the next 5 days.",
             z >= Z_SIGNAL, -rel5, -rel5, "Tesla beat SpaceX over 5 days"),
    ]

    zt = clean(z.iloc[-1])
    if zt is None:
        signal = ("none", "Not enough history yet")
    elif zt <= -Z_SIGNAL:
        signal = ("cheap", "SpaceX looks cheap vs Tesla")
    elif zt >= Z_SIGNAL:
        signal = ("dear", "SpaceX looks expensive vs Tesla")
    elif abs(zt) >= 1:
        signal = ("lean", "Drifting from normal - worth watching")
    else:
        signal = ("normal", "Normal - nothing stretched")

    gt = clean(gap_z.iloc[-1])
    if gt is None:
        today_tag = "none"
    elif gt <= -LAG_Z:
        today_tag = "lagging"
    elif gt >= LAG_Z:
        today_tag = "ahead"
    else:
        today_tag = "inline"

    last_date = df.index[-1]
    return {
        "updated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "a": A, "b": B, "names": NAMES,
        "window": WINDOW, "z_signal": Z_SIGNAL, "big_move": BIG_MOVE,
        "days": int(both.sum()),
        "last_date": last_date.strftime("%Y-%m-%d"),
        "price": {A: clean(df[A].iloc[-1]), B: clean(df[B].iloc[-1])},
        "today": {
            "a_move": clean(ra.iloc[-1]), "b_move": clean(rb.iloc[-1]),
            "b_expected": clean(beta * ra.iloc[-1]), "gap": clean(resid.iloc[-1]),
            "gap_z": gt, "tag": today_tag,
        },
        "stats": {"corr": clean(corr), "corr30": clean(corr30), "beta": clean(beta), "beta30": clean(beta30)},
        "z": zt, "signal": {"code": signal[0], "text": signal[1]},
        "tests": tests,
        "history": {
            "dates": [d.strftime("%Y-%m-%d") for d in df.index],
            A: [clean(v) for v in df[A]],
            B: [clean(v) for v in df[B]],
            "z": [clean(v) for v in z],
        },
    }


def main():
    is_scheduled = os.environ.get("GITHUB_EVENT_NAME") == "schedule"
    if is_scheduled and not market_window_now():
        print("Market closed - skipping pair update.")
        return 0
    df = load_prices()
    if len(df) < WINDOW + 5:
        print(f"Only {len(df)} days of prices came back - keeping the old file.")
        return 1
    out = compute(df)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"Saved {OUT_FILE}: {out['days']} days, stretch {out['z']}, {out['signal']['text']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
