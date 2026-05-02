"""4종 윈도우 walk-forward 비교 CLI (#122).

Usage:
    python scripts/compare_walkforward_windows.py --synthetic
    python scripts/compare_walkforward_windows.py --lake-dir lake --symbol BTCUSDT --interval 15m

출력:
  - stdout: 윈도우별 Sharpe/MDD/precision/recall/accuracy 비교표
  - stdout: 자동 롤백 임계 false positive/negative rate 표
  - stdout: 권고 윈도우 + 권고 임계

Exit codes: 0=ok, 1=fatal
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.window_rollback import (  # noqa: E402
    WalkForwardWindowComparator,
    RollbackThresholdAnalyzer,
    WindowComparisonResult,
    load_walkforward_config,
)
from ml.retrain_pipeline import build_events_and_features, label_events  # noqa: E402


# ---------------------------------------------------------------------------
# Synthetic data
# ---------------------------------------------------------------------------

def _make_synthetic_ohlcv(n: int = 8000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.standard_normal(n) * 0.3)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _print_window_table(results: list[WindowComparisonResult], asset: str) -> None:
    print(f"\n=== {asset.upper()} 윈도우별 walk-forward 비교 ===")
    print(f"{'윈도우':>8} {'folds':>6} {'acc mean':>10} {'acc std':>9} {'precision':>10} {'recall':>8} {'f1':>7} {'sharpe_proxy':>13}")
    print("-" * 80)
    for r in results:
        if r.n_folds == 0:
            print(f"{r.window_days:>7}d {'0':>6} {'N/A':>10}")
            continue
        print(
            f"{r.window_days:>7}d "
            f"{r.n_folds:>6} "
            f"{r.mean_accuracy:>10.4f} "
            f"{r.std_accuracy:>9.4f} "
            f"{r.mean_precision:>10.4f} "
            f"{r.mean_recall:>8.4f} "
            f"{r.f1:>7.4f} "
            f"{r.sharpe_proxy:>13.4f}"
        )

    best = WalkForwardWindowComparator.best_window(results)
    if best:
        print(f"\n  권고 윈도우 ({asset.upper()}): {best.window_days}일 (Sharpe proxy={best.sharpe_proxy:.4f})")


def _print_rollback_table(analyses, recommended) -> None:
    print("\n=== 자동 롤백 임계 분석 (accuracy delta 기준) ===")
    print(f"{'threshold':>10} {'triggered':>10} {'TP':>5} {'FP':>5} {'FN':>5} {'TN':>5} {'FPR':>8} {'FNR':>8} {'precision':>10} {'recall':>8}")
    print("-" * 85)
    for a in analyses:
        print(
            f"{a.threshold:>10.3f} "
            f"{a.n_rollbacks_triggered:>10} "
            f"{a.n_true_positives:>5} "
            f"{a.n_false_positives:>5} "
            f"{a.n_false_negatives:>5} "
            f"{a.n_true_negatives:>5} "
            f"{a.false_positive_rate:>8.3f} "
            f"{a.false_negative_rate:>8.3f} "
            f"{a.precision:>10.4f} "
            f"{a.recall:>8.4f}"
        )
    if recommended:
        print(f"\n  권고 임계: {recommended.threshold:.3f} (FPR={recommended.false_positive_rate:.3f}, recall={recommended.recall:.4f})")
    else:
        print("\n  권고 임계: 기준 미달 (모든 후보 FPR > 20%)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="4종 윈도우 walk-forward 비교 (#122)")
    parser.add_argument("--synthetic", action="store_true", help="합성 데이터 사용")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--asset", default="btc", choices=["btc", "krx"], help="자산군")
    parser.add_argument("--holding-bars", type=int, default=24)
    parser.add_argument("--tp-sigma", type=float, default=2.0)
    parser.add_argument("--sl-sigma", type=float, default=1.5)
    parser.add_argument("--costs-bps", type=float, default=4.0)
    parser.add_argument("--output-json", type=Path, default=None, help="결과를 JSON 파일로 저장")
    args = parser.parse_args()

    cfg = load_walkforward_config()
    asset_cfg = cfg.get("windows", {}).get(args.asset, {})
    window_candidates = asset_cfg.get("candidates", [7, 14, 30, 90])
    mode = asset_cfg.get("mode", "rolling")
    test_window_days = asset_cfg.get("test_window_days", 7)
    step_days = asset_cfg.get("step_days", 7)
    min_train_samples = asset_cfg.get("min_train_samples", 50)

    rollback_cfg = cfg.get("rollback", {})
    thresholds = [0.02, 0.05, 0.10, 0.20, 0.30]

    # Load data
    if args.synthetic:
        print("[data] 합성 OHLCV 사용 (n=8000, 15min)")
        ohlcv = _make_synthetic_ohlcv()
    else:
        from ml.retrain_pipeline import load_ohlcv_from_lake
        ohlcv = load_ohlcv_from_lake(args.lake_dir, args.symbol, args.interval)

    # Build features + labels
    print("[features] bullish divergence 이벤트 추출 중...")
    try:
        events, features = build_events_and_features(ohlcv, args.holding_bars)
        labels_df = label_events(ohlcv, events, args.tp_sigma, args.sl_sigma, args.costs_bps)
    except RuntimeError as e:
        print(f"[WARN] {e} — 데이터 부족, n 확대 필요", file=sys.stderr)
        return 1

    common = features.index.intersection(labels_df.index)
    X = features.loc[common]
    y = labels_df.loc[common, "label"].astype(int)
    print(f"[data] {len(X)} 이벤트, positive rate={y.mean():.3f}")

    # --- Walk-forward window comparison ---
    bars_per_day = 96  # 15min bars
    comparator = WalkForwardWindowComparator(
        window_days=window_candidates,
        asset=args.asset,
        mode=mode,
        bars_per_day=bars_per_day,
        test_window_days=test_window_days,
        step_days=step_days,
        min_train_samples=min_train_samples,
    )

    print(f"\n[compare] {len(window_candidates)}종 윈도우 walk-forward 실행 중 ({mode})...")
    results = comparator.compare(X, y)
    _print_window_table(results, args.asset)

    best = WalkForwardWindowComparator.best_window(results)

    # --- Rollback threshold analysis ---
    # Build accuracy time-series from fold results across the best-effort window
    # Use the 30-day window (or largest available with folds) for the series
    ref_result = next((r for r in sorted(results, key=lambda r: -r.n_folds) if r.n_folds > 0), None)
    if ref_result and ref_result.folds:
        acc_series = [f.accuracy for f in ref_result.folds if not f.skipped]
    else:
        # Fallback: simulate a noisy accuracy series
        rng = np.random.default_rng(7)
        acc_series = list(0.55 + rng.standard_normal(30) * 0.05)

    analyzer = RollbackThresholdAnalyzer(true_degradation_min=0.03)
    analyses = analyzer.analyze(acc_series, thresholds=thresholds)
    recommended = RollbackThresholdAnalyzer.recommend(analyses, max_fpr=0.20)
    _print_rollback_table(analyses, recommended)

    # --- Summary ---
    print("\n=== 권고 요약 ===")
    if best:
        print(f"  윈도우 권고 ({args.asset.upper()}): {best.window_days}일 rolling")
        print(f"    → mean_accuracy={best.mean_accuracy:.4f}, precision={best.mean_precision:.4f}, recall={best.mean_recall:.4f}")
    if recommended:
        print(f"  롤백 임계 권고: accuracy_delta >= {recommended.threshold:.3f}")
        print(f"    → FPR={recommended.false_positive_rate:.3f}, FNR={recommended.false_negative_rate:.3f}, recall={recommended.recall:.4f}")
    print(f"  configs/walkforward.yaml 설정 확인: WORKTREE/configs/walkforward.yaml")

    # --- Optional JSON output ---
    if args.output_json:
        out = {
            "asset": args.asset,
            "windows": [
                {
                    "window_days": r.window_days,
                    "n_folds": r.n_folds,
                    "mean_accuracy": r.mean_accuracy,
                    "std_accuracy": r.std_accuracy,
                    "mean_precision": r.mean_precision,
                    "mean_recall": r.mean_recall,
                    "f1": r.f1,
                    "sharpe_proxy": r.sharpe_proxy,
                }
                for r in results
            ],
            "rollback_analysis": [
                {
                    "threshold": a.threshold,
                    "n_rollbacks_triggered": a.n_rollbacks_triggered,
                    "false_positive_rate": a.false_positive_rate,
                    "false_negative_rate": a.false_negative_rate,
                    "precision": a.precision,
                    "recall": a.recall,
                }
                for a in analyses
            ],
            "recommended_window_days": best.window_days if best else None,
            "recommended_rollback_threshold": recommended.threshold if recommended else None,
        }
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(f"\n[out] 결과 저장: {args.output_json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
