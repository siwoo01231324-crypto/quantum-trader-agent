#!/usr/bin/env python3
"""Pre-registered regime-switching bench: R0-R5 variant matrix.

Issue #173. Builds on PR #172 (S2c/S4 strategies) and iter 5 bench pattern.

Variant Matrix (frozen):
    R0: S2c always (baseline, no regime)
    R1: S4 always (baseline 2)
    R2: HMM-2state on returns (vol regime) -> high-vol=S4, low-vol=S2c
    R3: HMM-3state (returns + funding) -> bull=S2c, bear/sideways=S4, crash=flat
    R4: Threshold-based switch (30d return > 0 = S2c, funding < 0 = S4)
    R5: Ensemble vote (R2 + R3 + R4 majority)

Usage:
    python scripts/bench_regime_switching.py --smoke
    python scripts/bench_regime_switching.py \
        --data-dir lake \
        --output-dir docs/work/active/000173-hmm-regime-detection \
        --start 2020-01-01 --end 2025-12-31

Output: ``<output-dir>/bench_output_regime_switching.json``
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "src"))

from src.backtest.swing.regime_switching import VARIANT_REGISTRY  # noqa: E402

logger = logging.getLogger("bench_regime_switching")

TAKER_FEE_ROUND_TRIP = 0.0008


@dataclass
class VariantResult:
    variant_id: str
    desc: str
    n_trades: int = 0
    sharpe: float | None = None
    sortino: float | None = None
    mdd: float | None = None
    calmar: float | None = None
    monthly_hit_rate: float | None = None
    skew: float | None = None
    kurtosis_excess: float | None = None
    status: str = "ok"
    error: str | None = None


# -- Data loading (reused from iter 5 bench) ----------------------------------


def load_ohlcv(
    data_dir: Path | None,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
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
        logger.warning("OHLCV missing columns %s", missing)
        return None
    return df


def resample_ohlcv(frame: pd.DataFrame, freq: str) -> pd.DataFrame:
    if freq.lower() in ("1min", "1m", "1mn"):
        return frame
    agg: dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
    }
    extra_cols = {}
    if "_funding_rate" in frame.columns:
        extra_cols["_funding_rate"] = "last"
    avail = {k: v for k, v in {**agg, **extra_cols}.items() if k in frame.columns}
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
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq="1min", tz="UTC")
    drift = 0.00002
    sigma = 0.001
    log_returns = rng.normal(loc=drift, scale=sigma, size=n_bars)
    close = 30000.0 * np.exp(log_returns.cumsum())
    volume = rng.lognormal(mean=2.0, sigma=0.5, size=n_bars)
    funding = rng.normal(-0.0001, 0.0002, size=n_bars)
    df = pd.DataFrame(
        {
            "open": close,
            "high": close * (1 + np.abs(rng.normal(scale=0.0005, size=n_bars))),
            "low": close * (1 - np.abs(rng.normal(scale=0.0005, size=n_bars))),
            "close": close,
            "volume": volume,
            "_funding_rate": funding,
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
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)

    position = signal.reindex(close.index, fill_value=0).shift(1).fillna(0)

    if position_size is not None:
        ps = position_size.reindex(close.index, fill_value=0).shift(1).fillna(0)
        effective_pos = position * ps
    else:
        effective_pos = position.astype(float)

    pnl = effective_pos * bar_ret

    pos_change = effective_pos.diff().abs().fillna(0)
    fee = pos_change * fee_round_trip
    pnl -= fee

    n_trades = int(position.diff().abs().fillna(0).sum())
    turnover = float(pos_change.sum()) / max(len(position), 1)
    return pnl, n_trades, turnover


def compute_metrics(per_bar_returns: pd.Series) -> dict[str, float | None]:
    if per_bar_returns.empty or per_bar_returns.abs().sum() == 0:
        return {
            "sharpe": None, "sortino": None, "mdd": None, "calmar": None,
            "monthly_hit_rate": None, "skew": None, "kurtosis_excess": None,
        }

    daily = per_bar_returns.resample("1D").sum()
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


# -- Output schema -------------------------------------------------------------


def variant_registry_sha256() -> str:
    payload = json.dumps(
        {k: v["desc"] for k, v in VARIANT_REGISTRY.items()},
        sort_keys=True,
    ).encode()
    return hashlib.sha256(payload).hexdigest()


def git_commit_hash() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
        return out
    except Exception:
        return "unknown"


def result_to_dict(r: VariantResult) -> dict[str, Any]:
    return {
        "variant_id": r.variant_id,
        "desc": r.desc,
        "status": r.status,
        "error": r.error,
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
        default=REPO_ROOT / "docs/work/active/000173-hmm-regime-detection",
    )
    p.add_argument("--symbol", type=str, default="BTCUSDT")
    p.add_argument("--timeframe", type=str, default="4h")
    p.add_argument("--start", type=str, default="2020-01-01")
    p.add_argument("--end", type=str, default="2025-12-31")
    p.add_argument(
        "--smoke", action="store_true",
        help="Run on 90 days of synthetic data (no real data needed).",
    )
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
                "OHLCV unavailable from %s for %s. Re-run with --smoke.",
                args.data_dir, args.symbol,
            )
            return 2
        df = resample_ohlcv(df, args.timeframe)
        data_source = f"{args.data_dir}::{args.symbol}@{args.timeframe}"

    if len(df) < 200:
        logger.error("OHLCV too short (%d bars). Need >= 200.", len(df))
        return 2

    logger.info("Data: %s  n_bars=%d", data_source, len(df))

    results: list[VariantResult] = []
    for vid, spec in VARIANT_REGISTRY.items():
        logger.info("Running %s: %s", vid, spec["desc"])
        res = VariantResult(variant_id=vid, desc=spec["desc"])
        try:
            signal, pos_size = spec["fn"](df)
            if signal.abs().sum() == 0:
                res.status = "no_signal"
            else:
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
        except Exception as exc:
            res.status = "error"
            res.error = str(exc)
            logger.exception("Error in %s", vid)
        results.append(res)

    output = {
        "schema_version": "regime-switching-173/v1",
        "issue": 173,
        "test_type": "pre_registered_variant_matrix",
        "data_source": data_source,
        "symbol": args.symbol,
        "timeframe": args.timeframe,
        "start": str(start),
        "end": str(end),
        "n_bars": int(len(df)),
        "git_commit": git_commit_hash(),
        "variant_registry_sha256": variant_registry_sha256(),
        "fee_round_trip": TAKER_FEE_ROUND_TRIP,
        "variants": [result_to_dict(r) for r in results],
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "bench_output_regime_switching.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", out_path)

    print()
    print("=" * 78)
    print("Regime Switching Bench #173 -- R0-R5 Variant Matrix")
    print(f"  data: {data_source}  n_bars={len(df)}  symbol={args.symbol}")
    print(f"  registry sha256: {variant_registry_sha256()[:16]}...")
    print()
    for r in results:
        status_str = f"[{r.status}]"
        if r.sharpe is not None:
            print(
                f"  {r.variant_id} {status_str:12s}  "
                f"Sharpe={r.sharpe:+.3f}  MDD={r.mdd:.4f}  "
                f"mhr={r.monthly_hit_rate:.3f}  trades={r.n_trades}"
            )
        else:
            print(f"  {r.variant_id} {status_str:12s}  {r.error or 'no metrics'}")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
