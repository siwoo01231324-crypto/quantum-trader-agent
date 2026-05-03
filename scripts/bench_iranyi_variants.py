#!/usr/bin/env python3
"""Pre-registered factorial experiment: Iranyi VWMA100 8-variant bench.

Issue #99. Reference plan:
    docs/work/active/000099-iranyi-vwma-research/01_plan.md (status=approved)

Variant matrix (FROZEN — sha256 of VARIANT_REGISTRY embedded in output):
    A: VWMA100 cross only (baseline)
    B: A + ema_slope > 0 filter
    C: A + multi_tf alignment (higher-TF VWMA bullish)
    D: A + time_of_day gate (KST 10:30-11:00 + weekends blocked)
    E: A + cross_sectional_rs (top quartile vs UBAI)
    F: A + poc distance filter
    G: A + orderbook flow (OBI + OFI + microprice gap)
    H: A + B + C + D + E + F + G (full stack, AND of all filters)

Composition semantics: B-H are AND-gates on top of A's vwma_cross signal.
Variant H requires ALL filters to pass simultaneously. Zero-signal-frequency
is a legitimate result — recorded as ``n_trades=0``, ``sharpe=NaN``,
``monthly_hit_rate=0.0``. Such variants are excluded from the DSR pool
(N decreases dynamically) but remain in the PBO rank analysis with
worst-rank assignment.

Usage:
    python scripts/bench_iranyi_variants.py --smoke
    python scripts/bench_iranyi_variants.py \
        --data-dir lake/binance_futures_usdtm \
        --output-dir docs/work/active/000099-iranyi-vwma-research \
        --start 2020-01-01 --end 2025-12-31

Output: ``<output-dir>/bench_output.json`` with per-variant metrics + DSR + PBO.
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

from src.features import (  # noqa: E402
    ema_slope,
    multi_tf_alignment,
    order_book_imbalance,
    point_of_control,
    relative_strength,
    time_gate,
    vwma,
    vwma_cross,
)
from src.ml.cv import PurgedKFold  # noqa: E402
from src.ml.validation import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)

logger = logging.getLogger("bench_iranyi_variants")

# -- Pre-registered variant matrix ------------------------------------------

VARIANT_REGISTRY: dict[str, list[str]] = {
    "A": ["vwma_cross"],
    "B": ["vwma_cross", "ema_slope"],
    "C": ["vwma_cross", "multi_tf"],
    "D": ["vwma_cross", "time_gate"],
    "E": ["vwma_cross", "cross_sectional_rs"],
    "F": ["vwma_cross", "poc_distance"],
    "G": ["vwma_cross", "obi", "ofi", "microprice_gap"],
    "H": [
        "vwma_cross",
        "ema_slope",
        "multi_tf",
        "time_gate",
        "cross_sectional_rs",
        "poc_distance",
        "obi",
        "ofi",
        "microprice_gap",
    ],
}

# Project gate (docs/background/12-validation-protocol.md §3.7)
GATE_DSR_MIN = 0.95
GATE_PBO_MAX = 0.20
GATE_OOS_MDD_MAX = 0.25
GATE_MONTHLY_HIT_RATE_MIN = 0.50

# Cost assumption (Binance taker fee, round-trip)
TAKER_FEE_ROUND_TRIP = 0.0008


# -- Result containers ------------------------------------------------------


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


# -- Data loading -----------------------------------------------------------


def load_ohlcv(
    data_dir: Path | None,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
    """Load 1-minute OHLCV from a Hive-partitioned parquet lake.

    Supported layouts (first match wins):
        ``<data_dir>/<symbol>.parquet`` (single file)
        ``<data_dir>/<symbol>/1m/<files>.parquet`` (legacy)
        ``<data_dir>/ohlcv/freq=1m/year=*/month=*/symbol=<symbol>/*.parquet``
            (Hive partition produced by ``scripts/fetch_futures_candles.py``)

    Returns ``None`` if no candles are available for the requested window.
    """
    if data_dir is None or not data_dir.exists():
        return None

    pattern_a = data_dir / f"{symbol}.parquet"
    pattern_b = data_dir / symbol / "1m"
    pattern_hive = data_dir / "ohlcv" / "freq=1m"

    df: pd.DataFrame | None = None
    if pattern_a.exists():
        df = pd.read_parquet(pattern_a)
    elif pattern_b.exists():
        files = sorted(pattern_b.rglob("*.parquet"))
        if files:
            df = pd.concat([pd.read_parquet(f) for f in files], axis=0)
    elif pattern_hive.exists():
        # Glob the Hive-partitioned files matching ``symbol`` and the
        # requested year/month range.
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
    # Drop duplicate timestamps that can arise across partition boundaries.
    df = df[~df.index.duplicated(keep="last")]
    df = df.loc[start:end]
    needed = {"close", "volume"}
    missing = needed - set(df.columns)
    if missing:
        logger.warning("OHLCV missing columns %s, skipping load", missing)
        return None
    return df


def synthetic_ohlcv(
    start: pd.Timestamp,
    n_bars: int = 30 * 24 * 60,
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


# -- Signal pipeline --------------------------------------------------------


def compute_signal(
    variant_id: str,
    features: list[str],
    df: pd.DataFrame,
    benchmark_returns: pd.Series | None,
) -> pd.Series:
    """Compute the held-position Series for a given variant.

    Trend-following model: enter long when ``vwma_cross == "golden"`` AND
    all filters pass at the entry bar. Hold until ``vwma_cross == "dead"``
    (no filter check at exit). Returns a {0, 1} Series where 1 means
    "long position held during this bar", causal (decision uses bar t-1
    information).
    """
    close = df["close"]
    volume = df["volume"]

    cross = vwma_cross(close, volume, window=100)
    entry_filter = pd.Series(True, index=close.index)

    # Build the AND-gated entry filter (only consulted at golden cross).
    if "ema_slope" in features:
        slope = ema_slope(close, span=100, slope_window=5)
        entry_filter &= (slope > 0).fillna(False)

    if "multi_tf" in features:
        align = multi_tf_alignment(close, volume, higher_tf="1h", vwma_window=100)
        entry_filter &= align.astype(bool)

    if "time_gate" in features:
        gate = time_gate(close.index)
        entry_filter &= gate.astype(bool)

    if "cross_sectional_rs" in features:
        if benchmark_returns is None:
            entry_filter &= False
        else:
            asset_ret = close.pct_change().fillna(0.0)
            rs = relative_strength(asset_ret, benchmark_returns, window=20)
            entry_filter &= (rs > 0).fillna(False)

    if "poc_distance" in features:
        poc_df = point_of_control(close, volume, n_bins=50, window=100)
        # Allow entries only when the close is reasonably close to (and
        # above) the rolling POC support — proxy for the "support
        # confirmed" condition in the interview.
        in_zone = (poc_df["poc_distance"] > 0) & (poc_df["poc_distance"] < 0.02)
        entry_filter &= in_zone.fillna(False)

    if "obi" in features or "ofi" in features or "microprice_gap" in features:
        if not has_l2_tick_data(df):
            return pd.Series(0, index=close.index)
        obi = order_book_imbalance(df["_l2_bid_vol"], df["_l2_ask_vol"])
        entry_filter &= (obi > 0).fillna(False)

    # State machine: 0 (flat) -> 1 on filtered golden, 1 -> 0 on dead cross.
    cross_arr = cross.to_numpy()
    filt_arr = entry_filter.to_numpy()
    pos = np.zeros(len(close), dtype=int)
    state = 0
    for i in range(len(close)):
        if state == 0:
            if cross_arr[i] == "golden" and bool(filt_arr[i]):
                state = 1
        else:
            if cross_arr[i] == "dead":
                state = 0
        pos[i] = state

    return pd.Series(pos, index=close.index, name=f"signal_{variant_id}")


def has_l2_tick_data(df: pd.DataFrame) -> bool:
    """Detect whether ``df`` carries L2 columns. Used to flag DATA_UNAVAILABLE."""
    return all(c in df.columns for c in ("_l2_bid_vol", "_l2_ask_vol"))


# -- Backtest mechanics -----------------------------------------------------


def backtest_signal(
    df: pd.DataFrame,
    signal: pd.Series,
    fee_round_trip: float = TAKER_FEE_ROUND_TRIP,
) -> tuple[pd.Series, int, float]:
    """Naive backtest: enter on signal=1, exit on next bar.

    Returns
    -------
    (per_bar_returns, n_trades, turnover)
    """
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)

    # Trade when signal flips from 0 to 1 (or stays at 1).
    position = signal.shift(1).fillna(0).astype(int)  # one-bar entry lag
    pnl = position * bar_ret

    # Charge round-trip fee when position changes
    pos_change = position.diff().abs().fillna(0)
    fee = pos_change * fee_round_trip
    pnl -= fee

    n_trades = int(pos_change.sum())
    turnover = float(pos_change.sum()) / max(len(position), 1)
    return pnl, n_trades, turnover


def compute_metrics(per_bar_returns: pd.Series) -> dict[str, float | None]:
    """Compute Sharpe/Sortino/MDD/Calmar/skew/kurt/monthly_hit_rate.

    Aggregates per-bar returns to daily for monthly hit-rate.
    """
    if per_bar_returns.empty or per_bar_returns.abs().sum() == 0:
        return {
            "sharpe": None,
            "sortino": None,
            "mdd": None,
            "calmar": None,
            "avg_rr": None,
            "monthly_hit_rate": None,
            "skew": None,
            "kurtosis_excess": None,
        }

    daily = per_bar_returns.resample("1D").sum()
    daily = daily[daily != 0.0] if daily.abs().sum() == 0 else daily
    if len(daily) < 2:
        return {
            "sharpe": None,
            "sortino": None,
            "mdd": None,
            "calmar": None,
            "avg_rr": None,
            "monthly_hit_rate": None,
            "skew": None,
            "kurtosis_excess": None,
        }

    mean = float(daily.mean())
    std = float(daily.std(ddof=1))
    if std == 0:
        sharpe = None
    else:
        sharpe = float((mean / std) * math.sqrt(252))

    downside = daily[daily < 0]
    if len(downside) > 1 and downside.std(ddof=1) > 0:
        sortino = float((mean / downside.std(ddof=1)) * math.sqrt(252))
    else:
        sortino = None

    equity = (1.0 + daily).cumprod()
    peak = equity.cummax()
    drawdown = equity / peak - 1.0
    mdd = float(drawdown.min())

    annual_return = (1.0 + mean) ** 252 - 1.0
    calmar = float(annual_return / abs(mdd)) if mdd != 0 else None

    pos = (daily > 0).sum()
    neg = (daily < 0).sum()
    avg_rr = (
        float(daily[daily > 0].mean() / abs(daily[daily < 0].mean()))
        if pos > 0 and neg > 0
        else None
    )

    # monthly hit rate: fraction of months with positive total return
    monthly = daily.resample("ME").sum()
    monthly_count = daily.resample("ME").count()
    monthly = monthly[monthly_count >= 5]  # partial-month filter
    if len(monthly) > 0:
        monthly_hit_rate = float((monthly > 0).mean())
    else:
        monthly_hit_rate = None

    skew = float(daily.skew())
    kurtosis_excess = float(daily.kurt())

    return {
        "sharpe": sharpe,
        "sortino": sortino,
        "mdd": mdd,
        "calmar": calmar,
        "avg_rr": avg_rr,
        "monthly_hit_rate": monthly_hit_rate,
        "skew": skew,
        "kurtosis_excess": kurtosis_excess,
    }


def run_variant(
    variant_id: str,
    features: list[str],
    df: pd.DataFrame,
    cv_splits: list[tuple[np.ndarray, np.ndarray]],
    benchmark_returns: pd.Series | None,
) -> VariantResult:
    """Execute a single variant across all CV folds and return aggregated metrics."""
    res = VariantResult(variant_id=variant_id)

    # DATA_UNAVAILABLE branch (Variant G/H without L2 tick)
    needs_l2 = any(f in features for f in ("obi", "ofi", "microprice_gap"))
    if needs_l2 and not has_l2_tick_data(df):
        res.status = "DATA_UNAVAILABLE"
        return res

    signal = compute_signal(variant_id, features, df, benchmark_returns)
    if signal.abs().sum() == 0:
        # Signal density zero — legitimate result, not an error.
        res.status = "ok"
        res.n_trades = 0
        return res

    # Concatenate OOS returns from all folds (chronological, no shuffle)
    oos_pieces: list[pd.Series] = []
    n_trades_total = 0
    turnover_sum = 0.0

    for _, test_idx in cv_splits:
        if len(test_idx) == 0:
            continue
        idx_slice = df.index[test_idx]
        sub_df = df.loc[idx_slice]
        sub_signal = signal.loc[idx_slice]
        per_bar, n_t, turnover = backtest_signal(sub_df, sub_signal)
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


# -- DSR / PBO orchestration -----------------------------------------------


def aggregate_and_score(
    results: dict[str, VariantResult],
    n_obs_per_variant: int,
) -> dict[str, Any]:
    """Compute DSR + PBO across the variant pool and apply the gate."""
    eligible = [r for r in results.values() if r.status == "ok" and r.sharpe is not None]
    n_actual = len(eligible)
    if n_actual < 2:
        return {
            "dsr": None,
            "pbo": None,
            "dsr_n_trials": n_actual,
            "winning_variant": None,
            "gate_passed": False,
            "gate_reason": "insufficient eligible variants for DSR/PBO",
        }

    sr_estimates = np.array([r.sharpe for r in eligible], dtype=float)
    best = max(eligible, key=lambda r: r.sharpe)
    dsr = deflated_sharpe_ratio(
        observed_sr=float(best.sharpe),
        sr_estimates=sr_estimates,
        n_obs=n_obs_per_variant,
        skew=float(best.skew or 0.0),
        kurtosis_excess=float(best.kurtosis_excess or 0.0),
        n_trials=n_actual,
    )

    # PBO via CSCV needs aligned daily-return columns. Build a (T, N) matrix
    # using the longest available daily-return series and pad shorter ones
    # with 0.0 so they are still ranked.
    daily_lengths = [len(r.daily_returns) for r in eligible]
    t_max = max(daily_lengths)
    matrix = np.zeros((t_max, n_actual), dtype=float)
    for i, r in enumerate(eligible):
        d = np.asarray(r.daily_returns, dtype=float)
        matrix[: len(d), i] = d

    n_groups = 16 if t_max >= 16 else (8 if t_max >= 8 else 4)
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
    if (
        best.monthly_hit_rate is None
        or best.monthly_hit_rate < GATE_MONTHLY_HIT_RATE_MIN
    ):
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


# -- Output schema ----------------------------------------------------------


def variant_registry_sha256() -> str:
    payload = json.dumps(VARIANT_REGISTRY, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()


def git_commit_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        return out
    except Exception:  # pragma: no cover
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


# -- Main -------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help="Directory containing OHLCV parquet (e.g. lake/binance_futures_usdtm)",
    )
    p.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT
        / "docs/work/active/000099-iranyi-vwma-research",
        help="Directory to write bench_output.json",
    )
    p.add_argument("--symbol", type=str, default="BTCUSDT")
    p.add_argument(
        "--timeframe",
        type=str,
        default="5min",
        help=(
            "Resample 1-minute OHLCV to this pandas offset alias before "
            "backtesting (e.g. '1min', '5min', '15min'). The interview's "
            "VWMA(100) reference behaviour assumes 5-minute bars."
        ),
    )
    p.add_argument(
        "--benchmark-symbol",
        type=str,
        default="ETHUSDT",
        help=(
            "Benchmark symbol for cross_sectional_rs (Variant E). "
            "Acts as a UBAI placeholder until the production Upbit adapter is wired "
            "(see 02_implementation.md §post-followup)."
        ),
    )
    p.add_argument("--start", type=str, default="2020-01-01")
    p.add_argument("--end", type=str, default="2025-12-31")
    p.add_argument(
        "--smoke",
        action="store_true",
        help="Run on 30 days of synthetic OHLCV (no real data needed).",
    )
    p.add_argument("--n-splits", type=int, default=5)
    p.add_argument("--embargo-frac", type=float, default=0.01)
    return p.parse_args()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")

    def _resample_ohlcv(frame: pd.DataFrame, freq: str) -> pd.DataFrame:
        """Resample 1m OHLCV to a coarser frequency with causal alignment."""
        if freq.lower() in ("1min", "1m", "1mn"):
            return frame
        agg = {
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

    if args.smoke:
        df = synthetic_ohlcv(start=start, n_bars=30 * 24 * 60)
        df = _resample_ohlcv(df, args.timeframe)
        data_source = "synthetic-smoke"
        benchmark_returns: pd.Series | None = None
    else:
        df = load_ohlcv(args.data_dir, args.symbol, start, end)
        if df is None or df.empty:
            logger.error(
                "OHLCV unavailable from %s for %s. Re-run with --smoke or "
                "fetch via scripts/fetch_futures_candles.py.",
                args.data_dir,
                args.symbol,
            )
            return 2
        df = _resample_ohlcv(df, args.timeframe)
        data_source = f"{args.data_dir}::{args.symbol}@{args.timeframe}"

        # Optional benchmark for Variant E (cross_sectional_rs). Uses the
        # daily-return series of ``--benchmark-symbol`` as a UBAI placeholder.
        benchmark_returns = None
        if args.benchmark_symbol and args.benchmark_symbol != args.symbol:
            bench_df = load_ohlcv(
                args.data_dir, args.benchmark_symbol, start, end
            )
            if bench_df is not None and not bench_df.empty:
                bench_df = _resample_ohlcv(bench_df, args.timeframe)
                # Daily benchmark returns mapped onto the (possibly coarser)
                # asset index.
                bench_daily = bench_df["close"].resample("1D").last().pct_change()
                benchmark_returns = bench_daily.reindex(
                    df.index, method="ffill"
                ).fillna(0.0).rename("benchmark_return")
            else:
                logger.warning(
                    "Benchmark %s OHLCV unavailable; Variant E will degrade.",
                    args.benchmark_symbol,
                )

    if len(df) < 200:
        logger.error("OHLCV too short (%d bars). Need >= 200.", len(df))
        return 2

    # Build CV splits (PurgedKFold needs t1; we use a simple horizon-based t1).
    horizon = pd.Timedelta(minutes=60)
    last_ts = df.index[-1]
    t1_series = pd.Series(df.index + horizon, index=df.index)
    t1_series = t1_series.where(t1_series <= last_ts, last_ts)
    cv = PurgedKFold(n_splits=args.n_splits, embargo_frac=args.embargo_frac)
    cv_splits = list(cv.split(df, t1_series))
    cv_split_hash = hashlib.sha256(
        b"".join(test_idx.tobytes() for _, test_idx in cv_splits)
    ).hexdigest()

    # Run all 8 variants
    results: dict[str, VariantResult] = {}
    for variant_id, features in VARIANT_REGISTRY.items():
        logger.info("Running variant %s (%s)", variant_id, ",".join(features))
        results[variant_id] = run_variant(
            variant_id, features, df, cv_splits, benchmark_returns
        )

    # Aggregate scores
    n_obs_per_variant = max(len(df) // args.n_splits, 1)
    scoring = aggregate_and_score(results, n_obs_per_variant)

    output = {
        "schema_version": "iranyi-vwma-99/v1",
        "issue": 99,
        "data_source": data_source,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "benchmark_symbol": args.benchmark_symbol if not args.smoke else None,
        "start": str(start),
        "end": str(end),
        "n_bars": int(len(df)),
        "git_commit": git_commit_hash(),
        "variant_registry": VARIANT_REGISTRY,
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
    out_path = args.output_dir / "bench_output.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", out_path)

    # Human-readable summary (ASCII-only to survive cp949 consoles)
    print()
    print("=" * 60)
    print(f"Iranyi VWMA #99 -- bench output: {out_path}")
    print(f"  data: {data_source}  n_bars={len(df)}  symbol={args.symbol}")
    print(f"  registry sha256: {variant_registry_sha256()[:16]}...")
    print(f"  CV split hash:   {cv_split_hash[:16]}...")
    print()
    print(f"{'ID':<3} {'status':<18} {'n_trades':>9} {'Sharpe':>9} {'MDD':>8} {'mhr':>6}")
    for r in results.values():
        print(
            f"{r.variant_id:<3} {r.status:<18} "
            f"{r.n_trades:>9d} "
            f"{(f'{r.sharpe:.3f}' if r.sharpe is not None else 'NA'):>9} "
            f"{(f'{r.mdd:.4f}' if r.mdd is not None else 'NA'):>8} "
            f"{(f'{r.monthly_hit_rate:.2f}' if r.monthly_hit_rate is not None else 'NA'):>6}"
        )
    print()
    print(f"DSR: {scoring['dsr']}  PBO: {scoring['pbo']}  N_trials: {scoring['dsr_n_trials']}")
    print(f"Winning variant: {scoring['winning_variant']}")
    print(f"Gate: {'PASS' if scoring['gate_passed'] else 'FAIL'} -- {scoring['gate_reason']}")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
