"""Compare 3 band-riding mitigations on v1.1 signal — 1y BTC 1h.

For each candidate filter (A: band-riding count, B: BB squeeze, C: BB midline
exit), measure:
  - Number of signals fired
  - Hit rate (signal closes > entry within N bars)
  - Loss-signal ratio (signal closes worse than -X% within N bars)
  - Avg bars to BB midline reversion
  - Avg max-adverse-excursion (MAE)

Compares to v1.1 baseline (no extra filter). Output ranked by hit-rate gain.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

import signals
from signals.airborne_bb_reversal import RETRACE_RATIO

CACHE = _REPO / "data" / "cache" / "binance_1m" / "BTCUSDT.parquet"

# v1.1 defaults
MIN_MARGIN = 0.001
MIN_BODY = 0.005
BB_WINDOW = 20
BB_STD = 2.0
RETRACE = RETRACE_RATIO

# Evaluation horizon
HORIZON_BARS = 20  # measure outcome within next 20 bars after fire


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


def simulate_with_filter(df: pd.DataFrame, filter_name: str = "none",
                          **kwargs) -> list[dict]:
    """Run v1.1 simulation and apply optional filter. Returns list of fire events."""
    bb = signals.compute("bollinger", close=df["close"], window=BB_WINDOW, n_std=BB_STD)
    upper, mid, lower = bb["upper"], bb["middle"], bb["lower"]
    bandwidth = (upper - lower) / mid
    body_pct = (df["close"] - df["open"]).abs() / df["open"]

    upper_thr = upper * (1 + MIN_MARGIN)
    lower_thr = lower * (1 - MIN_MARGIN)

    upper_break = ((df["close"] > upper_thr)
                   & (df["close"].shift(1) <= upper_thr.shift(1))
                   & (body_pct >= MIN_BODY))
    lower_break = ((df["close"] < lower_thr)
                   & (df["close"].shift(1) >= lower_thr.shift(1))
                   & (body_pct >= MIN_BODY))

    # Filter precomputation
    if filter_name == "A":  # band-riding count
        lookback = kwargs.get("lookback", 10)
        max_outside = kwargs.get("max_outside", 2)
        bars_close_above = (df["close"] > upper).rolling(lookback).sum()
        bars_close_below = (df["close"] < lower).rolling(lookback).sum()
        filter_pass_short = bars_close_above <= max_outside
        filter_pass_long  = bars_close_below <= max_outside
    elif filter_name == "B":  # BB squeeze (bandwidth percentile)
        squeeze_window = kwargs.get("squeeze_window", 50)
        pctile = kwargs.get("pctile", 0.25)
        # bandwidth must be in bottom-X percentile of trailing window
        bw_q = bandwidth.rolling(squeeze_window).quantile(pctile)
        filter_pass_short = bandwidth <= bw_q
        filter_pass_long  = bandwidth <= bw_q
    else:
        filter_pass_short = pd.Series(True, index=df.index)
        filter_pass_long  = pd.Series(True, index=df.index)

    # State machine
    n = len(df)
    state = 0
    base = np.nan
    extreme = np.nan
    fires = []
    close = df["close"].values
    high = df["high"].values
    low = df["low"].values
    mid_arr = mid.values
    ub = upper_break.values
    lb = lower_break.values
    fps = filter_pass_short.values
    fpl = filter_pass_long.values

    for i in range(n):
        if state == 0:
            if ub[i] and fps[i]:
                state = 2
                base = close[i]
                extreme = high[i]
            elif lb[i] and fpl[i]:
                state = 1
                base = close[i]
                extreme = low[i]
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, low[i])
            trig = extreme + RETRACE * (base - extreme)
            if close[i] >= trig:
                fires.append({
                    "bar": i, "side": "long", "entry": close[i],
                    "mid": mid_arr[i], "ts": df.index[i],
                })
                state = 0
                base = np.nan
                extreme = np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, high[i])
            trig = extreme - RETRACE * (extreme - base)
            if close[i] <= trig:
                fires.append({
                    "bar": i, "side": "short", "entry": close[i],
                    "mid": mid_arr[i], "ts": df.index[i],
                })
                state = 0
                base = np.nan
                extreme = np.nan

    return fires


def evaluate_fires(fires: list[dict], df: pd.DataFrame,
                    horizon: int = HORIZON_BARS) -> dict:
    """For each fire, measure outcome: MAE, time to BB midline reversion,
    'success' (price reached midline before adverse-X%)."""
    if not fires:
        return {"n": 0}

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    successes = 0  # reached BB midline before -2% adverse
    failures = 0  # adverse -2% before midline
    mae_list = []
    bars_to_mid_list = []
    bars_to_fail_list = []

    for f in fires:
        i = f["bar"]
        entry = f["entry"]
        target = f["mid"]
        side = f["side"]
        end = min(i + horizon, len(df) - 1)
        adverse_threshold = 0.02  # 2% adverse

        reached_mid = False
        reached_fail = False
        worst = 0.0
        bars_to_mid = horizon
        bars_to_fail = horizon

        for j in range(i + 1, end + 1):
            if side == "long":
                worst = min(worst, (low[j] - entry) / entry)
                if not reached_mid and close[j] >= target:
                    reached_mid = True
                    bars_to_mid = j - i
                if not reached_fail and (low[j] - entry) / entry <= -adverse_threshold:
                    reached_fail = True
                    bars_to_fail = j - i
            else:  # short
                worst = max(worst, (high[j] - entry) / entry)  # adverse goes up
                if not reached_mid and close[j] <= target:
                    reached_mid = True
                    bars_to_mid = j - i
                if not reached_fail and (high[j] - entry) / entry >= adverse_threshold:
                    reached_fail = True
                    bars_to_fail = j - i

        mae_list.append(abs(worst))
        if reached_mid:
            bars_to_mid_list.append(bars_to_mid)
        if reached_fail:
            bars_to_fail_list.append(bars_to_fail)

        # Success: midline before fail
        if reached_mid and (not reached_fail or bars_to_mid < bars_to_fail):
            successes += 1
        elif reached_fail and (not reached_mid or bars_to_fail < bars_to_mid):
            failures += 1

    n = len(fires)
    return {
        "n": n,
        "success": successes,
        "failure": failures,
        "neither": n - successes - failures,
        "hit_rate": successes / n if n else 0,
        "fail_rate": failures / n if n else 0,
        "mae_avg": np.mean(mae_list) if mae_list else 0,
        "bars_to_mid_avg": np.mean(bars_to_mid_list) if bars_to_mid_list else 0,
        "bars_to_fail_avg": np.mean(bars_to_fail_list) if bars_to_fail_list else 0,
    }


def main():
    print("Loading BTCUSDT 1h (1y)...")
    df = load_1h(12)
    print(f"  {len(df)} bars\n")

    print(f"=== Outcome metrics ({HORIZON_BARS} bars horizon, -2% adverse threshold) ===")
    print(f"  success = price reached BB midline BEFORE adverse 2%")
    print(f"  failure = price reached adverse 2% BEFORE midline\n")

    configs = [
        ("baseline (v1.1)", "none", {}),
        ("A: band-riding (lookback=10, max_outside=2)",  "A", {"lookback": 10, "max_outside": 2}),
        ("A: band-riding (lookback=10, max_outside=1)",  "A", {"lookback": 10, "max_outside": 1}),
        ("A: band-riding (lookback=5,  max_outside=1)",  "A", {"lookback": 5,  "max_outside": 1}),
        ("B: squeeze (window=50, pctile=0.25)",  "B", {"squeeze_window": 50, "pctile": 0.25}),
        ("B: squeeze (window=50, pctile=0.50)",  "B", {"squeeze_window": 50, "pctile": 0.50}),
        ("B: squeeze (window=100, pctile=0.25)", "B", {"squeeze_window": 100, "pctile": 0.25}),
    ]

    print(f"{'config':<48} {'n_fires':>8} {'hit%':>6} {'fail%':>6} {'MAE%':>6} {'b→mid':>7}")
    print("-" * 92)
    baseline = None
    for name, fname, kwargs in configs:
        fires = simulate_with_filter(df, fname, **kwargs)
        m = evaluate_fires(fires, df)
        if name.startswith("baseline"):
            baseline = m
        if m["n"] == 0:
            print(f"{name:<48} {0:>8}")
            continue
        print(f"{name:<48} {m['n']:>8} {m['hit_rate']*100:>5.1f}% {m['fail_rate']*100:>5.1f}% "
              f"{m['mae_avg']*100:>5.2f}% {m['bars_to_mid_avg']:>7.1f}")

    if baseline:
        print(f"\n=== vs baseline ({baseline['n']} fires, hit={baseline['hit_rate']*100:.1f}%, fail={baseline['fail_rate']*100:.1f}%) ===")
        for name, fname, kwargs in configs[1:]:
            fires = simulate_with_filter(df, fname, **kwargs)
            m = evaluate_fires(fires, df)
            if m["n"] == 0:
                continue
            d_hit = (m["hit_rate"] - baseline["hit_rate"]) * 100
            d_fail = (m["fail_rate"] - baseline["fail_rate"]) * 100
            d_n = m["n"] - baseline["n"]
            print(f"  {name:<48} Δhit={d_hit:+5.1f}pp  Δfail={d_fail:+5.1f}pp  Δn={d_n:+4d}")


if __name__ == "__main__":
    main()
