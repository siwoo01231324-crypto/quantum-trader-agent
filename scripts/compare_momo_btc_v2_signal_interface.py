#!/usr/bin/env python3
"""Compare momo-btc-v2 baseline (#69) vs Signal-aware (confidence-gated) version.

Runs BTC 15m 1-year backtest twice:
  - baseline: Signal(action, size, reason) only — no confidence gating
  - with_confidence: Signal.confidence < threshold → size scaled down

Writes results to docs/work/active/000076-signal-interface/sizing_comparison_with_confidence.json

Usage:
    python scripts/compare_momo_btc_v2_signal_interface.py \\
        [--data-dir lake/] [--start 2025-04-01] [--end 2026-04-01] \\
        [--out docs/work/active/000076-signal-interface/sizing_comparison_with_confidence.json]

If no real data is available, falls back to seeded synthetic OHLCV.
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
from backtest.protocol import Bar, Signal
from backtest.strategies.momo_btc_v2 import MomoBtcV2

DEFAULT_OUT = ROOT / "docs" / "work" / "active" / "000076-signal-interface" / "sizing_comparison_with_confidence.json"


def _synthetic_ohlcv(n: int = 35040, seed: int = 42) -> pd.DataFrame:
    """1 year of synthetic BTC 15m OHLCV (35040 bars = 365*96)."""
    rng = np.random.default_rng(seed)
    closes = 30_000.0 + np.cumsum(rng.standard_normal(n) * 50.0)
    closes = np.maximum(closes, 100.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2025-04-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def _load_real_ohlcv(data_dir: Path, start: str | None, end: str | None) -> pd.DataFrame | None:
    try:
        from backtest.bundle import load_ohlcv_from_parquet
        df = load_ohlcv_from_parquet(data_dir, symbol="BTCUSDT", freq="15m", start=start, end=end)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass
    return None


def _run_baseline(ohlcv: pd.DataFrame) -> dict[str, Any]:
    """Baseline: momo_btc_v2 without confidence gating."""
    strategy = MomoBtcV2(sizing_mode="full")
    config = BacktestConfig(initial_cash=10_000.0)
    result = run_backtest(ohlcv, strategy, config)
    m = result.metrics
    return {
        "mode": "baseline",
        "sharpe": float(m["sharpe"]),
        "mdd": float(m["mdd"]),
        "total_return": float(m["total_return"]),
        "trades": int(m["trades"]),
        "win_rate": float(m["win_rate"]),
        "final_equity": float(result.equity_curve.iloc[-1]),
    }


class MomoBtcV2WithConfidence(MomoBtcV2):
    """momo_btc_v2 variant that scales size by Signal.confidence."""

    CONFIDENCE_THRESHOLD: float = 0.3

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        sig = super().on_bar(bar, history, context)
        if sig.action == "buy" and sig.confidence is not None:
            # Scale size by confidence when below threshold
            if sig.confidence < self.CONFIDENCE_THRESHOLD:
                scaled_size = sig.size * (sig.confidence / self.CONFIDENCE_THRESHOLD)
                return Signal(
                    action=sig.action,
                    size=scaled_size,
                    reason=sig.reason,
                    expected_return=sig.expected_return,
                    win_probability=sig.win_probability,
                    confidence=sig.confidence,
                )
        return sig


def _run_with_confidence(ohlcv: pd.DataFrame) -> dict[str, Any]:
    """Signal-aware: scale position by confidence."""
    strategy = MomoBtcV2WithConfidence(sizing_mode="full")
    config = BacktestConfig(initial_cash=10_000.0)
    result = run_backtest(ohlcv, strategy, config)
    m = result.metrics
    return {
        "mode": "with_confidence",
        "sharpe": float(m["sharpe"]),
        "mdd": float(m["mdd"]),
        "total_return": float(m["total_return"]),
        "trades": int(m["trades"]),
        "win_rate": float(m["win_rate"]),
        "final_equity": float(result.equity_curve.iloc[-1]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare momo-btc-v2 baseline vs Signal-aware")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--start", default=None)
    parser.add_argument("--end", default=None)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    ohlcv = None
    if args.data_dir:
        ohlcv = _load_real_ohlcv(args.data_dir, args.start, args.end)
    if ohlcv is None:
        print("[compare] No real data found -- using synthetic 1y BTC 15m")
        ohlcv = _synthetic_ohlcv()

    print(f"[compare] Running baseline on {len(ohlcv)} bars …")
    baseline = _run_baseline(ohlcv)
    print(f"[compare]   baseline: win_rate={baseline['win_rate']:.3f}, sharpe={baseline['sharpe']:.3f}, trades={baseline['trades']}")

    print("[compare] Running with_confidence …")
    with_conf = _run_with_confidence(ohlcv)
    print(f"[compare]   with_conf: win_rate={with_conf['win_rate']:.3f}, sharpe={with_conf['sharpe']:.3f}, trades={with_conf['trades']}")

    # Detect regression: sharpe dropped > 10% relative
    reason_if_regressed = ""
    if baseline["sharpe"] > 0 and with_conf["sharpe"] < baseline["sharpe"] * 0.90:
        reason_if_regressed = (
            f"Sharpe regressed: baseline={baseline['sharpe']:.3f} → "
            f"with_confidence={with_conf['sharpe']:.3f} (>10% relative drop). "
            "Confidence scaling may be too aggressive at threshold=0.30."
        )

    output = {
        "win_rate_baseline": baseline["win_rate"],
        "win_rate_with_confidence": with_conf["win_rate"],
        "trade_count_baseline": baseline["trades"],
        "trade_count_with_confidence": with_conf["trades"],
        "sharpe_baseline": baseline["sharpe"],
        "sharpe_with_confidence": with_conf["sharpe"],
        "reason_if_regressed": reason_if_regressed,
        "_meta": {
            "baseline_mode": baseline["mode"],
            "confidence_mode": with_conf["mode"],
            "bars": len(ohlcv),
            "start": str(ohlcv.index[0]),
            "end": str(ohlcv.index[-1]),
        },
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[compare] Written to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
