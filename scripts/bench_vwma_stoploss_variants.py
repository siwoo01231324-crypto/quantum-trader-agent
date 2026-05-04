#!/usr/bin/env python3
"""Pre-registered factorial experiment: VWMA + stop-loss/take-profit variants.

Issue #147: VWMA + stop-loss/take-profit 통합 backtest (#99 후속).

Variant matrix (FROZEN — sha256 of VARIANT_REGISTRY embedded in output):
    B0: A (VWMA cross only) — #99 baseline sanity check
    B1: B0 + stop_loss(1%)
    B2: B0 + take_profit(7%)
    B3: B0 + stop(1%) + take(7%)  — Iranyi R:R rule
    B4: B (VWMA + ema_slope > 0) + stop(1%) + take(7%)  — core hypothesis
    B5: B4 + ATR-based stop (2*ATR)  — adaptive stop

Composition semantics:
    All variants use VWMA100 cross as the entry signal.
    Stop/take parameters wrap the intra-bar simulator in
    src/backtest/risk/stop_take.py.
    B5 computes ATR(14) at bar t-1 and sets stop = entry - 2*ATR.

Usage:
    python scripts/bench_vwma_stoploss_variants.py --dry-run
    python scripts/bench_vwma_stoploss_variants.py \\
        --data-dir lake/binance_futures_usdtm \\
        --output-dir docs/work/active/000147-vwma-stoploss \\
        --start 2020-01-01 --end 2025-12-31

Output: ``<output-dir>/bench_output_147.json`` with per-variant metrics.
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
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import importlib.util as _ilu
import types as _types

def _load_module(rel_path: str, fq_name: str):
    """Load a module by file path, registering it under fq_name in sys.modules.

    Registers parent package stubs so dataclasses.__module__ lookups work on
    Python 3.14 (sys.modules[cls.__module__] must not be None).
    """
    # Ensure all parent package names exist in sys.modules
    parts = fq_name.split(".")
    for i in range(1, len(parts)):
        pkg_name = ".".join(parts[:i])
        if pkg_name not in sys.modules:
            stub = _types.ModuleType(pkg_name)
            stub.__package__ = pkg_name
            stub.__path__ = []
            sys.modules[pkg_name] = stub

    spec = _ilu.spec_from_file_location(fq_name, REPO_ROOT / rel_path)
    mod = _ilu.module_from_spec(spec)
    sys.modules[fq_name] = mod
    spec.loader.exec_module(mod)
    return mod

_stop_take = _load_module("src/backtest/risk/stop_take.py", "src.backtest.risk.stop_take")
StopTakeConfig = _stop_take.StopTakeConfig
simulate_stop_take = _stop_take.simulate_stop_take

_vwma_mod = _load_module("src/features/vwma.py", "src.features.vwma")
vwma = _vwma_mod.vwma
vwma_cross = _vwma_mod.vwma_cross

_ma_mod = _load_module("src/features/ma_projection.py", "src.features.ma_projection")
ema_slope = _ma_mod.ema_slope

logger = logging.getLogger("bench_vwma_stoploss_variants")

# ---------------------------------------------------------------------------
# Pre-registered variant matrix (FROZEN — do not add variants post-hoc)
# ---------------------------------------------------------------------------

VARIANT_REGISTRY: dict[str, dict[str, Any]] = {
    "B0": {
        "description": "VWMA cross only — #99 baseline sanity check",
        "stop_loss_pct": None,
        "take_profit_pct": None,
        "ema_slope_filter": False,
        "atr_stop": False,
    },
    "B1": {
        "description": "B0 + stop_loss(1%)",
        "stop_loss_pct": 0.01,
        "take_profit_pct": None,
        "ema_slope_filter": False,
        "atr_stop": False,
    },
    "B2": {
        "description": "B0 + take_profit(7%)",
        "stop_loss_pct": None,
        "take_profit_pct": 0.07,
        "ema_slope_filter": False,
        "atr_stop": False,
    },
    "B3": {
        "description": "B0 + stop(1%) + take(7%) — Iranyi R:R rule",
        "stop_loss_pct": 0.01,
        "take_profit_pct": 0.07,
        "ema_slope_filter": False,
        "atr_stop": False,
    },
    "B4": {
        "description": "B (VWMA + ema_slope>0) + stop(1%) + take(7%) — core hypothesis",
        "stop_loss_pct": 0.01,
        "take_profit_pct": 0.07,
        "ema_slope_filter": True,
        "atr_stop": False,
    },
    "B5": {
        "description": "B4 + ATR-based adaptive stop (2*ATR14)",
        "stop_loss_pct": None,  # overridden dynamically per trade
        "take_profit_pct": 0.07,
        "ema_slope_filter": True,
        "atr_stop": True,
    },
}


def _registry_sha256() -> str:
    payload = json.dumps(VARIANT_REGISTRY, sort_keys=True, default=str).encode()
    return hashlib.sha256(payload).hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# ATR helper
# ---------------------------------------------------------------------------

def _atr(ohlcv: pd.DataFrame, window: int = 14) -> pd.Series:
    high = ohlcv["high"]
    low = ohlcv["low"]
    close = ohlcv["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    return tr.rolling(window, min_periods=window).mean().rename("atr")


# ---------------------------------------------------------------------------
# Per-trade stop simulation
# ---------------------------------------------------------------------------

def _run_trades_with_stop_take(
    ohlcv: pd.DataFrame,
    cross: pd.Series,
    variant: dict[str, Any],
) -> list[dict[str, Any]]:
    """Walk the OHLCV bar by bar, open/close trades based on cross + stop/take."""
    trades: list[dict[str, Any]] = []
    in_trade = False
    entry_price: float = 0.0
    entry_bar: pd.Timestamp | None = None
    entry_stop_loss_pct: float | None = None  # fixed at entry for ATR variants

    slope: pd.Series | None = None
    if variant["ema_slope_filter"]:
        slope = ema_slope(ohlcv["close"])

    atr_series: pd.Series | None = None
    if variant["atr_stop"]:
        atr_series = _atr(ohlcv)

    base_stop_loss_pct: float | None = variant["stop_loss_pct"]
    take_profit_pct: float | None = variant["take_profit_pct"]

    # Precompute next dead-cross index for signal exits (fast lookup)
    dead_mask = cross == "dead"

    for i, (ts, row) in enumerate(ohlcv.iterrows()):
        if not in_trade:
            # Entry: golden cross + optional slope filter
            if cross.iloc[i] != "golden":
                continue
            if slope is not None:
                sv = slope.iloc[i]
                if pd.isna(sv) or sv <= 0:
                    continue
            # Enter at this bar's close (next-bar open proxy)
            entry_price = float(row["close"])
            entry_bar = ts
            in_trade = True

            # Compute ATR-based stop at entry bar (fixed for trade duration)
            if variant["atr_stop"] and atr_series is not None:
                atr_val = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else float("nan")
                if not math.isnan(atr_val) and entry_price > 0:
                    entry_stop_loss_pct = (2.0 * atr_val) / entry_price
                else:
                    entry_stop_loss_pct = 0.02  # fallback 2%
            else:
                entry_stop_loss_pct = base_stop_loss_pct
        else:
            # In a trade — evaluate this single bar for stop/take/signal exit
            current_bar = ohlcv.iloc[i : i + 1]

            # Find next dead cross from current bar onward for signal exit
            future_dead = dead_mask.iloc[i:]
            dead_locs = future_dead[future_dead].index
            signal_exit_bar = dead_locs[0] if len(dead_locs) > 0 else None

            cfg = StopTakeConfig(
                stop_loss_pct=entry_stop_loss_pct,
                take_profit_pct=take_profit_pct,
            )
            result = simulate_stop_take(
                entry_price, current_bar, cfg, signal_exit_bar=signal_exit_bar
            )

            if result.reason is not None:
                exit_price = result.exit_price if result.exit_price is not None else float(row["close"])
                pnl_pct = (exit_price - entry_price) / entry_price
                trades.append(
                    {
                        "entry_bar": str(entry_bar),
                        "exit_bar": str(result.triggered_at),
                        "entry_price": entry_price,
                        "exit_price": exit_price,
                        "reason": result.reason,
                        "pnl_pct": pnl_pct,
                    }
                )
                in_trade = False
                entry_price = 0.0
                entry_bar = None
                entry_stop_loss_pct = None
            # No exit on this bar — continue to next bar

    return trades


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def _compute_metrics(trades: list[dict[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "n_trades": 0,
            "sharpe": float("nan"),
            "mdd_pct": float("nan"),
            "monthly_hit_rate": float("nan"),
            "total_return_pct": float("nan"),
        }

    returns = np.array([t["pnl_pct"] for t in trades], dtype=float)
    n = len(returns)
    mean_r = returns.mean()
    std_r = returns.std(ddof=1) if n > 1 else float("nan")
    sharpe = (mean_r / std_r) * math.sqrt(365) if std_r and not math.isnan(std_r) else float("nan")

    # MDD on cumulative return curve
    cum = np.cumprod(1 + returns)
    running_max = np.maximum.accumulate(cum)
    drawdowns = (cum - running_max) / running_max
    mdd = float(drawdowns.min()) if len(drawdowns) > 0 else float("nan")

    total_return = float(cum[-1] - 1) if len(cum) > 0 else float("nan")
    hit_rate = float((returns > 0).mean())

    return {
        "n_trades": n,
        "sharpe": round(sharpe, 4) if not math.isnan(sharpe) else None,
        "mdd_pct": round(mdd * 100, 2) if not math.isnan(mdd) else None,
        "monthly_hit_rate": round(hit_rate, 4),
        "total_return_pct": round(total_return * 100, 2) if not math.isnan(total_return) else None,
    }


# ---------------------------------------------------------------------------
# Dry-run: synthetic mini sample
# ---------------------------------------------------------------------------

def _make_dry_run_sample() -> pd.DataFrame:
    """Generate 1-month synthetic 1-minute OHLCV data for --dry-run."""
    rng = np.random.default_rng(42)
    n = 60 * 24 * 30  # ~1 month of 1-min bars
    idx = pd.date_range("2024-01-01", periods=n, freq="1min")
    log_returns = rng.normal(0, 0.0002, n)
    close = 40_000.0 * np.exp(np.cumsum(log_returns))
    vol_factor = rng.uniform(0.8, 1.2, n)
    high = close * (1 + rng.uniform(0, 0.002, n))
    low = close * (1 - rng.uniform(0, 0.002, n))
    opens = np.roll(close, 1)
    opens[0] = close[0]
    volume = (1_000 * vol_factor).astype(float)
    return pd.DataFrame(
        {"open": opens, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# Main bench loop
# ---------------------------------------------------------------------------

def run_bench(
    ohlcv: pd.DataFrame,
    output_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)

    registry_hash = _registry_sha256()
    git_commit = _git_commit()

    logger.info("variant_registry_sha256=%s", registry_hash)
    logger.info("git_commit=%s", git_commit)

    cross = vwma_cross(ohlcv["close"], ohlcv["volume"])

    results: dict[str, Any] = {}
    for variant_id, variant in VARIANT_REGISTRY.items():
        logger.info("Running variant %s: %s", variant_id, variant["description"])
        try:
            trades = _run_trades_with_stop_take(ohlcv, cross, variant)
            metrics = _compute_metrics(trades)
        except Exception as exc:
            logger.warning("Variant %s failed: %s", variant_id, exc)
            metrics = {"error": str(exc)}
        results[variant_id] = {
            "description": variant["description"],
            "metrics": metrics,
        }
        logger.info("  %s metrics: %s", variant_id, metrics)

    output = {
        "meta": {
            "variant_registry_sha256": registry_hash,
            "git_commit": git_commit,
            "dry_run": dry_run,
            "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "n_bars": len(ohlcv),
        },
        "variants": results,
    }

    out_path = output_dir / "bench_output_147.json"
    out_path.write_text(json.dumps(output, indent=2, default=str))
    logger.info("Output written to %s", out_path)
    return output


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry-run", action="store_true", help="Run on synthetic 1-month sample instead of real data")
    p.add_argument("--data-dir", type=Path, default=REPO_ROOT / "lake")
    p.add_argument("--output-dir", type=Path, default=REPO_ROOT / "docs" / "work" / "active" / "000147-vwma-stoploss")
    p.add_argument("--start", default="2020-01-01")
    p.add_argument("--end", default="2025-12-31")
    p.add_argument("--symbol", default="BTCUSDT")
    p.add_argument("--freq", default="1m", help="Source data frequency in lake (default: 1m)")
    p.add_argument("--resample-to", default="4h", dest="resample_to",
                   help="Resample to this pandas offset before backtesting (default: 4h). Set empty string to skip.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.dry_run:
        logger.info("--dry-run: generating synthetic 1-month sample")
        ohlcv = _make_dry_run_sample()
    else:
        # Load from Parquet lake using load_ohlcv_from_parquet
        try:
            _bundle_mod = _load_module("src/backtest/bundle.py", "src.backtest.bundle")
            load_ohlcv_from_parquet = _bundle_mod.load_ohlcv_from_parquet

            ohlcv = load_ohlcv_from_parquet(
                args.data_dir,
                symbol=args.symbol,
                freq=args.freq,
                start=args.start,
                end=args.end,
            )
            if ohlcv.empty:
                logger.error("No data loaded from %s. Check --data-dir and --symbol.", args.data_dir)
                sys.exit(1)
            logger.info("Loaded %d bars (%s to %s)", len(ohlcv), ohlcv.index[0], ohlcv.index[-1])

            # Downsample to target frequency if needed (1m data is ~2.6M bars for 5yr)
            if args.resample_to:
                logger.info("Resampling from %s to %s", args.freq, args.resample_to)
                ohlcv = ohlcv.resample(args.resample_to).agg({
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }).dropna()
                logger.info("After resample: %d bars", len(ohlcv))
        except Exception as exc:
            logger.error("Failed to load data: %s. Use --dry-run for smoke test.", exc)
            sys.exit(1)

    run_bench(ohlcv, args.output_dir, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
