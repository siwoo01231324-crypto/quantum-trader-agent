"""Quick bench for ``live_airborne_bb_reversal_v3`` — 1y BTC+ETH 1h sweep.

Same harness as v2; uses v3 strategy (trend + volume gates on top of v1 core).
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

import pandas as pd

Freq = Literal["1m", "15m", "1h", "4h"]
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
bench = importlib.import_module("bench_live_scanner")
logger = logging.getLogger("bench_live_airborne_v3_quick")

SWEEP_RR = [
    (0.005, 0.010, "0.5/1.0 (1:2)"), (0.010, 0.020, "1.0/2.0 (1:2)"),
    (0.015, 0.030, "1.5/3.0 (1:2)"), (0.020, 0.040, "2.0/4.0 (1:2)"),
    (0.030, 0.060, "3.0/6.0 (1:2)"), (0.010, 0.030, "1.0/3.0 (1:3)"),
    (0.020, 0.060, "2.0/6.0 (1:3)"), (0.005, 0.020, "0.5/2.0 (1:4)"),
]
FREQ_RULE: dict[Freq, str] = {"1m": "1min", "15m": "15min", "1h": "1h", "4h": "4h"}


def _resample(df_1m, freq):
    if freq == "1m": return df_1m
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (df_1m[cols].resample(FREQ_RULE[freq], label="right", closed="right")
            .agg({k: agg[k] for k in cols}).dropna(subset=["close"]))


def _edge(metrics):
    n = int(metrics.get("trades", 0)); w = float(metrics.get("win_rate", 0.0))
    P = float(metrics.get("realized_pnl_profit", 0.0))
    L = float(metrics.get("realized_pnl_loss", 0.0))
    nw = round(n * w); nl = n - nw
    avg_w = (P / nw) if nw else 0.0; avg_l = (L / nl) if nl else 0.0
    pf = (P / abs(L)) if L else float("inf")
    payoff = (avg_w / abs(avg_l)) if avg_l else float("inf")
    exp = ((P + L) / n) if n else 0.0
    return {"trades": n, "win_rate": w, "avg_win": avg_w, "avg_loss": avg_l,
            "payoff": payoff, "profit_factor": pf, "expectancy": exp}


def _load_panels(symbols, months, freq):
    cache_dir = _REPO_ROOT / "data" / "cache" / "binance_1m"
    selected = {}
    for sym in symbols:
        path = cache_dir / f"{sym}.parquet"
        if not path.exists(): continue
        p1m = pd.read_parquet(path)
        if p1m.index.tz is None: p1m = p1m.tz_localize("UTC")
        first_ts = p1m.index.max() - pd.DateOffset(months=months)
        p1m = p1m.loc[first_ts:]
        panel = _resample(p1m, freq)
        if len(panel) < 60: continue
        selected[sym] = panel
        logger.info("  %s @ %s: %d bars [%s..%s]", sym, freq, len(panel),
                    panel.index[0].date(), panel.index[-1].date())
        del p1m
    gc.collect()
    return selected


async def _run_combo(panels, stop, tp, cost_bps, trend_sma, vol_window, vol_min):
    from backtest.strategies.live_airborne_bb_reversal_v3 import LiveAirborneBbReversalV3
    strat = LiveAirborneBbReversalV3(
        stop_loss_pct=stop, take_profit_pct=tp, trailing_stop_pct=None,
        trend_sma_period=trend_sma, volume_window=vol_window, volume_ratio_min=vol_min,
    )
    all_trades = []
    for sym, panel in panels.items():
        all_trades.extend(await bench._replay_symbol(strat, sym, panel, cost_bps=cost_bps))
    return _edge(bench._aggregate(all_trades))


def _fmt_row(label, stop, tp, e):
    verdict = "PASS" if (e["profit_factor"] > 1.0 and e["expectancy"] > 0) else "LOSER"
    return (f"  {label:<18} stop={stop*100:.2f}% tp={tp*100:.2f}%  "
            f"trades={e['trades']:>4}  win={e['win_rate']*100:5.2f}%  "
            f"payoff={e['payoff']:5.2f}  PF={e['profit_factor']:5.3f}  "
            f"exp={e['expectancy']*100:+7.4f}%  {verdict}")


async def _main_async(args):
    t0 = time.time()
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    panels = _load_panels(symbols, args.months, args.freq)
    if not panels: return 3
    combos = SWEEP_RR if args.sweep_rr else [
        (args.stop, args.tp, f"{args.stop*100:.1f}/{args.tp*100:.1f}")]
    print("\n" + "=" * 110)
    print(f"live_airborne_bb_reversal_v3  |  freq={args.freq}  |  {args.months}mo  |  "
          f"symbols={','.join(panels.keys())}  |  cost={args.cost_bps:.0f}bp  |  "
          f"trend_sma={args.trend_sma}  |  vol_w={args.vol_window}  |  vol_min={args.vol_min}")
    print("=" * 110)
    rows = []
    for stop, tp, label in combos:
        c0 = time.time()
        e = await _run_combo(panels, stop, tp, args.cost_bps,
                             args.trend_sma, args.vol_window, args.vol_min)
        rows.append((label, stop, tp, e))
        logger.info("  combo %s done in %.1fs (trades=%d PF=%.3f exp=%+.4f%%)",
                    label, time.time() - c0, e["trades"],
                    e["profit_factor"], e["expectancy"] * 100)
    print()
    print(f"  {'label (R/R)':<18} {'stop':<7} {'tp':<7}  {'trades':>10}  {'win':>6}  "
          f"{'payoff':>6}  {'PF':>5}  {'exp':>8}  verdict")
    print("  " + "-" * 106)
    for label, stop, tp, e in sorted(rows, key=lambda r: r[3]["profit_factor"], reverse=True):
        print(_fmt_row(label, stop, tp, e))
    print("=" * 110)
    print(f"elapsed: {time.time()-t0:.1f}s")
    return 0


def _parse(argv=None):
    p = argparse.ArgumentParser(prog="bench_live_airborne_v3_quick")
    p.add_argument("--freq", type=str, default="1h", choices=list(FREQ_RULE.keys()))
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT")
    p.add_argument("--stop", type=float, default=0.02)
    p.add_argument("--tp", type=float, default=0.04)
    p.add_argument("--sweep-rr", action="store_true")
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--trend-sma", type=int, default=50)
    p.add_argument("--vol-window", type=int, default=20)
    p.add_argument("--vol-min", type=float, default=1.0)
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return asyncio.run(_main_async(_parse(argv)))


if __name__ == "__main__":
    sys.exit(main())
