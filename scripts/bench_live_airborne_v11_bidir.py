"""Bidirectional bench for v1.1 — 30 symbols, 1h, scaled R/R for 10x leverage scalping.

Implements v1.1 logic inline (no Strategy class) for both long + short directions.
Stops/TPs scaled to 1/10 of original sweep — assumes 10x leverage so coin-price %
of 0.30%/0.60% = capital % of 3%/6%.

Trade simulation per symbol:
  for each bar i:
    1) update v1.1 state machine (long_setup or short_setup)
    2) if signal fired and not in_pos: open trade
    3) if in_pos: check stop/TP at next bar's high/low
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import importlib
import logging
import sys
import time
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

Freq = Literal["1m", "15m", "1h", "4h"]
_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

import signals
from signals.airborne_bb_reversal import RETRACE_RATIO

logger = logging.getLogger("bench_v11_bidir")

# Scaled 1/10 R/R combos (coin-price % — capital % at 10x = 10 * this)
SWEEP_RR = [
    (0.0005, 0.0010, "0.05/0.10 (1:2)"),
    (0.0010, 0.0020, "0.10/0.20 (1:2)"),
    (0.0015, 0.0030, "0.15/0.30 (1:2)"),
    (0.0020, 0.0040, "0.20/0.40 (1:2)"),
    (0.0030, 0.0060, "0.30/0.60 (1:2)"),  # default
    (0.0010, 0.0030, "0.10/0.30 (1:3)"),
    (0.0020, 0.0060, "0.20/0.60 (1:3)"),
    (0.0005, 0.0020, "0.05/0.20 (1:4)"),
]

FREQ_RULE: dict[Freq, str] = {"1m": "1min", "15m": "15min", "1h": "1h", "4h": "4h"}

# v1.1 constants
BB_WINDOW = 20
BB_STD = 2.0
MIN_MARGIN = 0.001
MIN_BODY = 0.005


def _resample(df_1m: pd.DataFrame, freq: Freq) -> pd.DataFrame:
    if freq == "1m": return df_1m
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (df_1m[cols].resample(FREQ_RULE[freq], label="right", closed="right")
            .agg({k: agg[k] for k in cols}).dropna(subset=["close"]))


def _load_universe(months: int, freq: Freq, symbols: list[str]) -> dict[str, pd.DataFrame]:
    cache_dir = _REPO / "data" / "cache" / "binance_1m"
    selected = {}
    for sym in symbols:
        path = cache_dir / f"{sym}.parquet"
        if not path.exists():
            logger.warning("  %s: cache missing, skip", sym)
            continue
        p1m = pd.read_parquet(path)
        if p1m.index.tz is None:
            p1m = p1m.tz_localize("UTC")
        first_ts = p1m.index.max() - pd.DateOffset(months=months)
        p1m = p1m.loc[first_ts:]
        panel = _resample(p1m, freq)
        if len(panel) < 60: continue
        selected[sym] = panel
        logger.info("  %-12s @ %s: %d bars  [%s..%s]", sym, freq, len(panel),
                    panel.index[0].date(), panel.index[-1].date())
        del p1m
    gc.collect()
    return selected


def simulate_bidir(panel: pd.DataFrame, stop: float, tp: float, cost_bps: float) -> list[dict]:
    """Bidirectional v1.1 sim. Returns list of trade dicts."""
    bb = signals.compute("bollinger", close=panel["close"], window=BB_WINDOW, n_std=BB_STD)
    upper, lower = bb["upper"].values, bb["lower"].values
    closes = panel["close"].values
    opens = panel["open"].values
    highs = panel["high"].values
    lows = panel["low"].values
    body_pct = np.abs(closes - opens) / np.where(opens > 0, opens, 1.0)

    upper_thr = upper * (1 + MIN_MARGIN)
    lower_thr = lower * (1 - MIN_MARGIN)

    n = len(panel)
    # Pre-compute breakouts (with previous-bar margin check)
    upper_break = np.zeros(n, dtype=bool)
    lower_break = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(upper_thr[i]) or np.isnan(upper_thr[i-1]): continue
        ub = (closes[i] > upper_thr[i]) and (closes[i-1] <= upper_thr[i-1]) and (body_pct[i] >= MIN_BODY)
        lb = (closes[i] < lower_thr[i]) and (closes[i-1] >= lower_thr[i-1]) and (body_pct[i] >= MIN_BODY)
        upper_break[i] = ub
        lower_break[i] = lb

    # State machine
    state = 0
    base = np.nan
    extreme = np.nan
    fires: list[tuple[int, str, float]] = []  # (bar_index, side, entry_price)

    for i in range(n):
        if state == 0:
            if upper_break[i]:
                state = 2; base = closes[i]; extreme = highs[i]
            elif lower_break[i]:
                state = 1; base = closes[i]; extreme = lows[i]
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, lows[i])
            trig = extreme + RETRACE_RATIO * (base - extreme)
            if closes[i] >= trig:
                fires.append((i, "long", closes[i]))
                state = 0; base = np.nan; extreme = np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, highs[i])
            trig = extreme - RETRACE_RATIO * (extreme - base)
            if closes[i] <= trig:
                fires.append((i, "short", closes[i]))
                state = 0; base = np.nan; extreme = np.nan

    # Trade simulation
    trades = []
    in_pos = False
    pos_side = None
    pos_entry = 0.0
    pos_entry_i = 0
    fire_idx = 0
    cost = cost_bps / 10000.0
    times = panel.index

    for i in range(n):
        # Check exit if in position
        if in_pos:
            if pos_side == "long":
                sl_px = pos_entry * (1 - stop)
                tp_px = pos_entry * (1 + tp)
                exit_reason = None
                exit_px = None
                if lows[i] <= sl_px:
                    exit_reason = "stop_loss"; exit_px = sl_px
                elif highs[i] >= tp_px:
                    exit_reason = "take_profit"; exit_px = tp_px
                if exit_reason:
                    ret = (exit_px / pos_entry) - 1 - 2 * cost
                    trades.append({"side": "long", "entry_ts": times[pos_entry_i],
                                   "exit_ts": times[i], "entry": pos_entry,
                                   "exit": exit_px, "ret": ret, "exit_reason": exit_reason})
                    in_pos = False
            else:  # short
                sl_px = pos_entry * (1 + stop)
                tp_px = pos_entry * (1 - tp)
                exit_reason = None
                exit_px = None
                if highs[i] >= sl_px:
                    exit_reason = "stop_loss"; exit_px = sl_px
                elif lows[i] <= tp_px:
                    exit_reason = "take_profit"; exit_px = tp_px
                if exit_reason:
                    ret = 1 - (exit_px / pos_entry) - 2 * cost
                    trades.append({"side": "short", "entry_ts": times[pos_entry_i],
                                   "exit_ts": times[i], "entry": pos_entry,
                                   "exit": exit_px, "ret": ret, "exit_reason": exit_reason})
                    in_pos = False

        # Check entry from queued fires
        if not in_pos and fire_idx < len(fires) and fires[fire_idx][0] == i:
            _, side, entry = fires[fire_idx]
            in_pos = True
            pos_side = side
            pos_entry = entry
            pos_entry_i = i
            fire_idx += 1
        elif fire_idx < len(fires) and fires[fire_idx][0] <= i:
            # Skip fires that occurred while we were in position
            while fire_idx < len(fires) and fires[fire_idx][0] <= i:
                fire_idx += 1

    return trades


def aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0, "payoff": 0, "PF": 0, "exp": 0,
                "long_n": 0, "short_n": 0, "long_PF": 0, "short_PF": 0}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    total_profit = wins.sum() if len(wins) else 0
    total_loss = -losses.sum() if len(losses) else 0
    PF = total_profit / total_loss if total_loss > 0 else float("inf")
    avg_w = wins.mean() if len(wins) else 0
    avg_l = losses.mean() if len(losses) else 0
    payoff = abs(avg_w / avg_l) if avg_l < 0 else float("inf")
    exp = rets.mean()
    win_rate = len(wins) / len(trades)

    # Per-side breakdown
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    def side_pf(ts):
        if not ts: return 0
        r = np.array([t["ret"] for t in ts])
        p = r[r > 0].sum()
        l = -r[r <= 0].sum()
        return p / l if l > 0 else float("inf")

    return {"trades": len(trades), "win_rate": win_rate, "payoff": payoff,
            "PF": PF, "exp": exp,
            "long_n": len(longs), "short_n": len(shorts),
            "long_PF": side_pf(longs), "short_PF": side_pf(shorts)}


def run_combo(panels: dict[str, pd.DataFrame], stop: float, tp: float,
              cost_bps: float) -> dict:
    all_trades = []
    for sym, panel in panels.items():
        all_trades.extend(simulate_bidir(panel, stop, tp, cost_bps))
    return aggregate(all_trades)


def fmt_row(label, stop, tp, m):
    verdict = "PASS" if (m["PF"] > 1.0 and m["exp"] > 0) else "LOSER"
    return (f"  {label:<20} stop={stop*100:.3f}% tp={tp*100:.3f}%  "
            f"trades={m['trades']:>6}  win={m['win_rate']*100:5.2f}%  "
            f"payoff={m['payoff']:5.2f}  PF={m['PF']:6.3f}  exp={m['exp']*100:+8.5f}%  "
            f"L={m['long_n']:>4}/{m['long_PF']:5.2f}  S={m['short_n']:>4}/{m['short_PF']:5.2f}  {verdict}")


def parse_args(argv=None):
    p = argparse.ArgumentParser(prog="bench_v11_bidir")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--symbols", type=str, default="ALL",
                   help="comma-separated or 'ALL' for cache scan")
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--freq", type=str, default="1h", choices=list(FREQ_RULE.keys()))
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = parse_args(argv)
    t0 = time.time()

    if args.symbols == "ALL":
        cache = _REPO / "data" / "cache" / "binance_1m"
        symbols = [f.stem for f in cache.glob("*.parquet") if not f.stem.startswith("_")]
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]

    logger.info(f"Loading {len(symbols)} symbols @ {args.freq}, {args.months}mo...")
    panels = _load_universe(args.months, args.freq, symbols)
    if not panels:
        logger.error("No data — abort"); return 3

    print("\n" + "=" * 140)
    print(f"live_airborne_bb_reversal v1.1 BIDIR  |  freq={args.freq}  |  {args.months}mo  |  "
          f"symbols={len(panels)}  |  cost={args.cost_bps:.0f}bp")
    print(f"  R/R sweep scaled 1/10 for 10x leverage (coin% = capital%/10)")
    print("=" * 140)

    rows = []
    for stop, tp, label in SWEEP_RR:
        c0 = time.time()
        m = run_combo(panels, stop, tp, args.cost_bps)
        rows.append((label, stop, tp, m))
        logger.info("  %s done in %.1fs (trades=%d PF=%.3f exp=%+.5f%%)",
                    label, time.time() - c0, m["trades"], m["PF"], m["exp"]*100)

    print()
    print(f"  {'label':<20} {'stop':<8} {'tp':<8}  {'trades':>10}  {'win':>6}  "
          f"{'payoff':>6}  {'PF':>6}  {'exp':>9}  {'L_n/PF':<12} {'S_n/PF':<12} verdict")
    print("  " + "-" * 136)
    for label, stop, tp, m in sorted(rows, key=lambda r: r[3]["PF"], reverse=True):
        print(fmt_row(label, stop, tp, m))
    print("=" * 140)
    print(f"elapsed: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main() or 0)
