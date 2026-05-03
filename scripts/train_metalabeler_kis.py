"""Train MetaLabeler on KIS KRX historical data (Issue #97 · Phase A).

Pipeline:
  1. Load OHLCV from lake/ohlcv/freq=15m/year=*/month=*/symbol=005930/
     (falls back to synthetic data with warning if lake is missing)
  2. Build bullish-divergence events + features (RSI/ATR/confidence/...)
  3. Triple-barrier labeling (tp=2·σ, sl=1.5·σ, holding=26 bars, costs_bps=26)
  4. PurgedKFold CV (n=5, embargo=0.01) on 70% train split
  5. Fit MetaLabeler on train → save model + manifest with git SHA

KRX-specific parameters vs BTC:
  costs_bps = 26.0  (BUY 0.015% + SELL 0.245% ≈ 26 bps, not 4.0)
  holding_bars = 26  (≈ 1 trading day, not 24)
  periods_per_year = 6552  (26 bars/day × 252 days, not 35040)

Exit codes:
  0 → success (CV accuracy ≥ 0.55 or warning issued)
  2 → CV accuracy < 0.55 (deployment not recommended)
  3 → no bullish divergence events found in data

Usage:
    python scripts/train_metalabeler_kis.py \\
        --lake-dir lake/ \\
        --symbol 005930 --interval 15m \\
        --holding-bars 26 --tp-sigma 2.0 --sl-sigma 1.5 --costs-bps 26.0 \\
        --output-dir models/momo-kis-v1/
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.pipelines.kis_cross_validation import run_kis_pipeline, run_kis_pipeline_pooled  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Train MetaLabeler on KIS KRX historical data")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="005930", help="KRX stock code (6 digits)")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--holding-bars", type=int, default=26)
    parser.add_argument("--tp-sigma", type=float, default=2.0)
    parser.add_argument("--sl-sigma", type=float, default=1.5)
    parser.add_argument("--costs-bps", type=float, default=26.0)
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--output-dir", type=Path, default=WORKTREE / "models/momo-kis-v1")
    parser.add_argument("--multi-symbol", action="store_true", help="Pool N symbols from KOSPI200 universe")
    parser.add_argument("--n-symbols", type=int, default=30, help="Pool size when --multi-symbol is set")
    parser.add_argument("--seed", type=int, default=42, help="Seed for pool sampling")
    args = parser.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_dir / ts

    if args.multi_symbol:
        from universe.krx_pool import get_pool_codes  # noqa: PLC0415
        symbols = get_pool_codes(args.n_symbols, seed=args.seed)
        print(f"[train] multi-symbol mode: {len(symbols)} symbols, interval={args.interval}, "
              f"holding_bars={args.holding_bars}")
        artifact, report = run_kis_pipeline_pooled(
            symbols=symbols,
            lake_dir=args.lake_dir,
            output_dir=out_dir,
            interval=args.interval,
            holding_bars=args.holding_bars,
            costs_bps=args.costs_bps,
            tp_sigma=args.tp_sigma,
            sl_sigma=args.sl_sigma,
            train_frac=args.train_frac,
        )
    else:
        artifact, report = run_kis_pipeline(
            lake_dir=args.lake_dir,
            output_dir=out_dir,
            holding_bars=args.holding_bars,
            costs_bps=args.costs_bps,
            tp_sigma=args.tp_sigma,
            sl_sigma=args.sl_sigma,
            train_frac=args.train_frac,
            symbol=args.symbol,
            interval=args.interval,
        )

    if report.get("exit_code") == 3:
        print("[ERROR] No bullish divergence events found — check data window or RSI parameters.", file=sys.stderr)
        return 3

    cv = report.get("cv", {})
    mean_acc = cv.get("mean_accuracy", 0.0)

    if mean_acc < 0.55:
        print(
            f"[WARN] CV mean accuracy {mean_acc:.4f} < 0.55 — "
            "momo-kis-v1 MetaLabeler deployment not recommended."
        )
        return 2

    print(f"[OK] Training complete. CV mean accuracy: {mean_acc:.4f}")
    if artifact:
        print(f"     Artifacts: {artifact.output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
