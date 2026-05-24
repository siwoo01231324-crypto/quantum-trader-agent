"""Hybrid bench: v1.1 signals on 1h, trade execution on 5m bars.

Models realistic scalping: indicator fires on 1h confirmed close → trade
opens at that price → stop/TP checked on 5m bar high/low (more granular than
1h). This properly accounts for stop-first vs tp-first ordering at the 5m
resolution (still imperfect within a 5m bar but ~12x better than 1h).
"""
from __future__ import annotations

import argparse
import gc
import logging
import sys
import time
from pathlib import Path
from typing import Literal

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

import signals
from signals.airborne_bb_reversal import RETRACE_RATIO

logger = logging.getLogger("bench_v11_5m_exit")

SWEEP_RR = [
    (0.0005, 0.0010, "0.05/0.10 (1:2)"),
    (0.0010, 0.0020, "0.10/0.20 (1:2)"),
    (0.0015, 0.0030, "0.15/0.30 (1:2)"),
    (0.0020, 0.0040, "0.20/0.40 (1:2)"),
    (0.0030, 0.0060, "0.30/0.60 (1:2)"),
    (0.0010, 0.0030, "0.10/0.30 (1:3)"),
    (0.0020, 0.0060, "0.20/0.60 (1:3)"),
    (0.0005, 0.0020, "0.05/0.20 (1:4)"),
]

BB_WINDOW = 20
BB_STD = 2.0
MIN_MARGIN = 0.001
MIN_BODY = 0.005


def _resample(df_1m: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (df_1m[cols].resample(rule, label="right", closed="right")
            .agg({k: agg[k] for k in cols}).dropna(subset=["close"]))


def _load_universe(months: int, symbols: list[str]) -> dict[str, tuple[pd.DataFrame, pd.DataFrame]]:
    """Returns {symbol: (panel_1h, panel_5m)}."""
    cache_dir = _REPO / "data" / "cache" / "binance_1m"
    selected = {}
    for sym in symbols:
        path = cache_dir / f"{sym}.parquet"
        if not path.exists():
            logger.warning("  %s: cache missing", sym); continue
        p1m = pd.read_parquet(path)
        if p1m.index.tz is None: p1m = p1m.tz_localize("UTC")
        first_ts = p1m.index.max() - pd.DateOffset(months=months)
        p1m = p1m.loc[first_ts:]
        panel_1h = _resample(p1m, "1h")
        panel_5m = _resample(p1m, "5min")
        if len(panel_1h) < 60: continue
        selected[sym] = (panel_1h, panel_5m)
        logger.info("  %-12s 1h=%d  5m=%d  [%s..%s]", sym, len(panel_1h), len(panel_5m),
                    panel_1h.index[0].date(), panel_1h.index[-1].date())
        del p1m
    gc.collect()
    return selected


def extract_fires(panel_1h: pd.DataFrame) -> list[tuple[pd.Timestamp, str, float]]:
    """Returns list of (entry_ts, side, entry_price) from v1.1 1h sim."""
    bb = signals.compute("bollinger", close=panel_1h["close"], window=BB_WINDOW, n_std=BB_STD)
    upper = bb["upper"].values
    lower = bb["lower"].values
    closes = panel_1h["close"].values
    opens = panel_1h["open"].values
    highs = panel_1h["high"].values
    lows = panel_1h["low"].values
    body_pct = np.abs(closes - opens) / np.where(opens > 0, opens, 1.0)

    upper_thr = upper * (1 + MIN_MARGIN)
    lower_thr = lower * (1 - MIN_MARGIN)
    n = len(panel_1h)

    upper_break = np.zeros(n, dtype=bool)
    lower_break = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(upper_thr[i]) or np.isnan(upper_thr[i-1]): continue
        upper_break[i] = ((closes[i] > upper_thr[i]) and (closes[i-1] <= upper_thr[i-1])
                          and (body_pct[i] >= MIN_BODY))
        lower_break[i] = ((closes[i] < lower_thr[i]) and (closes[i-1] >= lower_thr[i-1])
                          and (body_pct[i] >= MIN_BODY))

    state = 0
    base = np.nan
    extreme = np.nan
    fires = []
    ts = panel_1h.index

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
                fires.append((ts[i], "long", float(closes[i])))
                state = 0; base = np.nan; extreme = np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, highs[i])
            trig = extreme - RETRACE_RATIO * (extreme - base)
            if closes[i] <= trig:
                fires.append((ts[i], "short", float(closes[i])))
                state = 0; base = np.nan; extreme = np.nan
    return fires


def execute_trade_5m(panel_5m: pd.DataFrame, entry_ts: pd.Timestamp, side: str,
                     entry_px: float, stop: float, tp: float, cost_bps: float,
                     max_hold_5m_bars: int = 24*12) -> dict | None:
    """Walk 5m bars after entry_ts, return on first stop/tp hit. Stop priority
    on same-bar dual hit (conservative)."""
    idx = panel_5m.index.searchsorted(entry_ts, side='right')
    if idx >= len(panel_5m): return None
    end = min(idx + max_hold_5m_bars, len(panel_5m))
    highs = panel_5m["high"].values
    lows = panel_5m["low"].values
    cost = cost_bps / 10000.0

    if side == "long":
        sl_px = entry_px * (1 - stop)
        tp_px = entry_px * (1 + tp)
        for j in range(idx, end):
            if lows[j] <= sl_px:
                ret = (sl_px / entry_px) - 1 - 2 * cost
                return {"side": "long", "entry": entry_px, "exit": sl_px, "ret": ret,
                        "reason": "stop_loss", "entry_ts": entry_ts,
                        "exit_ts": panel_5m.index[j], "bars": j - idx + 1}
            if highs[j] >= tp_px:
                ret = (tp_px / entry_px) - 1 - 2 * cost
                return {"side": "long", "entry": entry_px, "exit": tp_px, "ret": ret,
                        "reason": "take_profit", "entry_ts": entry_ts,
                        "exit_ts": panel_5m.index[j], "bars": j - idx + 1}
    else:  # short
        sl_px = entry_px * (1 + stop)
        tp_px = entry_px * (1 - tp)
        for j in range(idx, end):
            if highs[j] >= sl_px:
                ret = 1 - (sl_px / entry_px) - 2 * cost
                return {"side": "short", "entry": entry_px, "exit": sl_px, "ret": ret,
                        "reason": "stop_loss", "entry_ts": entry_ts,
                        "exit_ts": panel_5m.index[j], "bars": j - idx + 1}
            if lows[j] <= tp_px:
                ret = 1 - (tp_px / entry_px) - 2 * cost
                return {"side": "short", "entry": entry_px, "exit": tp_px, "ret": ret,
                        "reason": "take_profit", "entry_ts": entry_ts,
                        "exit_ts": panel_5m.index[j], "bars": j - idx + 1}
    return None  # never hit within max_hold


def aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0, "payoff": 0, "PF": 0, "exp": 0,
                "long_n": 0, "short_n": 0, "long_PF": 0, "short_PF": 0,
                "avg_bars": 0, "tp_pct": 0}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    tp_count = sum(1 for t in trades if t["reason"] == "take_profit")
    total_profit = wins.sum() if len(wins) else 0
    total_loss = -losses.sum() if len(losses) else 0
    PF = total_profit / total_loss if total_loss > 0 else float("inf")
    avg_w = wins.mean() if len(wins) else 0
    avg_l = losses.mean() if len(losses) else 0
    payoff = abs(avg_w / avg_l) if avg_l < 0 else float("inf")
    exp = rets.mean()
    win_rate = len(wins) / len(trades)
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    def side_pf(ts):
        if not ts: return 0
        r = np.array([t["ret"] for t in ts])
        p = r[r > 0].sum(); l = -r[r <= 0].sum()
        return p / l if l > 0 else float("inf")
    return {"trades": len(trades), "win_rate": win_rate, "payoff": payoff,
            "PF": PF, "exp": exp, "long_n": len(longs), "short_n": len(shorts),
            "long_PF": side_pf(longs), "short_PF": side_pf(shorts),
            "avg_bars": np.mean([t["bars"] for t in trades]),
            "tp_pct": tp_count / len(trades)}


def run_combo(symbol_data: dict, stop: float, tp: float, cost_bps: float) -> dict:
    all_trades = []
    for sym, (panel_1h, panel_5m) in symbol_data.items():
        fires = extract_fires(panel_1h)
        for entry_ts, side, entry_px in fires:
            t = execute_trade_5m(panel_5m, entry_ts, side, entry_px, stop, tp, cost_bps)
            if t: all_trades.append(t)
    return aggregate(all_trades)


def fmt_row(label, stop, tp, m):
    verdict = "PASS" if (m["PF"] > 1.0 and m["exp"] > 0) else "LOSER"
    return (f"  {label:<20} stop={stop*100:.3f}% tp={tp*100:.3f}%  "
            f"trades={m['trades']:>6}  win={m['win_rate']*100:5.2f}%  "
            f"payoff={m['payoff']:5.2f}  PF={m['PF']:6.3f}  exp={m['exp']*100:+8.5f}%  "
            f"tp_pct={m['tp_pct']*100:4.1f}%  avgB={m['avg_bars']:5.1f}  "
            f"L={m['long_n']:>4}/{m['long_PF']:5.2f}  S={m['short_n']:>4}/{m['short_PF']:5.2f}  {verdict}")


def main(argv=None):
    p = argparse.ArgumentParser(prog="bench_v11_5m_exit")
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--symbols", type=str, default="ALL")
    p.add_argument("--cost-bps", type=float, default=10.0)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    t0 = time.time()
    if args.symbols == "ALL":
        cache = _REPO / "data" / "cache" / "binance_1m"
        symbols = [f.stem for f in cache.glob("*.parquet") if not f.stem.startswith("_")]
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info(f"Loading {len(symbols)} symbols (1h + 5m), {args.months}mo...")
    data = _load_universe(args.months, symbols)
    if not data:
        logger.error("No data"); return 3

    print("\n" + "=" * 150)
    print(f"v1.1 BIDIR  signal=1h  exit=5m  |  {args.months}mo  |  symbols={len(data)}  |  cost={args.cost_bps:.0f}bp")
    print(f"  R/R scaled 1/10 for 10x leverage scalping")
    print("=" * 150)

    rows = []
    for stop, tp, label in SWEEP_RR:
        c0 = time.time()
        m = run_combo(data, stop, tp, args.cost_bps)
        rows.append((label, stop, tp, m))
        logger.info("  %s done in %.1fs (trades=%d PF=%.3f exp=%+.5f%%)",
                    label, time.time() - c0, m["trades"], m["PF"], m["exp"]*100)

    print()
    print(f"  {'label':<20} {'stop':<8} {'tp':<8}  {'trades':>10}  {'win':>6}  "
          f"{'payoff':>6}  {'PF':>6}  {'exp':>9}  {'tp%':>5}  {'avgB':>5}  "
          f"{'L_n/PF':<12} {'S_n/PF':<12} verdict")
    print("  " + "-" * 146)
    for label, stop, tp, m in sorted(rows, key=lambda r: r[3]["PF"], reverse=True):
        print(fmt_row(label, stop, tp, m))
    print("=" * 150)
    print(f"elapsed: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    sys.exit(main() or 0)
