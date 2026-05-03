"""Benchmark: momo-kis-v1 MetaLabeler CV / PR-AUC / DSR (Issue #97, Phase A).

Scope: CV cross-validation metrics only — NO equity-curve backtest.
Outputs JSON summary to stdout.

KRX-specific constants:
  costs_bps    = 26.0   (BUY 0.015% + SELL 0.245% ≈ 26 bps)
  holding_bars = 26     (≈ 1 KRX trading day at 15m)
  periods_per_year = 6552  (26 bars/day × 252 trading days)

Synthetic OHLCV fallback (when lake missing):
  GBM mu=0, sigma=0.01, n=2000 bars
  KRX trading hours weekdays 09:00-15:30 KST at 15m intervals
  seed=42 (deterministic)

Usage:
    python scripts/bench_metalabeler_kis.py
    python scripts/bench_metalabeler_kis.py --lake-dir lake/ --symbol 005930
    python scripts/bench_metalabeler_kis.py --output-json results/bench_kis.json
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

from ml.pipelines.kis_cross_validation import (  # noqa: E402
    _make_synthetic_ohlcv,
    KRX_PERIODS_PER_YEAR,
    run_kis_pipeline_pooled,
)
from ml.retrain_pipeline import (  # noqa: E402
    load_ohlcv_from_lake,
    build_events_and_features,
    label_events,
    run_cv_extended,
)
from ml.scoring import annualized_sharpe, deflated_sharpe_ratio, pr_auc_score  # noqa: E402
from ml.reporting.cross_asset_compare import compute_effective_n  # noqa: E402


ASSET_ID = "krx-005930"
STRATEGY_ID = "momo-kis-v1"
COSTS_BPS = 26.0
HOLDING_BARS = 26
TP_SIGMA = 2.0
SL_SIGMA = 1.5
TRAIN_FRAC = 0.7

KRX_PERIODS_PER_YEAR_1M = 98280  # 390 bars/day × 252 trading days


def _load_ohlcv(lake_dir: Path, symbol: str, interval: str) -> tuple[pd.DataFrame, str]:
    """Load OHLCV from lake or fall back to synthetic."""
    try:
        ohlcv = load_ohlcv_from_lake(lake_dir, symbol, interval)
        return ohlcv, "lake"
    except FileNotFoundError:
        print(
            f"[WARN] Lake data not found for {symbol}@{interval} in {lake_dir}. "
            "Using synthetic OHLCV (seed=42, n=2000 bars, GBM mu=0 sigma=0.01).",
            file=sys.stderr,
        )
        return _make_synthetic_ohlcv(n=2000, seed=42), "synthetic"


def _triple_barrier_returns(
    ohlcv: pd.DataFrame,
    events: pd.DataFrame,
    labels_df: pd.DataFrame,
) -> pd.Series:
    """Compute per-event log-returns for the triple-barrier holding window."""
    close = ohlcv["close"]
    rets = []
    for entry_ts, row in events.iterrows():
        if entry_ts not in labels_df.index:
            continue
        t_exit = labels_df.loc[entry_ts, "t_touch"]
        if pd.isna(t_exit):
            continue
        try:
            p_entry = close.loc[entry_ts]
            p_exit = close.asof(t_exit) if hasattr(close, "asof") else close.loc[t_exit]
            rets.append(np.log(p_exit / p_entry) if p_entry > 0 else 0.0)
        except (KeyError, ZeroDivisionError):
            pass
    return pd.Series(rets, dtype=float)


def _run_multi_symbol_bench(args: argparse.Namespace) -> int:
    """Multi-symbol pooled benchmark — runs run_kis_pipeline_pooled and reports metrics."""
    import tempfile
    from universe.krx_pool import get_pool_codes  # noqa: PLC0415

    symbols = get_pool_codes(args.n_symbols, seed=args.seed)
    print(f"[bench] multi-symbol mode: {len(symbols)} symbols, interval={args.interval}, "
          f"holding_bars={args.holding_bars}", file=sys.stderr)

    holding_bars = args.holding_bars if args.holding_bars != 26 else 78  # default for 1m
    if args.interval == "15m" and args.holding_bars == 26:
        holding_bars = 26

    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            artifact, report = run_kis_pipeline_pooled(
                symbols=symbols,
                lake_dir=args.lake_dir,
                output_dir=Path(tmp_dir) / "model",
                interval=args.interval,
                holding_bars=holding_bars,
                costs_bps=COSTS_BPS,
            )
        except ValueError as exc:
            print(f"[ERROR] {exc}", file=sys.stderr)
            return 4

    if report.get("exit_code") == 3:
        print("[ERROR] No events after pooling — check lake data.", file=sys.stderr)
        return 3

    n_symbols = report.get("n_symbols", len(symbols))
    rho_avg = report.get("rho_avg", 0.0)
    n_eff = report.get("n_eff", compute_effective_n(n_symbols, rho_avg))
    cv = report.get("cv", {})
    asset_id = f"krx-pool-{n_symbols}"

    periods = KRX_PERIODS_PER_YEAR_1M if args.interval == "1m" else KRX_PERIODS_PER_YEAR

    summary = {
        "asset_id": asset_id,
        "strategy_id": "momo-kis-v1-pooled",
        "n_symbols": n_symbols,
        "n_eff": round(n_eff, 4),
        "rho_avg": round(rho_avg, 6),
        "interval": args.interval,
        "holding_bars": holding_bars,
        "costs_bps": COSTS_BPS,
        "periods_per_year": periods,
        "cv_mean_accuracy": round(cv.get("mean_accuracy", 0.0), 6),
        "positive_rate": round(report.get("positive_rate", 0.0), 6),
    }

    out_str = json.dumps(summary, indent=2)
    print(out_str)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(out_str, encoding="utf-8")
        print(f"[bench] JSON written to {args.output_json}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark momo-kis-v1 MetaLabeler (CV/PR-AUC/DSR)")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--multi-symbol", action="store_true", help="Pooled multi-symbol benchmark")
    parser.add_argument("--n-symbols", type=int, default=30, help="Pool size when --multi-symbol is set")
    parser.add_argument("--holding-bars", type=int, default=26)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    if args.multi_symbol:
        return _run_multi_symbol_bench(args)

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    ohlcv, data_source = _load_ohlcv(args.lake_dir, args.symbol, args.interval)

    # ------------------------------------------------------------------
    # 2. Events + features + labels
    # ------------------------------------------------------------------
    try:
        events, features = build_events_and_features(ohlcv, HOLDING_BARS)
    except RuntimeError as exc:
        if "No bullish divergence events found" in str(exc):
            print("[ERROR] No bullish divergence events — insufficient data or flat RSI.", file=sys.stderr)
            return 3
        raise

    labels_df = label_events(ohlcv, events, TP_SIGMA, SL_SIGMA, COSTS_BPS)

    common = features.index.intersection(labels_df.index)
    X = features.loc[common]
    y = labels_df.loc[common, "label"].astype(int)
    t1 = labels_df.loc[common, "t_touch"]

    n = len(X)
    split = int(n * TRAIN_FRAC)
    X_tr = X.iloc[:split]
    y_tr = y.iloc[:split]
    t1_tr = t1.iloc[:split]

    print(f"[bench] n_events={n}, train={split}, holdout={n - split}, data_source={data_source}")

    # ------------------------------------------------------------------
    # 3. Extended CV on train split
    # ------------------------------------------------------------------
    cv_result = run_cv_extended(X_tr, y_tr, t1_tr, n_splits=5, embargo=0.01)

    # Aggregate fold-level probabilities for global PR-AUC
    y_true_all: list[np.ndarray] = []
    y_prob_all: list[np.ndarray] = []
    fold_pr_aucs: list[float] = []

    for fold in cv_result["folds"]:
        if fold.get("skipped"):
            continue
        y_true_all.append(fold["y_true"])
        y_prob_all.append(fold["y_prob"])
        fold_pr_aucs.append(fold["pr_auc"])

    if y_true_all:
        y_true_concat = np.concatenate(y_true_all)
        y_prob_concat = np.concatenate(y_prob_all)
        pr_auc_global = pr_auc_score(y_true_concat, y_prob_concat)
    else:
        pr_auc_global = 0.0

    mean_acc = cv_result["mean_accuracy"]
    mean_pr_auc = cv_result["mean_pr_auc"]

    # ------------------------------------------------------------------
    # 4. Triple-barrier returns → Sharpe (off = no filter, on = label=1 only)
    # ------------------------------------------------------------------
    # "off" = all events (no meta-labeler filter)
    tb_returns_off = _triple_barrier_returns(ohlcv, events.loc[common], labels_df.loc[common])

    # "on" = only events where label == 1 (positive triple-barrier outcome)
    positive_idx = y[y == 1].index
    tb_returns_on = _triple_barrier_returns(ohlcv, events.loc[positive_idx], labels_df.loc[positive_idx])

    sr_off = annualized_sharpe(tb_returns_off, periods_per_year=KRX_PERIODS_PER_YEAR)
    sr_on = annualized_sharpe(tb_returns_on, periods_per_year=KRX_PERIODS_PER_YEAR)

    # ------------------------------------------------------------------
    # 5. DSR (n_trials=1 — deflation negligible at n_trials=1)
    # ------------------------------------------------------------------
    sr_pool = [sr_off] if sr_off != 0.0 else [0.0]
    dsr_off = deflated_sharpe_ratio(sr_off, sr_estimates=sr_pool, n_trials=1)
    dsr_on = deflated_sharpe_ratio(sr_on, sr_estimates=sr_pool, n_trials=1)

    # ------------------------------------------------------------------
    # 6. MDD (from cumulative triple-barrier equity)
    # ------------------------------------------------------------------
    def _equity_mdd(returns: pd.Series) -> float:
        if len(returns) == 0:
            return 0.0
        equity = (1.0 + returns).cumprod()
        peak = equity.cummax()
        dd = (equity - peak) / peak.replace(0, np.nan)
        return float(-dd.min()) if len(dd) > 0 else 0.0

    mdd_off = _equity_mdd(tb_returns_off)
    mdd_on = _equity_mdd(tb_returns_on)

    # ------------------------------------------------------------------
    # 7. Print JSON summary
    # ------------------------------------------------------------------
    summary = {
        "asset_id": ASSET_ID,
        "strategy_id": STRATEGY_ID,
        "data_source": data_source,
        "n_events": n,
        "n_train": split,
        "n_holdout": n - split,
        "costs_bps": COSTS_BPS,
        "holding_bars": HOLDING_BARS,
        "periods_per_year": KRX_PERIODS_PER_YEAR,
        "cv_mean_accuracy": round(mean_acc, 6),
        "cv_mean_pr_auc": round(mean_pr_auc, 6),
        "pr_auc_global": round(pr_auc_global, 6),
        "sr_off": round(sr_off, 6),
        "sr_on": round(sr_on, 6),
        "mdd_off": round(mdd_off, 6),
        "mdd_on": round(mdd_on, 6),
        "dsr_off": round(dsr_off, 6),
        "dsr_on": round(dsr_on, 6),
        "note_dsr": "deflation negligible at n_trials=1",
    }

    out_str = json.dumps(summary, indent=2)
    print(out_str)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(out_str, encoding="utf-8")
        print(f"[bench] JSON written to {args.output_json}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
