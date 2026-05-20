"""Quick bench for ``live_mg_bb_reversal`` — freq + R/R sweep on a short window.

Not a 5y eval. Designed as a fast smoke / sanity check on a small recent window
so we can see whether the strategy actually fires trades, and at what (freq,
stop, take_profit) combinations the edge metrics (PF, expectancy) survive.

Reuses the production replay seam from ``bench_live_scanner._replay_symbol`` so
exit timing matches what ``LivePositionRiskManager`` enforces live.

Usage::

    # single (freq, stop, tp) run
    python scripts/bench_live_mg_quick.py --freq 1h --stop 0.01 --tp 0.02

    # R/R sweep over a single freq
    python scripts/bench_live_mg_quick.py --freq 15m --sweep-rr

    # change window / symbols / cost
    python scripts/bench_live_mg_quick.py --freq 1m --months 6 \\
        --symbols BTCUSDT,ETHUSDT,SOLUSDT --cost-bps 10
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import sys
import time
from pathlib import Path

import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_scanner")
logger = logging.getLogger("bench_live_mg_quick")

# Pre-defined R/R combos for the sweep — mix of 1:2 (lecture-style) and 1:3.
SWEEP_RR = [
    # (stop, tp, label)
    (0.005, 0.010, "0.5/1.0 (1:2)"),
    (0.010, 0.020, "1.0/2.0 (1:2)"),  # lecture default
    (0.015, 0.030, "1.5/3.0 (1:2)"),
    (0.020, 0.040, "2.0/4.0 (1:2)"),
    (0.030, 0.060, "3.0/6.0 (1:2)"),  # sibling live_bb_lower_bounce default
    (0.010, 0.030, "1.0/3.0 (1:3)"),
    (0.020, 0.060, "2.0/6.0 (1:3)"),
    (0.005, 0.020, "0.5/2.0 (1:4)"),
]

# Resampling rules — pandas accepts "1min", "15min", "1h", "4h", etc.
FREQ_RULE = {"1m": "1min", "15m": "15min", "1h": "1h", "4h": "4h"}


def _resample(df_1m: pd.DataFrame, freq: str) -> pd.DataFrame:
    if freq == "1m":
        # No resample needed — return as-is (caller may still slice).
        return df_1m
    rule = FREQ_RULE[freq]
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (
        df_1m[cols]
        .resample(rule, label="right", closed="right")
        .agg({k: agg[k] for k in cols})
        .dropna(subset=["close"])
    )


def _edge(metrics: dict) -> dict:
    """Same PF/expectancy derivation as eval_live_scanners_5y."""
    n = int(metrics.get("trades", 0))
    w = float(metrics.get("win_rate", 0.0))
    P = float(metrics.get("realized_pnl_profit", 0.0))
    L = float(metrics.get("realized_pnl_loss", 0.0))
    nw = round(n * w)
    nl = n - nw
    avg_w = (P / nw) if nw else 0.0
    avg_l = (L / nl) if nl else 0.0
    pf = (P / abs(L)) if L else float("inf")
    payoff = (avg_w / abs(avg_l)) if avg_l else float("inf")
    exp = ((P + L) / n) if n else 0.0
    return {
        "trades": n, "win_rate": w, "avg_win": avg_w, "avg_loss": avg_l,
        "payoff": payoff, "profit_factor": pf, "expectancy": exp,
    }


def _load_panels(symbols: list[str], months: int, freq: str
                 ) -> dict[str, pd.DataFrame]:
    logger.info("loading 1m cache for %s ...", symbols)
    all_panels = bench._load_binance_universe("5y", bar="1m")
    if not all_panels:
        return {}
    selected: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        if sym not in all_panels:
            logger.warning("symbol %s not in cache, skipping", sym)
            continue
        p1m = all_panels[sym]
        if p1m.index.tz is None:
            p1m = p1m.tz_localize("UTC")
        last_ts = p1m.index.max()
        first_ts = last_ts - pd.DateOffset(months=months)
        p1m = p1m.loc[first_ts:last_ts]
        panel = _resample(p1m, freq)
        if len(panel) < 60:
            logger.warning("%s: only %d %s bars — skip", sym, len(panel), freq)
            continue
        selected[sym] = panel
        logger.info("  %s @ %s: 1m=%d → %d bars  [%s .. %s]",
                    sym, freq, len(p1m), len(panel),
                    panel.index[0].date(), panel.index[-1].date())
    return selected


async def _run_combo(strat, panels: dict[str, pd.DataFrame],
                     stop: float, tp: float, cost_bps: float) -> dict:
    strat.stop_loss_pct = stop  # type: ignore[misc]
    strat.take_profit_pct = tp  # type: ignore[misc]
    strat.trailing_stop_pct = None  # type: ignore[misc]
    all_trades: list[dict] = []
    for sym, panel in panels.items():
        all_trades.extend(
            await bench._replay_symbol(strat, sym, panel, cost_bps=cost_bps)
        )
    return _edge(bench._aggregate(all_trades))


def _fmt_row(label: str, stop: float, tp: float, e: dict) -> str:
    verdict = "PASS" if (e["profit_factor"] > 1.0 and e["expectancy"] > 0) else "LOSER"
    return (
        f"  {label:<18} stop={stop*100:.2f}% tp={tp*100:.2f}%  "
        f"trades={e['trades']:>4}  "
        f"win={e['win_rate']*100:5.2f}%  "
        f"payoff={e['payoff']:5.2f}  "
        f"PF={e['profit_factor']:5.3f}  "
        f"exp={e['expectancy']*100:+7.4f}%  "
        f"{verdict}"
    )


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    from backtest.strategies.live_mg_bb_reversal import LiveMgBbReversal

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.freq not in FREQ_RULE:
        logger.error("unknown freq %s (choose: %s)", args.freq, list(FREQ_RULE))
        return 2

    panels = _load_panels(symbols, args.months, args.freq)
    if not panels:
        logger.error("no usable panels — abort.")
        return 3

    strat = LiveMgBbReversal()
    combos = SWEEP_RR if args.sweep_rr else [
        (args.stop, args.tp, f"{args.stop*100:.1f}/{args.tp*100:.1f}"),
    ]

    print("\n" + "=" * 110)
    print(
        f"live_mg_bb_reversal  |  freq={args.freq}  |  {args.months}mo  |  "
        f"symbols={','.join(panels.keys())}  |  cost={args.cost_bps:.0f}bp"
    )
    print("=" * 110)

    rows: list[tuple[str, float, float, dict]] = []
    for stop, tp, label in combos:
        c0 = time.time()
        e = await _run_combo(strat, panels, stop, tp, args.cost_bps)
        rows.append((label, stop, tp, e))
        logger.info("  combo %s done in %.1fs (trades=%d PF=%.3f exp=%+.4f%%)",
                    label, time.time() - c0, e["trades"],
                    e["profit_factor"], e["expectancy"] * 100)

    print()
    print(_fmt_row("label (R/R)", 0, 0, {
        "trades": 0, "win_rate": 0, "payoff": 0, "profit_factor": 0, "expectancy": 0,
    }).replace("trades=   0", "trades=    ")
        .replace("win= 0.00%", "win=     ")
        .replace("payoff= 0.00", "payoff=     ")
        .replace("PF=0.000", "PF=     ")
        .replace("exp=+0.0000%", "exp=          ")
        .replace("LOSER", "       "))
    print("  " + "-" * 106)
    rows_sorted = sorted(rows, key=lambda r: r[3]["profit_factor"], reverse=True)
    for label, stop, tp, e in rows_sorted:
        print(_fmt_row(label, stop, tp, e))
    print("=" * 110)
    print(f"elapsed: {time.time()-t0:.1f}s")
    return 0


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bench_live_mg_quick")
    p.add_argument("--freq", type=str, default="1h",
                   choices=list(FREQ_RULE.keys()),
                   help="bar frequency (default 1h)")
    p.add_argument("--months", type=int, default=12,
                   help="recent window in months (default 12)")
    p.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT",
                   help="comma-separated symbol list (default BTC,ETH)")
    p.add_argument("--stop", type=float, default=0.01,
                   help="stop_loss_pct (ignored if --sweep-rr)")
    p.add_argument("--tp", type=float, default=0.02,
                   help="take_profit_pct (ignored if --sweep-rr)")
    p.add_argument("--sweep-rr", action="store_true",
                   help="run all pre-defined R/R combos")
    p.add_argument("--cost-bps", type=float, default=10.0,
                   help="round-trip cost in bps (default 10)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
