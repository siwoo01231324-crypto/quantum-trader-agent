"""Train MetaLabeler on historical BTC data (Issue #85 · AC4 실데이터 검증).

Pipeline:
  1. Load OHLCV from lake/ohlcv/freq=15m/year=*/month=*/symbol=BTCUSDT/part-0.parquet
  2. Compute RSI (reuse src/signals/rsi.py detect_divergence)
  3. Scan bars for bullish divergences → build events DataFrame (entry_ts, side=+1, t1=entry+24 bars)
  4. Extract features at each event (rsi/atr/divergence_magnitude/bars_since_pivot/confidence/close/volume)
  5. Triple-barrier labeling (tp=2·σ, sl=1.5·σ, holding=24 bars, costs_bps=4)
  6. Train/holdout split (70/30 by time)
  7. PurgedKFold CV (n=5, embargo=0.01) on train set → report mean accuracy
  8. Fit MetaLabeler on train → save model + manifest with git SHA

Usage:
    python scripts/train_metalabeler_btc.py \
        --lake-dir lake/ \
        --symbol BTCUSDT --interval 15m \
        --holding-bars 24 --tp-sigma 2.0 --sl-sigma 1.5 --costs-bps 4.0 \
        --output-dir models/momo-btc-v2/

AC gate (CV): accuracy ≥ 0.55 (else WARN — may indicate insufficient data / feature set).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.retrain_pipeline import (  # noqa: E402
    load_ohlcv_from_lake,
    build_events_and_features,
    label_events,
    run_cv,
    train_and_save,
    SavedArtifact,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Train MetaLabeler on BTC historical data")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--holding-bars", type=int, default=24)
    parser.add_argument("--tp-sigma", type=float, default=2.0)
    parser.add_argument("--sl-sigma", type=float, default=1.5)
    parser.add_argument("--costs-bps", type=float, default=4.0)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--output-dir", type=Path, default=WORKTREE / "models/momo-btc-v2")
    args = parser.parse_args()

    ohlcv = load_ohlcv_from_lake(args.lake_dir, args.symbol, args.interval)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_dir / ts

    artifact, cv_report = train_and_save(
        strategy_id="momo-btc-v2",
        ohlcv=ohlcv,
        output_dir=out_dir,
        train_frac=args.train_frac,
        holding_bars=args.holding_bars,
        tp_sigma=args.tp_sigma,
        sl_sigma=args.sl_sigma,
        costs_bps=args.costs_bps,
    )

    if cv_report["mean_accuracy"] < 0.55:
        print(f"[WARN] CV mean accuracy {cv_report['mean_accuracy']:.4f} < 0.55 - metalabeler deployment not recommended")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
