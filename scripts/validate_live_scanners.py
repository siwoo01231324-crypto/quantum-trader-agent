"""DSR / PSR / PBO validation for the 4 live-scanner strategies on a universe.

Built on top of bench_live_scanner._replay_symbol (production exit logic) +
src/ml/validation/{deflated_sharpe, pbo}. Replays each strategy across the
loaded panels, aggregates per-trade returns into a daily PnL series, then:

  1. Per-strategy: annualised SR, skew, kurt_excess, MDD, PSR, DSR(N=trials).
  2. Cross-strategy: PBO via CSCV on the (T, N) daily-PnL matrix.

Project gates (12-validation-protocol §3.7, 99-AFML):
  - PSR >= 0.95 (single-trial significance)
  - DSR >= 0.95 (multi-trial correction)
  - PBO <= 0.20

Usage::

    python scripts/validate_live_scanners.py --universe krx --bar 1d
    python scripts/validate_live_scanners.py --universe binance --bar 1d
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats as sps

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_scanner")
from src.ml.validation.deflated_sharpe import (
    deflated_sharpe_ratio, probabilistic_sharpe_ratio,
)
from src.ml.validation.pbo import probability_of_backtest_overfitting

logger = logging.getLogger("validate_live_scanners")

STRATEGIES = [
    "live_bb_lower_bounce",
    "live_rsi_oversold_volume_spike",
    "live_oversold_with_divergence",
    "live_breakout_with_atr_stop",
]


def _daily_pnl_series(trades: list[dict],
                      full_calendar: pd.DatetimeIndex | None = None) -> pd.Series:
    """Per-trade returns → TRUE daily PnL series.

    If ``full_calendar`` given, the series is reindexed to it with 0 fill for
    days without trades. This is the correct denominator for SR / DSR — the
    trade-only-days variant (used by bench_live_scanner._aggregate) silently
    omits no-trade days, deflating variance and inflating SR ~2-5x.
    """
    if not trades:
        if full_calendar is None or len(full_calendar) == 0:
            return pd.Series(dtype=float)
        return pd.Series(0.0, index=full_calendar)
    by_day: dict[pd.Timestamp, list[float]] = {}
    for t in trades:
        day = pd.Timestamp(t["exit_ts"]).normalize()
        by_day.setdefault(day, []).append(float(t["ret"]))
    days = sorted(by_day.keys())
    vals = [max(-0.999, float(np.mean(by_day[d]))) for d in days]
    s = pd.Series(vals, index=pd.DatetimeIndex(days))
    if full_calendar is not None and len(full_calendar) > 0:
        # tz-strip both sides to avoid mismatch when panel is UTC and trade
        # exit_ts.normalize() loses tz info.
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        cal = full_calendar
        if cal.tz is not None:
            cal = cal.tz_localize(None)
        s = s.reindex(cal.normalize().unique(), fill_value=0.0)
    return s


def _mdd(equity: np.ndarray) -> float:
    if equity.size == 0:
        return 0.0
    cum_max = np.maximum.accumulate(equity)
    return float(np.min(equity / cum_max - 1.0))


async def _collect_trades(strategy_id: str, panels: dict[str, pd.DataFrame],
                          cost_bps: float) -> list[dict]:
    strat = bench._load_strategy(strategy_id)
    trades: list[dict] = []
    for sym, panel in panels.items():
        trades.extend(
            await bench._replay_symbol(strat, sym, panel, cost_bps=cost_bps)
        )
    return trades


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    if args.universe == "krx":
        logger.info("loading KRX universe (daily)...")
        panels = bench._load_krx_universe(args.period)
    else:
        logger.info("loading Binance universe (%s)...", args.bar)
        panels = bench._load_binance_universe(args.period, bar=args.bar)
    logger.info("loaded %d symbols (%.1fs)", len(panels), time.time() - t0)
    if not panels:
        return 2

    # Union of all panel timestamps → full calendar for true SR denominator.
    all_idx = pd.DatetimeIndex(sorted(set().union(
        *[set(p.index) for p in panels.values()]
    )))
    full_calendar = all_idx.normalize().unique()
    logger.info("full calendar: %d unique days", len(full_calendar))

    # 1) Replay each strategy → TRUE daily PnL series (0-pad no-trade days)
    series: dict[str, pd.Series] = {}
    per_strat_stats: dict[str, dict] = {}
    for sid in STRATEGIES:
        c0 = time.time()
        trades = await _collect_trades(sid, panels, args.cost_bps)
        s = _daily_pnl_series(trades, full_calendar=full_calendar)
        series[sid] = s
        if s.empty:
            per_strat_stats[sid] = {"n_days": 0, "trades": 0}
            logger.warning("%s: no trades", sid); continue
        # equity curve + MDD
        equity = np.cumprod(1 + s.to_numpy())
        mdd = _mdd(equity)
        # Annualised SR (daily-mean / daily-std × √252) — same scale eval uses
        mean_d = float(s.mean())
        std_d = float(s.std(ddof=1))
        sr_ann = (mean_d / std_d) * np.sqrt(252) if std_d > 0 else 0.0
        # higher moments
        sk = float(sps.skew(s)) if len(s) >= 3 else 0.0
        ku = float(sps.kurtosis(s, fisher=True)) if len(s) >= 4 else 0.0
        per_strat_stats[sid] = {
            "trades": len(trades), "n_days": len(s),
            "sr_ann": sr_ann, "skew": sk, "kurt_excess": ku,
            "mdd": mdd, "final_equity": float(equity[-1]) if equity.size else 1.0,
        }
        logger.info("  %s done %.0fs  trades=%d  n_days=%d  SR=%.3f  MDD=%.2f%%",
                    sid, time.time() - c0, len(trades), len(s), sr_ann, mdd * 100)

    # 2) PSR / DSR
    valid_sids = [s for s in STRATEGIES if per_strat_stats[s].get("n_days", 0) >= 30]
    sr_arr = np.array([per_strat_stats[s]["sr_ann"] for s in valid_sids])
    for sid in valid_sids:
        st = per_strat_stats[sid]
        psr = probabilistic_sharpe_ratio(
            st["sr_ann"], 0.0, st["n_days"], st["skew"], st["kurt_excess"],
        )
        dsr = deflated_sharpe_ratio(
            st["sr_ann"], sr_arr, st["n_days"], st["skew"], st["kurt_excess"],
            n_trials=len(valid_sids),
        )
        per_strat_stats[sid]["psr"] = psr
        per_strat_stats[sid]["dsr"] = dsr

    # 3) PBO via CSCV — align series on union of dates, fill missing with 0
    all_dates = sorted(set().union(*[set(series[s].index) for s in valid_sids]))
    if len(all_dates) >= 32:  # CSCV needs reasonable T
        idx = pd.DatetimeIndex(all_dates)
        ret_mat = np.zeros((len(idx), len(valid_sids)), dtype=float)
        for j, sid in enumerate(valid_sids):
            ret_mat[:, j] = series[sid].reindex(idx, fill_value=0.0).to_numpy()
        n_groups = 16 if len(idx) >= 64 else 8 if len(idx) >= 32 else 4
        pbo = probability_of_backtest_overfitting(ret_mat, n_groups=n_groups)
    else:
        pbo = None

    # 4) Output
    title = f"{args.universe.upper()} {args.bar} {args.period} (cost={args.cost_bps}bp)"
    print("\n" + "=" * 110)
    print(f"DSR/PSR/PBO 검정  |  {title}")
    print("=" * 110)
    print(f"{'strategy':<36}{'trades':>8}{'n_days':>8}{'SR_ann':>9}"
          f"{'skew':>7}{'kurt_e':>8}{'MDD':>8}{'PSR':>8}{'DSR':>8}  verdict")
    print("-" * 110)
    for sid in STRATEGIES:
        st = per_strat_stats[sid]
        if st.get("n_days", 0) < 30:
            print(f"  {sid:<34}{st.get('trades',0):>8}{st.get('n_days',0):>8}"
                  f"  -- insufficient --")
            continue
        psr_pass = "✓" if st["psr"] >= 0.95 else "✗"
        dsr_pass = "✓" if st["dsr"] >= 0.95 else "✗"
        v = "PASS" if (st["psr"] >= 0.95 and st["dsr"] >= 0.95) else "FAIL"
        print(f"  {sid:<34}{st['trades']:>8}{st['n_days']:>8}"
              f"{st['sr_ann']:>+8.3f} {st['skew']:>+6.2f}{st['kurt_excess']:>+7.2f}"
              f"{st['mdd']*100:>+7.1f}%{st['psr']:>7.3f}{psr_pass}"
              f"{st['dsr']:>7.3f}{dsr_pass}  {v}")
    print("-" * 110)
    if pbo is not None:
        pbo_v = "PASS" if pbo <= 0.20 else "FAIL"
        print(f"  PBO (4 trials, CSCV)  =  {pbo:.4f}  (gate <=0.20)  {pbo_v}")
    print("=" * 110)
    print(f"elapsed: {time.time()-t0:.1f}s")

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        # Dump daily series too — enables walk-forward + ensemble post-analysis
        # without rerunning the expensive trade replay.
        series_dump = {}
        for sid, s in series.items():
            if s.empty:
                series_dump[sid] = {"dates": [], "rets": []}
                continue
            idx = s.index
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            series_dump[sid] = {
                "dates": idx.strftime("%Y-%m-%d").tolist(),
                "rets": [float(x) for x in s.to_numpy()],
            }
        Path(args.output).write_text(json.dumps({
            "universe": args.universe, "bar": args.bar, "period": args.period,
            "cost_bps": args.cost_bps, "n_symbols": len(panels),
            "per_strategy": per_strat_stats, "pbo": pbo,
            "daily_series": series_dump,
        }, indent=2, default=float))
    return 0


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="validate_live_scanners")
    p.add_argument("--universe", choices=["krx", "binance"], required=True)
    p.add_argument("--bar", choices=["1d", "1m"], default="1d")
    p.add_argument("--period", default="5y")
    p.add_argument("--cost-bps", type=float, default=None,
                   help="default 55 for KRX, 10 for Binance")
    p.add_argument("--output", default=None)
    args = p.parse_args(argv)
    if args.cost_bps is None:
        args.cost_bps = 55.0 if args.universe == "krx" else 10.0
    return args


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
