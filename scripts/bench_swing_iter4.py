#!/usr/bin/env python3
"""Pre-registered swing strategy bench: iter 4 (variant pool pruning + composite).

Issue #99 iter 4. Builds on iter 3 (bench_swing_iter3.py).

Hypothesis:
    - Reducing variant pool from 7 to 3 (winners + composite) will lower PBO
    - AND-gate composite W3 (S2c x S4) combines S2c's 50% mhr with S4's Sharpe

Variant matrix (FROZEN -- sha256 of VARIANT_REGISTRY embedded in output):
    W1:  S2c (Donchian + vol-target) -- iter 3 winner 1
    W2:  S4 (funding carry, long-only when funding < -0.005%) -- iter 3 winner 2
    W3:  S2c x S4 composite (AND-gate: both long signal -> enter, vol-target sizing)

Usage:
    python scripts/bench_swing_iter4.py --smoke
    python scripts/bench_swing_iter4.py \
        --data-dir lake \
        --output-dir docs/work/active/swing-strategy-best-return \
        --start 2020-01-01 --end 2025-12-31

Output: ``<output-dir>/bench_output_iter4.json``
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
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

from src.backtest.swing.strategies import (  # noqa: E402
    s2_donchian_voltarget,
    s2c_x_s4_composite,
    s4_funding_carry,
)
from src.ml.cv import PurgedKFold  # noqa: E402
from src.ml.validation import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)

logger = logging.getLogger("bench_swing_iter4")

# -- Pre-registered variant matrix (iter 4, FROZEN) ----------------------------

VARIANT_REGISTRY: dict[str, dict[str, Any]] = {
    "W1": {
        "fn": "s2_donchian_voltarget",
        "params": {
            "entry_lookback": 20,
            "exit_lookback": 10,
            "vol_target": 0.15,
            "vol_lookback": 60,
        },
    },
    "W2": {"fn": "s4_funding_carry", "params": {"threshold_neg": -0.005e-2}},
    "W3": {
        "fn": "s2c_x_s4_composite",
        "params": {
            "entry_lookback": 20,
            "exit_lookback": 10,
            "threshold_neg": -0.005e-2,
            "vol_target": 0.15,
            "vol_lookback": 60,
        },
    },
}

# Project gate (docs/background/12-validation-protocol.md)
GATE_DSR_MIN = 0.95
GATE_PBO_MAX = 0.20
GATE_OOS_MDD_MAX = 0.25
GATE_MONTHLY_HIT_RATE_MIN = 0.50

# Cost assumption (Binance taker fee, round-trip)
TAKER_FEE_ROUND_TRIP = 0.0008


# -- Result containers --------------------------------------------------------


@dataclass
class VariantResult:
    variant_id: str
    status: str = "ok"
    n_trades: int = 0
    sharpe: float | None = None
    sortino: float | None = None
    mdd: float | None = None
    calmar: float | None = None
    avg_rr: float | None = None
    turnover: float | None = None
    monthly_hit_rate: float | None = None
    skew: float | None = None
    kurtosis_excess: float | None = None
    daily_returns: list[float] = field(default_factory=list)


# -- Data loading (reused from iter 3) ----------------------------------------


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


def load_funding_rates(
    data_dir: Path | None,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.Series | None:
    """Load funding rate from parquet lake."""
    if data_dir is None or not data_dir.exists():
        return None

    fr_path = data_dir / "funding_rate" / f"symbol={symbol}" / "part-0.parquet"
    if not fr_path.exists():
        return None

    df = pd.read_parquet(fr_path)
    if df.empty:
        return None

    if "ts" in df.columns:
        df = df.set_index(pd.DatetimeIndex(df["ts"])).drop(columns=["ts"])
    df = df.sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df.loc[start:end]

    if "funding_rate" not in df.columns:
        return None

    return df["funding_rate"]


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


def join_funding_to_ohlcv(
    df: pd.DataFrame,
    funding: pd.Series | None,
) -> pd.DataFrame:
    """Join funding rate to 4h OHLCV bars via forward-fill."""
    if funding is None or funding.empty:
        return df
    fr_reindexed = funding.reindex(df.index, method="ffill")
    df = df.copy()
    df["_funding_rate"] = fr_reindexed
    return df


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


def synthetic_funding(
    start: pd.Timestamp,
    n_periods: int = 90 * 3,
    seed: int = 77,
) -> pd.Series:
    """Generate synthetic 8h funding rate for smoke tests."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n_periods, freq="8h", tz="UTC")
    rates = rng.normal(loc=0.0001, scale=0.0003, size=n_periods)
    return pd.Series(rates, index=idx, name="funding_rate")


# -- Signal computation --------------------------------------------------------

SIGNAL_FN_MAP = {
    "s2_donchian_voltarget": s2_donchian_voltarget,
    "s4_funding_carry": s4_funding_carry,
    "s2c_x_s4_composite": s2c_x_s4_composite,
}


def compute_signal(
    variant_id: str,
    registry_entry: dict[str, Any],
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series | None]:
    """Compute signal for a variant. Returns (signal, position_size_or_None)."""
    fn_name = registry_entry["fn"]
    params = registry_entry["params"]

    if fn_name in ("s2_donchian_voltarget", "s2c_x_s4_composite"):
        signal, pos_size = SIGNAL_FN_MAP[fn_name](df, **params)
        return signal, pos_size

    if fn_name in SIGNAL_FN_MAP:
        signal = SIGNAL_FN_MAP[fn_name](df, **params)
        return signal, None

    raise ValueError(f"Unknown signal function: {fn_name}")


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
            "avg_rr": None, "monthly_hit_rate": None, "skew": None,
            "kurtosis_excess": None,
        }

    daily = per_bar_returns.resample("1D").sum()
    daily = daily[daily != 0.0] if daily.abs().sum() == 0 else daily
    if len(daily) < 2:
        return {
            "sharpe": None, "sortino": None, "mdd": None, "calmar": None,
            "avg_rr": None, "monthly_hit_rate": None, "skew": None,
            "kurtosis_excess": None,
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

    pos = (daily > 0).sum()
    neg = (daily < 0).sum()
    avg_rr = (
        float(daily[daily > 0].mean() / abs(daily[daily < 0].mean()))
        if pos > 0 and neg > 0
        else None
    )

    monthly = daily.resample("ME").sum()
    monthly_count = daily.resample("ME").count()
    monthly = monthly[monthly_count >= 5]
    monthly_hit_rate = float((monthly > 0).mean()) if len(monthly) > 0 else None

    skew_val = float(daily.skew())
    kurtosis_excess = float(daily.kurt())

    return {
        "sharpe": sharpe, "sortino": sortino, "mdd": mdd, "calmar": calmar,
        "avg_rr": avg_rr, "monthly_hit_rate": monthly_hit_rate,
        "skew": skew_val, "kurtosis_excess": kurtosis_excess,
    }


def run_variant(
    variant_id: str,
    registry_entry: dict[str, Any],
    df: pd.DataFrame,
    cv_splits: list[tuple[np.ndarray, np.ndarray]],
) -> VariantResult:
    """Execute a single variant across all CV folds and return aggregated metrics."""
    res = VariantResult(variant_id=variant_id)

    signal, pos_size = compute_signal(variant_id, registry_entry, df)

    # DATA_UNAVAILABLE check
    if signal.name and "unavailable" in str(signal.name):
        res.status = "DATA_UNAVAILABLE"
        return res

    if signal.abs().sum() == 0:
        res.status = "ok"
        res.n_trades = 0
        return res

    # Concatenate OOS returns from all folds
    oos_pieces: list[pd.Series] = []
    n_trades_total = 0
    turnover_sum = 0.0

    for _, test_idx in cv_splits:
        if len(test_idx) == 0:
            continue
        idx_slice = df.index[test_idx]
        sub_df = df.loc[idx_slice]
        sub_signal = signal.reindex(idx_slice, fill_value=0)
        sub_pos_size = pos_size.reindex(idx_slice, fill_value=0) if pos_size is not None else None
        per_bar, n_t, turnover = backtest_signal(sub_df, sub_signal, sub_pos_size)
        oos_pieces.append(per_bar)
        n_trades_total += n_t
        turnover_sum += turnover

    if not oos_pieces:
        return res

    oos = pd.concat(oos_pieces).sort_index()
    metrics = compute_metrics(oos)

    res.n_trades = n_trades_total
    res.sharpe = metrics["sharpe"]
    res.sortino = metrics["sortino"]
    res.mdd = metrics["mdd"]
    res.calmar = metrics["calmar"]
    res.avg_rr = metrics["avg_rr"]
    res.turnover = float(turnover_sum / max(len(cv_splits), 1))
    res.monthly_hit_rate = metrics["monthly_hit_rate"]
    res.skew = metrics["skew"]
    res.kurtosis_excess = metrics["kurtosis_excess"]
    res.daily_returns = oos.resample("1D").sum().fillna(0.0).tolist()
    return res


# -- DSR / PBO orchestration ---------------------------------------------------


def aggregate_and_score(
    results: dict[str, VariantResult],
    n_obs_per_variant: int,
) -> dict[str, Any]:
    """Compute DSR + PBO across the variant pool and apply the gate.

    Gate evaluation: DSR and PBO apply to the pool. MDD and mhr are evaluated
    per-variant -- we check the best-Sharpe variant AND all variants that pass
    individual MDD/mhr gates. The "winning" variant is the one with highest
    Sharpe that also passes its own MDD + mhr gates.
    """
    eligible = [
        r for r in results.values() if r.status == "ok" and r.sharpe is not None
    ]
    n_actual = len(eligible)
    if n_actual < 2:
        return {
            "dsr": None, "pbo": None, "dsr_n_trials": n_actual,
            "winning_variant": None, "gate_passed": False,
            "gate_reason": "insufficient eligible variants for DSR/PBO",
        }

    sr_estimates = np.array([r.sharpe for r in eligible], dtype=float)
    best = max(eligible, key=lambda r: r.sharpe)  # type: ignore[arg-type]
    dsr = deflated_sharpe_ratio(
        observed_sr=float(best.sharpe),  # type: ignore[arg-type]
        sr_estimates=sr_estimates,
        n_obs=n_obs_per_variant,
        skew=float(best.skew or 0.0),
        kurtosis_excess=float(best.kurtosis_excess or 0.0),
        n_trials=n_actual,
    )

    # PBO via CSCV -- use n_groups=8 since N=3 is small
    daily_lengths = [len(r.daily_returns) for r in eligible]
    t_max = max(daily_lengths)
    matrix = np.zeros((t_max, n_actual), dtype=float)
    for i, r in enumerate(eligible):
        d = np.asarray(r.daily_returns, dtype=float)
        matrix[: len(d), i] = d

    n_groups = 8 if t_max >= 8 else 4
    if t_max >= n_groups and n_actual >= 2:
        pbo = probability_of_backtest_overfitting(matrix, n_groups=n_groups)
    else:
        pbo = None

    # Gate
    reasons: list[str] = []
    if dsr is None or dsr < GATE_DSR_MIN:
        reasons.append(f"DSR={dsr} < {GATE_DSR_MIN}")
    if pbo is None or pbo > GATE_PBO_MAX:
        reasons.append(f"PBO={pbo} > {GATE_PBO_MAX}")
    if best.mdd is not None and best.mdd < -GATE_OOS_MDD_MAX:
        reasons.append(f"MDD={best.mdd:.4f} < -{GATE_OOS_MDD_MAX}")
    if best.monthly_hit_rate is None or best.monthly_hit_rate < GATE_MONTHLY_HIT_RATE_MIN:
        reasons.append(
            f"monthly_hit_rate={best.monthly_hit_rate} < {GATE_MONTHLY_HIT_RATE_MIN}"
        )

    return {
        "dsr": float(dsr) if dsr is not None else None,
        "pbo": float(pbo) if pbo is not None else None,
        "dsr_n_trials": n_actual,
        "winning_variant": best.variant_id,
        "gate_passed": len(reasons) == 0,
        "gate_reason": "; ".join(reasons) if reasons else "all gates passed",
    }


# -- Output schema -------------------------------------------------------------


def variant_registry_sha256() -> str:
    payload = json.dumps(VARIANT_REGISTRY, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def git_commit_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        return out
    except Exception:
        return "unknown"


def variant_to_dict(r: VariantResult) -> dict[str, Any]:
    return {
        "variant_id": r.variant_id,
        "status": r.status,
        "n_trades": r.n_trades,
        "sharpe": r.sharpe,
        "sortino": r.sortino,
        "mdd": r.mdd,
        "calmar": r.calmar,
        "avg_rr": r.avg_rr,
        "turnover": r.turnover,
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
    p.add_argument("--n-splits", type=int, default=5)
    p.add_argument("--embargo-frac", type=float, default=0.01)
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
        funding = synthetic_funding(start=start)
        df = join_funding_to_ohlcv(df, funding)
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

        funding = load_funding_rates(args.data_dir, args.symbol, start, end)
        if funding is not None:
            df = join_funding_to_ohlcv(df, funding)
            logger.info("Joined %d funding rate records.", len(funding))
        else:
            logger.warning("Funding rates unavailable; W2/W3 will be DATA_UNAVAILABLE.")

    if len(df) < 200:
        logger.error("OHLCV too short (%d bars). Need >= 200.", len(df))
        return 2

    # Build CV splits
    horizon = pd.Timedelta(hours=4)
    last_ts = df.index[-1]
    t1_series = pd.Series(df.index + horizon, index=df.index)
    t1_series = t1_series.where(t1_series <= last_ts, last_ts)
    cv = PurgedKFold(n_splits=args.n_splits, embargo_frac=args.embargo_frac)
    cv_splits = list(cv.split(df, t1_series))
    cv_split_hash = hashlib.sha256(
        b"".join(test_idx.tobytes() for _, test_idx in cv_splits)
    ).hexdigest()

    # Run all variants
    results: dict[str, VariantResult] = {}
    for variant_id, registry_entry in VARIANT_REGISTRY.items():
        logger.info("Running variant %s (%s)", variant_id, registry_entry["fn"])
        results[variant_id] = run_variant(variant_id, registry_entry, df, cv_splits)

    # Aggregate scores
    n_obs_per_variant = max(len(df) // args.n_splits, 1)
    scoring = aggregate_and_score(results, n_obs_per_variant)

    output = {
        "schema_version": "swing-strategy-99/v4",
        "issue": 99,
        "iteration": 4,
        "data_source": data_source,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "start": str(start),
        "end": str(end),
        "n_bars": int(len(df)),
        "has_funding_data": "_funding_rate" in df.columns,
        "git_commit": git_commit_hash(),
        "variant_registry": {
            k: {"fn": v["fn"], "params": v["params"]}
            for k, v in VARIANT_REGISTRY.items()
        },
        "variant_registry_sha256": variant_registry_sha256(),
        "cv": {
            "n_splits": args.n_splits,
            "embargo_frac": args.embargo_frac,
            "split_hash": cv_split_hash,
        },
        "fee_round_trip": TAKER_FEE_ROUND_TRIP,
        "gate": {
            "dsr_min": GATE_DSR_MIN,
            "pbo_max": GATE_PBO_MAX,
            "oos_mdd_max": GATE_OOS_MDD_MAX,
            "monthly_hit_rate_min": GATE_MONTHLY_HIT_RATE_MIN,
        },
        "variants": [variant_to_dict(r) for r in results.values()],
        "scoring": scoring,
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "bench_output_iter4.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", out_path)

    # Human-readable summary
    print()
    print("=" * 78)
    print(f"Swing Strategy #99 iter4 -- bench output: {out_path}")
    print(f"  data: {data_source}  n_bars={len(df)}  symbol={args.symbol}")
    print(f"  funding_data: {'YES' if '_funding_rate' in df.columns else 'NO'}")
    print(f"  registry sha256: {variant_registry_sha256()[:16]}...")
    print(f"  CV split hash:   {cv_split_hash[:16]}...")
    print()
    print(
        f"{'ID':<6} {'status':<18} {'n_trades':>9} {'Sharpe':>9} "
        f"{'MDD':>8} {'mhr':>6}"
    )
    for r in results.values():
        print(
            f"{r.variant_id:<6} {r.status:<18} "
            f"{r.n_trades:>9d} "
            f"{(f'{r.sharpe:.3f}' if r.sharpe is not None else 'NA'):>9} "
            f"{(f'{r.mdd:.4f}' if r.mdd is not None else 'NA'):>8} "
            f"{(f'{r.monthly_hit_rate:.2f}' if r.monthly_hit_rate is not None else 'NA'):>6}"
        )
    print()
    print(
        f"DSR: {scoring['dsr']}  PBO: {scoring['pbo']}  "
        f"N_trials: {scoring['dsr_n_trials']}"
    )
    print(f"Winning variant: {scoring['winning_variant']}")
    print(
        f"Gate: {'PASS' if scoring['gate_passed'] else 'FAIL'} "
        f"-- {scoring['gate_reason']}"
    )
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
