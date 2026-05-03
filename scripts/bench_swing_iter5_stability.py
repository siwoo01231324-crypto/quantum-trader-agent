#!/usr/bin/env python3
"""Pre-registered swing strategy bench: iter 5 (W1 parameter stability grid).

Issue #99 iter 5. Builds on iter 4 (bench_swing_iter4.py).

Hypothesis:
    - W1 (S2c Donchian + vol-target) is the only variant passing DSR/MDD/mhr
      single gates (iter 4). But is the result robust to parameter choice, or
      was it over-tuned to (entry=20, exit=10, vol_target=0.15, vol_lookback=60)?
    - If the Sharpe/MDD/mhr distributions across a sensible parameter grid are
      narrow (std/mean < 0.3), the result is robust. Otherwise, over-tuned.

Parameter grid (small, sense-checked):
    entry_lookback: [10, 20, 30]
    exit_lookback:  [5, 10, 20]
    vol_target:     [0.10, 0.15, 0.20]
    vol_lookback:   [10, 20, 30]
    Total: 3 x 3 x 3 x 3 = 81 combinations

Usage:
    python scripts/bench_swing_iter5_stability.py --smoke
    python scripts/bench_swing_iter5_stability.py \
        --data-dir lake \
        --output-dir docs/work/active/swing-strategy-best-return \
        --start 2020-01-01 --end 2025-12-31

Output: ``<output-dir>/bench_output_iter5_grid.json``
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import itertools
import json
import logging
import math
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from src.backtest.swing.strategies import s2_donchian_voltarget  # noqa: E402

logger = logging.getLogger("bench_swing_iter5_stability")

# -- Parameter grid (iter 5, FROZEN) ------------------------------------------

PARAM_GRID = {
    "entry_lookback": [10, 20, 30],
    "exit_lookback": [5, 10, 20],
    "vol_target": [0.10, 0.15, 0.20],
    "vol_lookback": [10, 20, 30],
}

# Cost assumption (Binance taker fee, round-trip)
TAKER_FEE_ROUND_TRIP = 0.0008

# Gate thresholds (for per-combo pass/fail annotation)
GATE_MDD_MAX = 0.25
GATE_MONTHLY_HIT_RATE_MIN = 0.50


# -- Result container ----------------------------------------------------------


@dataclass
class ComboResult:
    params: dict[str, Any]
    n_trades: int = 0
    sharpe: float | None = None
    sortino: float | None = None
    mdd: float | None = None
    calmar: float | None = None
    monthly_hit_rate: float | None = None
    skew: float | None = None
    kurtosis_excess: float | None = None
    status: str = "ok"


# -- Data loading (reused from iter 4) ----------------------------------------


def load_ohlcv(
    data_dir: Path | None,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
    """Load 1-minute OHLCV from Hive-partitioned parquet lake."""
    if data_dir is None or not data_dir.exists():
        return None

    pattern_hive = data_dir / "ohlcv" / "freq=1m"
    pattern_single = data_dir / f"{symbol}.parquet"

    df: pd.DataFrame | None = None
    if pattern_hive.exists():
        years = range(int(start.year), int(end.year) + 1)
        files: list[Path] = []
        for y in years:
            year_dir = pattern_hive / f"year={y}"
            if not year_dir.exists():
                continue
            for sym_dir in year_dir.glob(f"month=*/symbol={symbol}"):
                files.extend(sym_dir.glob("*.parquet"))
        if files:
            df = pd.concat([pd.read_parquet(f) for f in sorted(files)], axis=0)
    elif pattern_single.exists():
        df = pd.read_parquet(pattern_single)

    if df is None or df.empty:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        for col in ("ts", "timestamp", "open_time"):
            if col in df.columns:
                df = df.set_index(pd.DatetimeIndex(df[col])).drop(columns=[col])
                break
    df = df.sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[~df.index.duplicated(keep="last")]
    df = df.loc[start:end]
    needed = {"close", "volume"}
    missing = needed - set(df.columns)
    if missing:
        logger.warning("OHLCV missing columns %s, skipping load", missing)
        return None
    return df


def resample_ohlcv(frame: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample 1m OHLCV to a coarser frequency with causal alignment."""
    if freq.lower() in ("1min", "1m", "1mn"):
        return frame
    agg: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    avail = {k: v for k, v in agg.items() if k in frame.columns}
    return (
        frame.resample(freq, label="right", closed="right")
        .agg(avail)
        .dropna(subset=["close"])
    )


def synthetic_ohlcv(
    start: pd.Timestamp,
    n_bars: int = 90 * 24 * 60,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate 1-minute synthetic OHLCV for smoke tests."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq="1min", tz="UTC")
    drift = 0.00002
    sigma = 0.001
    log_returns = rng.normal(loc=drift, scale=sigma, size=n_bars)
    close = 30000.0 * np.exp(log_returns.cumsum())
    volume = rng.lognormal(mean=2.0, sigma=0.5, size=n_bars)
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * (1 + np.abs(rng.normal(scale=0.0005, size=n_bars))),
            "low": close * (1 - np.abs(rng.normal(scale=0.0005, size=n_bars))),
            "close": close,
            "volume": volume,
        },
        index=idx,
    )
    return df


# -- Backtest mechanics --------------------------------------------------------


def backtest_signal(
    df: pd.DataFrame,
    signal: pd.Series,
    position_size: pd.Series | None = None,
    fee_round_trip: float = TAKER_FEE_ROUND_TRIP,
) -> tuple[pd.Series, int, float]:
    """Naive backtest: hold during signal=1 (or signal * position_size).

    Returns (per_bar_returns, n_trades, turnover).
    """
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)

    # One-bar entry lag to avoid lookahead on the signal bar
    position = signal.reindex(close.index, fill_value=0).shift(1).fillna(0)

    if position_size is not None:
        ps = position_size.reindex(close.index, fill_value=0).shift(1).fillna(0)
        effective_pos = position * ps
    else:
        effective_pos = position.astype(float)

    pnl = effective_pos * bar_ret

    # Charge round-trip fee when position changes
    pos_change = effective_pos.diff().abs().fillna(0)
    fee = pos_change * fee_round_trip
    pnl -= fee

    n_trades = int(position.diff().abs().fillna(0).sum())
    turnover = float(pos_change.sum()) / max(len(position), 1)
    return pnl, n_trades, turnover


def compute_metrics(per_bar_returns: pd.Series) -> dict[str, float | None]:
    """Compute Sharpe/Sortino/MDD/Calmar/skew/kurt/monthly_hit_rate."""
    if per_bar_returns.empty or per_bar_returns.abs().sum() == 0:
        return {
            "sharpe": None, "sortino": None, "mdd": None, "calmar": None,
            "monthly_hit_rate": None, "skew": None, "kurtosis_excess": None,
        }

    daily = per_bar_returns.resample("1D").sum()
    daily = daily[daily != 0.0] if daily.abs().sum() == 0 else daily
    if len(daily) < 2:
        return {
            "sharpe": None, "sortino": None, "mdd": None, "calmar": None,
            "monthly_hit_rate": None, "skew": None, "kurtosis_excess": None,
        }

    mean = float(daily.mean())
    std = float(daily.std(ddof=1))
    sharpe = float((mean / std) * math.sqrt(365)) if std != 0 else None

    downside = daily[daily < 0]
    if len(downside) > 1 and downside.std(ddof=1) > 0:
        sortino = float((mean / downside.std(ddof=1)) * math.sqrt(365))
    else:
        sortino = None

    equity = (1.0 + daily).cumprod()
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    mdd = float(drawdown.min())

    annual_return = (1.0 + mean) ** 365 - 1.0
    calmar = float(annual_return / abs(mdd)) if mdd != 0 else None

    monthly = daily.resample("ME").sum()
    monthly_count = daily.resample("ME").count()
    monthly = monthly[monthly_count >= 5]
    monthly_hit_rate = float((monthly > 0).mean()) if len(monthly) > 0 else None

    skew_val = float(daily.skew())
    kurtosis_excess = float(daily.kurt())

    return {
        "sharpe": sharpe, "sortino": sortino, "mdd": mdd, "calmar": calmar,
        "monthly_hit_rate": monthly_hit_rate, "skew": skew_val,
        "kurtosis_excess": kurtosis_excess,
    }


# -- Grid runner ---------------------------------------------------------------


def generate_grid() -> list[dict[str, Any]]:
    """Generate all parameter combinations from PARAM_GRID."""
    keys = sorted(PARAM_GRID.keys())
    values = [PARAM_GRID[k] for k in keys]
    combos = []
    for vals in itertools.product(*values):
        combos.append(dict(zip(keys, vals)))
    return combos


def run_combo(
    params: dict[str, Any],
    df: pd.DataFrame,
) -> ComboResult:
    """Execute a single parameter combination on full data."""
    res = ComboResult(params=params)

    signal, pos_size = s2_donchian_voltarget(df, **params)

    if signal.abs().sum() == 0:
        res.status = "no_signal"
        return res

    pnl, n_trades, _turnover = backtest_signal(df, signal, pos_size)
    metrics = compute_metrics(pnl)

    res.n_trades = n_trades
    res.sharpe = metrics["sharpe"]
    res.sortino = metrics["sortino"]
    res.mdd = metrics["mdd"]
    res.calmar = metrics["calmar"]
    res.monthly_hit_rate = metrics["monthly_hit_rate"]
    res.skew = metrics["skew"]
    res.kurtosis_excess = metrics["kurtosis_excess"]
    return res


def compute_distribution_stats(
    values: list[float],
) -> dict[str, float | None]:
    """Compute distribution statistics for a list of metric values."""
    if not values:
        return {
            "mean": None, "std": None, "min": None, "max": None,
            "q25": None, "median": None, "q75": None,
            "iqr": None, "pct_within_1_5_iqr": None,
            "cv": None,
        }
    arr = np.array(values, dtype=float)
    mean = float(np.mean(arr))
    std = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    q25, median, q75 = float(np.percentile(arr, 25)), float(np.median(arr)), float(np.percentile(arr, 75))
    iqr = q75 - q25
    lower = q25 - 1.5 * iqr
    upper = q75 + 1.5 * iqr
    within = float(np.mean((arr >= lower) & (arr <= upper)))
    cv = std / abs(mean) if mean != 0 else None

    return {
        "mean": mean,
        "std": std,
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
        "q25": q25,
        "median": median,
        "q75": q75,
        "iqr": iqr,
        "pct_within_1_5_iqr": within,
        "cv": cv,
    }


# -- Output schema -------------------------------------------------------------


def param_grid_sha256() -> str:
    payload = json.dumps(PARAM_GRID, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def git_commit_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        return out
    except Exception:
        return "unknown"


def combo_to_dict(r: ComboResult) -> dict[str, Any]:
    return {
        "params": r.params,
        "status": r.status,
        "n_trades": r.n_trades,
        "sharpe": r.sharpe,
        "sortino": r.sortino,
        "mdd": r.mdd,
        "calmar": r.calmar,
        "monthly_hit_rate": r.monthly_hit_rate,
        "skew": r.skew,
        "kurtosis_excess": r.kurtosis_excess,
    }


# -- Main ----------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--data-dir", type=Path, default=None)
    p.add_argument(
        "--output-dir", type=Path,
        default=REPO_ROOT / "docs/work/active/swing-strategy-best-return",
    )
    p.add_argument("--symbol", type=str, default="BTCUSDT")
    p.add_argument("--timeframe", type=str, default="4h")
    p.add_argument("--start", type=str, default="2020-01-01")
    p.add_argument("--end", type=str, default="2025-12-31")
    p.add_argument("--smoke", action="store_true",
                    help="Run on 90 days of synthetic data (no real data needed).")
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")

    if args.smoke:
        smoke_bars = 90 * 24 * 60
        df = synthetic_ohlcv(start=start, n_bars=smoke_bars)
        df = resample_ohlcv(df, args.timeframe)
        data_source = "synthetic-smoke"
    else:
        df = load_ohlcv(args.data_dir, args.symbol, start, end)
        if df is None or df.empty:
            logger.error(
                "OHLCV unavailable from %s for %s. Re-run with --smoke or "
                "fetch via scripts/fetch_futures_candles.py.",
                args.data_dir, args.symbol,
            )
            return 2
        df = resample_ohlcv(df, args.timeframe)
        data_source = f"{args.data_dir}::{args.symbol}@{args.timeframe}"

    if len(df) < 200:
        logger.error("OHLCV too short (%d bars). Need >= 200.", len(df))
        return 2

    # Generate grid
    grid = generate_grid()
    logger.info("Parameter grid: %d combinations", len(grid))

    # Run all combos
    results: list[ComboResult] = []
    for i, params in enumerate(grid):
        logger.info(
            "[%d/%d] entry=%d exit=%d vol_target=%.2f vol_lb=%d",
            i + 1, len(grid),
            params["entry_lookback"], params["exit_lookback"],
            params["vol_target"], params["vol_lookback"],
        )
        results.append(run_combo(params, df))

    # Filter OK results with valid metrics
    ok_results = [r for r in results if r.status == "ok" and r.sharpe is not None]
    logger.info("%d / %d combos produced valid metrics", len(ok_results), len(results))

    # Compute distribution stats
    sharpe_vals = [r.sharpe for r in ok_results if r.sharpe is not None]
    mdd_vals = [r.mdd for r in ok_results if r.mdd is not None]
    mhr_vals = [r.monthly_hit_rate for r in ok_results if r.monthly_hit_rate is not None]

    sharpe_stats = compute_distribution_stats(sharpe_vals)
    mdd_stats = compute_distribution_stats(mdd_vals)
    mhr_stats = compute_distribution_stats(mhr_vals)

    # Robustness assessment
    sharpe_cv = sharpe_stats["cv"]
    if sharpe_cv is not None and sharpe_cv < 0.3:
        robustness = "ROBUST"
        robustness_reason = f"Sharpe CV={sharpe_cv:.3f} < 0.3 threshold"
    elif sharpe_cv is not None and sharpe_cv < 0.5:
        robustness = "MODERATE"
        robustness_reason = f"Sharpe CV={sharpe_cv:.3f} between 0.3-0.5"
    else:
        robustness = "OVER-TUNED"
        robustness_reason = f"Sharpe CV={sharpe_cv} >= 0.5 or insufficient data"

    # Count combos passing individual MDD + mhr gates
    gate_pass_count = sum(
        1 for r in ok_results
        if r.mdd is not None and r.mdd > -GATE_MDD_MAX
        and r.monthly_hit_rate is not None and r.monthly_hit_rate >= GATE_MONTHLY_HIT_RATE_MIN
    )

    # Find best combo
    best = max(ok_results, key=lambda r: r.sharpe or -999) if ok_results else None

    output = {
        "schema_version": "swing-strategy-99/v5-grid",
        "issue": 99,
        "iteration": 5,
        "test_type": "parameter_stability_grid",
        "strategy": "W1 (S2c Donchian + vol-target)",
        "data_source": data_source,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "start": str(start),
        "end": str(end),
        "n_bars": int(len(df)),
        "git_commit": git_commit_hash(),
        "param_grid": PARAM_GRID,
        "param_grid_sha256": param_grid_sha256(),
        "n_combinations": len(grid),
        "n_valid": len(ok_results),
        "fee_round_trip": TAKER_FEE_ROUND_TRIP,
        "distribution": {
            "sharpe": sharpe_stats,
            "mdd": mdd_stats,
            "monthly_hit_rate": mhr_stats,
        },
        "robustness": {
            "verdict": robustness,
            "reason": robustness_reason,
            "sharpe_cv": sharpe_cv,
            "gate_pass_count": gate_pass_count,
            "gate_pass_pct": gate_pass_count / len(ok_results) if ok_results else 0,
        },
        "best_combo": combo_to_dict(best) if best else None,
        "all_combos": [combo_to_dict(r) for r in results],
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "bench_output_iter5_grid.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", out_path)

    # Human-readable summary
    print()
    print("=" * 78)
    print(f"Swing Strategy #99 iter5 -- W1 Parameter Stability Grid")
    print(f"  data: {data_source}  n_bars={len(df)}  symbol={args.symbol}")
    print(f"  grid: {len(grid)} combinations  valid: {len(ok_results)}")
    print(f"  param_grid sha256: {param_grid_sha256()[:16]}...")
    print()
    print("Sharpe distribution:")
    print(f"  mean={sharpe_stats['mean']:.3f}  std={sharpe_stats['std']:.3f}  "
          f"CV={sharpe_cv:.3f}" if sharpe_cv else "  insufficient data")
    print(f"  min={sharpe_stats['min']:.3f}  Q25={sharpe_stats['q25']:.3f}  "
          f"median={sharpe_stats['median']:.3f}  Q75={sharpe_stats['q75']:.3f}  "
          f"max={sharpe_stats['max']:.3f}")
    print(f"  within 1.5*IQR: {sharpe_stats['pct_within_1_5_iqr']:.1%}")
    print()
    print("MDD distribution:")
    print(f"  mean={mdd_stats['mean']:.4f}  std={mdd_stats['std']:.4f}")
    print(f"  min={mdd_stats['min']:.4f}  median={mdd_stats['median']:.4f}  "
          f"max={mdd_stats['max']:.4f}")
    print()
    print("Monthly hit rate distribution:")
    print(f"  mean={mhr_stats['mean']:.3f}  std={mhr_stats['std']:.3f}")
    print(f"  min={mhr_stats['min']:.3f}  median={mhr_stats['median']:.3f}  "
          f"max={mhr_stats['max']:.3f}")
    print()
    print(f"Gate pass (MDD + mhr): {gate_pass_count}/{len(ok_results)} "
          f"({gate_pass_count/len(ok_results):.0%})" if ok_results else "N/A")
    print()
    print(f"ROBUSTNESS VERDICT: {robustness}")
    print(f"  {robustness_reason}")
    if best:
        print()
        print(f"Best combo: {best.params}")
        print(f"  Sharpe={best.sharpe:.3f}  MDD={best.mdd:.4f}  "
              f"mhr={best.monthly_hit_rate:.3f}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
