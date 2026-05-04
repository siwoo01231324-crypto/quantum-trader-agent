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
DSR_PASS_THRESHOLD = 0.3
N_EFF_MIN = 5.0


def _sortino(returns: pd.Series, periods_per_year: int) -> float:
    """Annualized Sortino ratio. 0.0 when empty / no downside / zero std."""
    if len(returns) == 0:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) == 0:
        return 0.0
    d_std = float(downside.std(ddof=1))
    if d_std == 0.0 or np.isnan(d_std):
        return 0.0
    return float(returns.mean()) / d_std * float(np.sqrt(periods_per_year))


def _resolve_periods_per_year(interval: str, explicit: int | None) -> int:
    """CLI 의 --periods-per-year 가 명시되면 그대로, 아니면 interval 기반 자동."""
    if explicit is not None:
        return int(explicit)
    if interval == "1m":
        return KRX_PERIODS_PER_YEAR_1M
    # 15m / 미지원 interval 모두 KRX_PERIODS_PER_YEAR (6552) 로 폴백
    return KRX_PERIODS_PER_YEAR


def _compute_verdict(dsr_on: float, n_eff: float) -> str:
    """DSR 임계 + n_eff 기반 자동 판정.

    n_eff 가 우선 게이트: < 5 면 DSR 무관 HOLD.
    """
    if n_eff < N_EFF_MIN:
        return f"HOLD (n_eff<{N_EFF_MIN:g})"
    if dsr_on >= DSR_PASS_THRESHOLD:
        return f"PASS (dsr_on>={DSR_PASS_THRESHOLD})"
    return f"HOLD (dsr_on<{DSR_PASS_THRESHOLD})"


def _oof_filter(oof: pd.Series, threshold: float) -> pd.Index:
    """OOF win_probability ≥ threshold 인 event index 반환."""
    if len(oof) == 0:
        return oof.index[:0]
    return oof[oof >= threshold].index


def _build_oof_series(cv_result: dict) -> pd.Series:
    """fold dict 들의 test_event_idx + y_prob 을 단일 pandas.Series 로 결합."""
    idx_all: list = []
    prob_all: list = []
    for fold in cv_result.get("folds", []):
        if fold.get("skipped"):
            continue
        ev_idx = fold.get("test_event_idx")
        prob = fold.get("y_prob")
        if ev_idx is None or prob is None:
            continue
        idx_all.extend(list(ev_idx))
        prob_all.extend(list(prob))
    if not idx_all:
        return pd.Series([], dtype=float)
    return pd.Series(prob_all, index=idx_all, dtype=float)


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


def _pooled_triple_barrier_returns(
    events_with_sym: pd.DataFrame,
    labels_df: pd.DataFrame,
    ohlcv_per_sym: dict[str, pd.DataFrame],
) -> pd.Series:
    """Multi-symbol triple-barrier log-returns (positional pairing).

    events_with_sym 의 'symbol' 칼럼 + 종목별 close 시계열로 entry→t_touch log return 계산.
    multi-symbol pool 에서 timestamp 중복이 가능하므로 events/labels 를 positional pair 로 처리.
    """
    rets: list[float] = []
    n = min(len(events_with_sym), len(labels_df))
    if n == 0:
        return pd.Series([], dtype=float)
    ev_iter = events_with_sym.iloc[:n]
    lb_iter = labels_df.iloc[:n]
    for (entry_ts, ev_row), (_, lb_row) in zip(ev_iter.iterrows(), lb_iter.iterrows()):
        sym = ev_row.get("symbol")
        if sym is None or sym not in ohlcv_per_sym:
            continue
        t_exit = lb_row.get("t_touch")
        if t_exit is None or pd.isna(t_exit):
            continue
        close = ohlcv_per_sym[sym]["close"]
        try:
            p_entry = float(close.loc[entry_ts]) if entry_ts in close.index else float(close.asof(entry_ts))
            p_exit = float(close.asof(t_exit)) if hasattr(close, "asof") else float(close.loc[t_exit])
            if p_entry > 0 and not np.isnan(p_entry) and not np.isnan(p_exit):
                rets.append(np.log(p_exit / p_entry))
        except (KeyError, ZeroDivisionError, ValueError):
            pass
    return pd.Series(rets, dtype=float)


def _run_multi_symbol_bench(args: argparse.Namespace) -> int:
    """Multi-symbol pooled benchmark — equity-curve Sharpe/Sortino/MDD/DSR + verdict (#154)."""
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
    extras = report.get("_extras") or {}
    asset_id = f"krx-pool-{n_symbols}"

    periods = _resolve_periods_per_year(args.interval, args.periods_per_year)

    # Equity-curve metrics (#154)
    events_concat: pd.DataFrame = extras.get("events_concat", pd.DataFrame())
    labels_concat: pd.DataFrame = extras.get("labels_concat", pd.DataFrame())
    ohlcv_per_sym: dict = extras.get("ohlcv_per_symbol", {})
    oof_prob_pos: pd.Series = extras.get("oof_prob_pos", pd.Series([], dtype=float))
    train_split: int = extras.get("train_split", 0)

    if events_concat.empty or train_split == 0:
        sr_off = sr_on = sortino_off = sortino_on = 0.0
        mdd_off = mdd_on = dsr_off = dsr_on = dsr_delta = 0.0
        n_events_off = n_events_on = 0
    else:
        # OFF = train-split events all (no filter)
        events_off = events_concat.iloc[:train_split]
        labels_off = labels_concat.iloc[:train_split]
        tb_off = _pooled_triple_barrier_returns(events_off, labels_off, ohlcv_per_sym)

        # ON = OOF prob >= threshold (positional pairing)
        on_pos = list(oof_prob_pos[oof_prob_pos >= args.metalabeler_threshold].index)
        if on_pos:
            events_on = events_off.iloc[on_pos]
            labels_on = labels_off.iloc[on_pos]
        else:
            events_on = events_off.iloc[:0]
            labels_on = labels_off.iloc[:0]
        tb_on = _pooled_triple_barrier_returns(events_on, labels_on, ohlcv_per_sym)

        sr_off = annualized_sharpe(tb_off, periods_per_year=periods)
        sr_on = annualized_sharpe(tb_on, periods_per_year=periods)
        sortino_off = _sortino(tb_off, periods_per_year=periods)
        sortino_on = _sortino(tb_on, periods_per_year=periods)

        def _mdd(rets: pd.Series) -> float:
            if len(rets) == 0:
                return 0.0
            equity = (1.0 + rets).cumprod()
            peak = equity.cummax()
            dd = (equity - peak) / peak.replace(0, np.nan)
            return float(-dd.min()) if len(dd) > 0 else 0.0

        mdd_off = _mdd(tb_off)
        mdd_on = _mdd(tb_on)

        sr_pool = [sr_off] if sr_off != 0.0 else [0.0]
        # n_trials = number of meta-labeler decisions evaluated. Use OOF folds.
        n_trials = max(int(cv.get("n_folds", 1)), 1)
        dsr_off = deflated_sharpe_ratio(sr_off, sr_estimates=sr_pool, n_trials=n_trials)
        dsr_on = deflated_sharpe_ratio(sr_on, sr_estimates=sr_pool, n_trials=n_trials)
        dsr_delta = dsr_on - dsr_off

        n_events_off = int(len(events_off))
        n_events_on = int(len(events_on))

    verdict = _compute_verdict(dsr_on=dsr_on, n_eff=float(n_eff))

    summary = {
        "asset_id": asset_id,
        "strategy_id": "momo-kis-v1-pooled",
        "n_symbols": n_symbols,
        "n_eff": round(float(n_eff), 4),
        "rho_avg": round(rho_avg, 6),
        "interval": args.interval,
        "holding_bars": holding_bars,
        "costs_bps": COSTS_BPS,
        "periods_per_year": periods,
        "metalabeler_threshold": args.metalabeler_threshold,
        "cv_mean_accuracy": round(cv.get("mean_accuracy", 0.0), 6),
        "positive_rate": round(report.get("positive_rate", 0.0), 6),
        "n_events_off": n_events_off,
        "n_events_on": n_events_on,
        "sr_off": round(sr_off, 6),
        "sr_on": round(sr_on, 6),
        "sharpe_off": round(sr_off, 6),
        "sharpe_on": round(sr_on, 6),
        "sortino_off": round(sortino_off, 6),
        "sortino_on": round(sortino_on, 6),
        "mdd_off": round(mdd_off, 6),
        "mdd_on": round(mdd_on, 6),
        "dsr_off": round(dsr_off, 6),
        "dsr_on": round(dsr_on, 6),
        "dsr_delta": round(dsr_delta, 6),
        "verdict": verdict,
    }

    out_str = json.dumps(summary, indent=2, ensure_ascii=False)
    print(out_str)
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(out_str, encoding="utf-8")
        print(f"[bench] JSON written to {args.output_json}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Benchmark momo-kis-v1 MetaLabeler (CV/PR-AUC/DSR)")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="005930")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--multi-symbol", action="store_true", help="Pooled multi-symbol benchmark")
    parser.add_argument("--n-symbols", type=int, default=30, help="Pool size when --multi-symbol is set")
    parser.add_argument("--holding-bars", type=int, default=26)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--metalabeler-threshold", type=float, default=0.5,
        help="ON 경로 OOF win_probability 임계치 (default 0.5)",
    )
    parser.add_argument(
        "--periods-per-year", type=int, default=None,
        help="명시 시 우선. None 이면 interval 기반 자동 (1m=98280, 15m=6552)",
    )
    args = parser.parse_args(argv)

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
    # 4. Triple-barrier returns → Sharpe / Sortino
    #    OFF = 모든 RSI bullish divergence 신호
    #    ON  = OOF win_probability ≥ threshold (#154)
    # ------------------------------------------------------------------
    periods_per_year = _resolve_periods_per_year(args.interval, args.periods_per_year)

    tb_returns_off = _triple_barrier_returns(ohlcv, events.loc[common], labels_df.loc[common])

    oof_series = _build_oof_series(cv_result)
    on_idx = _oof_filter(oof_series, threshold=args.metalabeler_threshold)
    on_idx_in_common = [i for i in on_idx if i in common]
    if on_idx_in_common:
        tb_returns_on = _triple_barrier_returns(
            ohlcv, events.loc[on_idx_in_common], labels_df.loc[on_idx_in_common]
        )
    else:
        tb_returns_on = pd.Series([], dtype=float)

    sr_off = annualized_sharpe(tb_returns_off, periods_per_year=periods_per_year)
    sr_on = annualized_sharpe(tb_returns_on, periods_per_year=periods_per_year)
    sortino_off = _sortino(tb_returns_off, periods_per_year=periods_per_year)
    sortino_on = _sortino(tb_returns_on, periods_per_year=periods_per_year)

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
    dsr_delta = dsr_on - dsr_off
    n_eff_single = 1.0  # 단일 종목 모드 — multi-symbol 은 _run_multi_symbol_bench 후속 이슈
    verdict = _compute_verdict(dsr_on=dsr_on, n_eff=n_eff_single)

    summary = {
        "asset_id": ASSET_ID,
        "strategy_id": STRATEGY_ID,
        "data_source": data_source,
        "n_events": n,
        "n_events_off": int(len(events.loc[common])),
        "n_events_on": int(len(on_idx_in_common)),
        "n_train": split,
        "n_holdout": n - split,
        "costs_bps": COSTS_BPS,
        "holding_bars": HOLDING_BARS,
        "periods_per_year": periods_per_year,
        "metalabeler_threshold": args.metalabeler_threshold,
        "cv_mean_accuracy": round(mean_acc, 6),
        "cv_mean_pr_auc": round(mean_pr_auc, 6),
        "pr_auc_global": round(pr_auc_global, 6),
        "sr_off": round(sr_off, 6),
        "sr_on": round(sr_on, 6),
        "sharpe_off": round(sr_off, 6),  # alias for #154 AC
        "sharpe_on": round(sr_on, 6),
        "sortino_off": round(sortino_off, 6),
        "sortino_on": round(sortino_on, 6),
        "mdd_off": round(mdd_off, 6),
        "mdd_on": round(mdd_on, 6),
        "dsr_off": round(dsr_off, 6),
        "dsr_on": round(dsr_on, 6),
        "dsr_delta": round(dsr_delta, 6),
        "n_eff": n_eff_single,
        "verdict": verdict,
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
