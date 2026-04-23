#!/usr/bin/env python3
"""Compare momo-btc-v2 baseline (size=1.0) vs half-Kelly sizing.

Runs the same backtest twice on the same OHLCV, once per sizing_mode, and
writes the metrics to JSON. The AC for #69 requires a comparison, not an
improvement guarantee (half-Kelly may underperform on out-of-sample data;
the point is to produce and log the numbers).

Usage:
    python scripts/compare_momo_btc_v2_sizing.py \\
        --data-dir lake/ --start 2025-04-01 --end 2026-04-01 \\
        --out docs/work/active/000069-position-sizing/sizing_comparison.json

If no real data is available via --data-dir, the script falls back to a
seeded synthetic OHLCV so CI and local smoke runs still produce output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from backtest.engine import BacktestConfig, run_backtest
from backtest.strategies.momo_btc_v2 import MomoBtcV2


def _synthetic_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 30_000.0 + np.cumsum(rng.standard_normal(n) * 50.0)
    closes = np.maximum(closes, 100.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def _load_real_ohlcv(data_dir: Path, start: str | None, end: str | None) -> pd.DataFrame | None:
    try:
        from backtest.bundle import load_ohlcv_from_parquet
    except Exception:
        return None
    try:
        df = load_ohlcv_from_parquet(
            data_dir, symbol="BTCUSDT", freq="15m", start=start, end=end
        )
    except Exception:
        return None
    if df is None or df.empty:
        return None
    return df


def _run(ohlcv: pd.DataFrame, mode: str) -> dict[str, Any]:
    strategy = MomoBtcV2(sizing_mode=mode)  # type: ignore[arg-type]
    config = BacktestConfig(initial_cash=10_000.0)
    result = run_backtest(ohlcv, strategy, config)
    m = result.metrics
    return {
        "mode": mode,
        "sharpe": float(m["sharpe"]),
        "mdd": float(m["mdd"]),
        "total_return": float(m["total_return"]),
        "trades": int(m["trades"]),
        "win_rate": float(m["win_rate"]),
        "final_equity": float(result.equity_curve.iloc[-1]),
    }


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data-dir", default="lake/", type=Path)
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.add_argument(
        "--out",
        default="docs/work/active/000069-position-sizing/sizing_comparison.json",
        type=Path,
    )
    p.add_argument("--synthetic-bars", type=int, default=2000,
                   help="Synthetic OHLCV bar count when real data is unavailable.")
    p.add_argument("--seed", type=int, default=42)
    return p


def main() -> None:
    args = build_parser().parse_args()

    ohlcv = _load_real_ohlcv(args.data_dir, args.start, args.end)
    source = "parquet"
    if ohlcv is None:
        ohlcv = _synthetic_ohlcv(n=args.synthetic_bars, seed=args.seed)
        source = f"synthetic(n={args.synthetic_bars},seed={args.seed})"

    print(f"Loaded {len(ohlcv)} bars ({source})")

    baseline = _run(ohlcv, "full")
    half = _run(ohlcv, "half-kelly")
    voltarg = _run(ohlcv, "vol-target")

    report = {
        "strategy": "momo-btc-v2",
        "data_source": source,
        "bars": int(len(ohlcv)),
        "period": [str(ohlcv.index[0]), str(ohlcv.index[-1])],
        "runs": [baseline, half, voltarg],
        "delta_sharpe_half_vs_full": half["sharpe"] - baseline["sharpe"],
        "delta_sharpe_voltarg_vs_full": voltarg["sharpe"] - baseline["sharpe"],
    }

    out_path = (ROOT / args.out) if not args.out.is_absolute() else args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
