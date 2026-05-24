"""Sweep v1.1 (margin, body) parameter space — count signals per combo on 1y BTC 1h.

User's goal: find (min_close_margin, min_body) that produces signal frequency
closest to original 에어본 indicator. We can't query the original on historical
data, but can quantify how each combo changes signal density vs v1 (no filter).

Output: table of (margin, body, n_signals, signal/100bars) so user can pick the
combo that matches the original's observed density on their chart.
"""
from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import signals
from signals.airborne_bb_reversal import RETRACE_RATIO, evaluate_long_fire

CACHE = _REPO / "data" / "cache" / "binance_1m" / "BTCUSDT.parquet"


def load_1h(months: int = 12) -> pd.DataFrame:
    p1m = pd.read_parquet(CACHE)
    if p1m.index.tz is None:
        p1m = p1m.tz_localize("UTC")
    first_ts = p1m.index.max() - pd.DateOffset(months=months)
    p1m = p1m.loc[first_ts:]
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    return (p1m.resample("1h", label="right", closed="right")
              .agg(agg).dropna(subset=["close"]))


def simulate_v11(df: pd.DataFrame, min_margin: float, min_body: float,
                 bb_window: int = 20, bb_std: float = 2.0,
                 retrace: float = RETRACE_RATIO) -> dict:
    """Run v1.1 simulation: count long+short setups + fires."""
    bb = signals.compute("bollinger", close=df["close"], window=bb_window, n_std=bb_std)
    upper, lower = bb["upper"], bb["lower"]
    body_pct = (df["close"] - df["open"]).abs() / df["open"]

    upper_thr = upper * (1 + min_margin)
    lower_thr = lower * (1 - min_margin)

    # close-based breakout w/ margin + body filter
    upper_break = ((df["close"] > upper_thr)
                   & (df["close"].shift(1) <= upper_thr.shift(1))
                   & (body_pct >= min_body))
    lower_break = ((df["close"] < lower_thr)
                   & (df["close"].shift(1) >= lower_thr.shift(1))
                   & (body_pct >= min_body))

    # State machine (Python, bar-by-bar)
    n = len(df)
    state = 0
    base = np.nan
    extreme = np.nan
    long_fires = 0
    short_fires = 0
    long_setups = 0
    short_setups = 0
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    ub = upper_break.values
    lb = lower_break.values

    for i in range(n):
        if state == 0:
            if ub[i]:
                state = 2
                base = close[i]
                extreme = high[i]
                short_setups += 1
            elif lb[i]:
                state = 1
                base = close[i]
                extreme = low[i]
                long_setups += 1
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, low[i])
            trig = extreme + retrace * (base - extreme)
            if close[i] >= trig:
                long_fires += 1
                state = 0
                base = np.nan
                extreme = np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, high[i])
            trig = extreme - retrace * (extreme - base)
            if close[i] <= trig:
                short_fires += 1
                state = 0
                base = np.nan
                extreme = np.nan

    return {
        "long_setups": long_setups,
        "short_setups": short_setups,
        "long_fires": long_fires,
        "short_fires": short_fires,
        "total_fires": long_fires + short_fires,
        "bars": n,
        "fires_per_100bars": (long_fires + short_fires) / n * 100,
    }


def main():
    print("Loading BTCUSDT 1h (1y)...")
    df = load_1h(12)
    print(f"  {len(df)} bars  [{df.index[0]} .. {df.index[-1]}]")

    margins = [0.0, 0.0005, 0.001, 0.0015, 0.002, 0.003]
    bodies  = [0.0, 0.002, 0.003, 0.005, 0.007, 0.010]

    print(f"\nSweeping {len(margins)} margins x {len(bodies)} bodies = {len(margins)*len(bodies)} combos\n")

    rows = []
    for m, b in product(margins, bodies):
        r = simulate_v11(df, m, b)
        rows.append({"margin_%": m*100, "body_%": b*100, **r})

    rdf = pd.DataFrame(rows)
    print(f"{'margin %':>8} {'body %':>7} | {'long_set':>8} {'short_set':>9} {'long_fire':>9} {'short_fire':>10} {'total':>6} {'/100bar':>8}")
    print("-" * 78)
    for _, r in rdf.iterrows():
        print(f"{r['margin_%']:>8.3f} {r['body_%']:>7.3f} | "
              f"{r['long_setups']:>8} {r['short_setups']:>9} "
              f"{r['long_fires']:>9} {r['short_fires']:>10} {r['total_fires']:>6} {r['fires_per_100bars']:>8.2f}")

    print(f"\n=== Reference values ===")
    print(f"  v1.1 default (margin=0.10%, body=0.50%):")
    ref = simulate_v11(df, 0.001, 0.005)
    print(f"    long_setups={ref['long_setups']}  short_setups={ref['short_setups']}")
    print(f"    long_fires={ref['long_fires']}  short_fires={ref['short_fires']}")
    print(f"    total={ref['total_fires']}  per_100bars={ref['fires_per_100bars']:.2f}")

    print(f"\n  v1 (no filter, margin=0%, body=0%):")
    ref0 = simulate_v11(df, 0.0, 0.0)
    print(f"    long_fires={ref0['long_fires']}  short_fires={ref0['short_fires']}")
    print(f"    total={ref0['total_fires']}  per_100bars={ref0['fires_per_100bars']:.2f}")

    print(f"\n  Reduction at v1.1 default: {(1 - ref['total_fires']/ref0['total_fires'])*100:.1f}%")


if __name__ == "__main__":
    main()
