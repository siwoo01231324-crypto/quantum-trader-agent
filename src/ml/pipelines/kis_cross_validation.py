"""KIS KRX cross-validation pipeline for momo-kis-v1 MetaLabeler.

KRX-specific constants:
  costs_bps = 26.0  (BUY 0.015% + SELL 0.245% ≈ 26 bps round-trip)
  holding_bars = 26  (≈ 1 trading day at KRX 15m: 09:00-15:30 = 26 bars)
  periods_per_year = 6552  (26 bars/day × 252 trading days)
"""
from __future__ import annotations

import json
import logging
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

_WORKTREE = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_WORKTREE / "src"))

from ml.retrain_pipeline import (  # noqa: E402
    load_ohlcv_from_lake,
    train_and_save,
    SavedArtifact,
    build_events_and_features,
    label_events,
)
from ml.cv import TimeBlockGroupKFold, PurgedKFold  # noqa: E402
from ml.meta_labeler import MetaLabeler  # noqa: E402

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

KRX_PERIODS_PER_YEAR = 6552  # 26 bars/day × 252 trading days


def _make_synthetic_ohlcv(n: int = 2000, seed: int = 42) -> pd.DataFrame:
    """GBM synthetic OHLCV with KRX 15m trading hours index."""
    rng = np.random.default_rng(seed)
    log_rets = rng.normal(0, 0.01, size=n)
    closes = 50_000.0 * np.exp(np.cumsum(log_rets))
    closes = np.maximum(closes, 1.0)
    opens = closes * np.exp(rng.normal(0, 0.001, size=n))
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.normal(0, 0.002, size=n)))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.normal(0, 0.002, size=n)))
    volumes = np.abs(rng.normal(5_000_000, 1_000_000, size=n))

    # KRX trading hours: weekdays 09:00-15:30 KST at 15-min intervals
    index = pd.bdate_range(
        start="2024-01-02 09:00:00",
        periods=n,
        freq="15min",
        tz="Asia/Seoul",
    ).tz_convert("UTC")

    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


def run_kis_pipeline(
    lake_dir: Path,
    output_dir: Path,
    *,
    holding_bars: int = 26,
    costs_bps: float = 26.0,
    tp_sigma: float = 2.0,
    sl_sigma: float = 1.5,
    train_frac: float = 0.7,
    symbol: str = "005930",
    interval: str = "15m",
) -> tuple[SavedArtifact | None, dict]:
    """Run the KIS KRX MetaLabeler training pipeline.

    Parameters
    ----------
    lake_dir:
        Root of the hive-partitioned data lake. If no data exists for
        the symbol/interval, a synthetic OHLCV series (seed=42) is used
        with an explicit warning.
    output_dir:
        Directory where model artifacts (manifest.json, model, cv_report)
        will be written.
    holding_bars:
        Triple-barrier holding period in bars. Default 26 (≈ 1 KRX trading day).
    costs_bps:
        Round-trip transaction costs in basis points. Default 26.0
        (KRX: BUY 0.015% + SELL 0.245%).
    tp_sigma, sl_sigma:
        Take-profit / stop-loss multipliers of rolling-window σ.
    train_frac:
        Fraction of events used for training (rest is holdout).
    symbol:
        KRX stock code. Default "005930" (Samsung Electronics).
    interval:
        Bar interval. Default "15m".

    Returns
    -------
    (SavedArtifact, report_dict)
        On success: SavedArtifact is populated; report_dict contains
        at minimum {"status": "ok", "costs_bps": ..., "holding_bars": ...}.
        On no-events: returns (None, {"status": "no_events", "exit_code": 3}).
    """
    lake_dir = Path(lake_dir)
    output_dir = Path(output_dir)

    # ------------------------------------------------------------------
    # 1. Load OHLCV — fall back to synthetic if lake is missing
    # ------------------------------------------------------------------
    try:
        ohlcv = load_ohlcv_from_lake(lake_dir, symbol, interval)
    except FileNotFoundError:
        warnings.warn(
            f"[kis_pipeline] Lake data not found for {symbol}@{interval} in {lake_dir}. "
            "Using synthetic OHLCV (seed=42, n=2000 bars, GBM mu=0, sigma=0.01). "
            "Results are for structural validation only — not tradeable.",
            stacklevel=2,
        )
        ohlcv = _make_synthetic_ohlcv(n=2000, seed=42)

    # ------------------------------------------------------------------
    # 2. Quick positive-rate pre-check (informational)
    # ------------------------------------------------------------------
    from ml.retrain_pipeline import build_events_and_features, label_events  # noqa: PLC0415

    try:
        events, features = build_events_and_features(ohlcv, holding_bars)
    except RuntimeError as exc:
        if "No bullish divergence events found" in str(exc):
            log.warning("[kis_pipeline] No bullish divergence events — returning exit_code=3.")
            return None, {"status": "no_events", "exit_code": 3}
        raise

    labels_df = label_events(ohlcv, events, tp_sigma, sl_sigma, costs_bps)

    common = features.index.intersection(labels_df.index)
    y = labels_df.loc[common, "label"].astype(int)
    pos_rate = float(y.mean()) if len(y) > 0 else 0.0
    if pos_rate < 0.2:
        log.warning(
            "[kis_pipeline] Positive label rate %.4f < 0.20 — "
            "triple-barrier threshold may be too strict for KRX volatility.",
            pos_rate,
        )

    # ------------------------------------------------------------------
    # 3. Full train_and_save
    # ------------------------------------------------------------------
    try:
        artifact, cv_report = train_and_save(
            "momo-kis-v1",
            ohlcv,
            output_dir,
            train_frac=train_frac,
            holding_bars=holding_bars,
            tp_sigma=tp_sigma,
            sl_sigma=sl_sigma,
            costs_bps=costs_bps,
        )
    except RuntimeError as exc:
        if "No bullish divergence events found" in str(exc):
            log.warning("[kis_pipeline] train_and_save: no events — exit_code=3.")
            return None, {"status": "no_events", "exit_code": 3}
        raise

    report = {
        "status": "ok",
        "costs_bps": costs_bps,
        "holding_bars": holding_bars,
        "tp_sigma": tp_sigma,
        "sl_sigma": sl_sigma,
        "train_frac": train_frac,
        "symbol": symbol,
        "interval": interval,
        "positive_rate": pos_rate,
        "cv": cv_report,
    }
    return artifact, report


def run_kis_pipeline_pooled(
    symbols: list[str],
    lake_dir: Path,
    output_dir: Path,
    *,
    interval: str = "1m",
    holding_bars: int = 78,
    costs_bps: float = 26.0,
    tp_sigma: float = 2.0,
    sl_sigma: float = 1.5,
    train_frac: float = 0.7,
    use_time_block_cv: bool = True,
    ohlcv_filter_config: dict | None = None,
) -> tuple[SavedArtifact, dict]:
    """Run the pooled multi-symbol KIS KRX MetaLabeler training pipeline.

    Parameters
    ----------
    symbols:
        List of KRX stock codes to pool (e.g. from get_pool_codes(30)).
    lake_dir:
        Root of the hive-partitioned data lake.
    output_dir:
        Directory where model artifacts will be written.
    interval:
        Bar interval. Default "1m" for 1-minute bars.
    holding_bars:
        Triple-barrier holding period. Default 78 (≈ 1.3h at 1m bars).
    costs_bps:
        Round-trip transaction costs in basis points.
    tp_sigma, sl_sigma:
        Take-profit / stop-loss multipliers of rolling-window σ.
    train_frac:
        Fraction of events for training.
    use_time_block_cv:
        If True, use TimeBlockGroupKFold (prevents cross-symbol leakage).
        If False, fall back to PurgedKFold.
    ohlcv_filter_config:
        Kwargs passed to filter_noise_bars(). None means use defaults.

    Returns
    -------
    (SavedArtifact, report_dict)
        report_dict includes n_symbols, rho_avg, interval, holding_bars, use_time_block_cv.

    Raises
    ------
    ValueError
        If all symbols fail to load from the lake.
    """
    from data_lake.ohlcv_filter import filter_noise_bars  # noqa: PLC0415
    from ml.meta_labeler import MetaLabeler  # noqa: PLC0415

    lake_dir = Path(lake_dir)
    output_dir = Path(output_dir)
    filter_config = ohlcv_filter_config or {}

    all_events: list[pd.DataFrame] = []
    all_features: list[pd.DataFrame] = []
    all_ohlcv_by_symbol: dict[str, pd.DataFrame] = {}
    processed_symbols: list[str] = []

    for symbol in symbols:
        try:
            raw_ohlcv = load_ohlcv_from_lake(lake_dir, symbol, interval)
        except FileNotFoundError:
            warnings.warn(
                f"[pooled_pipeline] Lake data not found for {symbol}@{interval} — skipping.",
                stacklevel=2,
            )
            continue

        filtered = filter_noise_bars(raw_ohlcv, **filter_config)
        if len(filtered) == 0:
            warnings.warn(
                f"[pooled_pipeline] All bars filtered for {symbol}@{interval} — skipping.",
                stacklevel=2,
            )
            continue

        try:
            events, features = build_events_and_features(filtered, holding_bars)
        except RuntimeError as exc:
            if "No bullish divergence events found" in str(exc):
                warnings.warn(
                    f"[pooled_pipeline] No events for {symbol}@{interval} — skipping.",
                    stacklevel=2,
                )
                continue
            raise

        features = features.copy()
        features["symbol"] = symbol
        events = events.copy()
        events["symbol"] = symbol

        all_events.append(events)
        all_features.append(features)
        all_ohlcv_by_symbol[symbol] = filtered
        processed_symbols.append(symbol)

    if not processed_symbols:
        raise ValueError(
            f"[pooled_pipeline] All {len(symbols)} symbols failed to load from lake. "
            "Run fetch_kis_backfill.py first."
        )

    log.info("[pooled_pipeline] Processed %d / %d symbols.", len(processed_symbols), len(symbols))

    # Label each symbol against its own OHLCV to avoid duplicate-timestamp issues
    all_labels: list[pd.DataFrame] = []
    for sym, events, features in zip(processed_symbols, all_events, all_features):
        sym_ohlcv = all_ohlcv_by_symbol[sym]
        sym_labels = label_events(sym_ohlcv, events, tp_sigma, sl_sigma, costs_bps)
        all_labels.append(sym_labels)

    events_concat = pd.concat(all_events, axis=0).sort_index()
    features_concat = pd.concat(all_features, axis=0).sort_index()
    labels_concat = pd.concat(all_labels, axis=0).sort_index()

    common = features_concat.index.intersection(labels_concat.index)
    if len(common) == 0:
        log.warning("[pooled_pipeline] No events after labeling — exit_code=3.")
        return None, {"status": "no_events", "exit_code": 3}  # type: ignore[return-value]

    # Drop non-numeric columns (symbol tag used for tracking only)
    numeric_cols = [c for c in features_concat.columns if c != "symbol"]
    X = features_concat.loc[common, numeric_cols]
    y = labels_concat.loc[common, "label"].astype(int)
    t1 = labels_concat.loc[common, "t_touch"]

    pos_rate = float(y.mean()) if len(y) > 0 else 0.0
    if pos_rate < 0.2:
        log.warning(
            "[pooled_pipeline] Positive label rate %.4f < 0.20 — "
            "threshold may be too strict for KRX volatility.",
            pos_rate,
        )

    n = len(X)
    split = int(n * train_frac)
    X_tr = X.iloc[:split]
    y_tr = y.iloc[:split]
    t1_tr = t1.iloc[:split]
    log.info("[pooled_pipeline] n_events=%d, train=%d, holdout=%d", n, split, n - split)

    # CV splitter selection
    if use_time_block_cv:
        splitter = TimeBlockGroupKFold(n_splits=5, embargo_frac=0.01)
    else:
        splitter = PurgedKFold(n_splits=5, embargo_frac=0.01)

    accuracies: list[float] = []
    oof_pos: list[int] = []
    oof_prob_vals: list[float] = []
    for fold_idx, (tr_idx, te_idx) in enumerate(splitter.split(X_tr, t1_tr)):
        if len(tr_idx) < 50 or len(te_idx) < 5:
            continue
        ml_fold = MetaLabeler()
        ml_fold.fit(X_tr.iloc[tr_idx], y_tr.iloc[tr_idx])
        y_prob_fold = ml_fold.win_probability(X_tr.iloc[te_idx])
        preds = (y_prob_fold >= 0.5).astype(int)
        acc = float((preds == y_tr.iloc[te_idx].values).mean())
        accuracies.append(acc)
        oof_pos.extend(list(te_idx))
        oof_prob_vals.extend(list(y_prob_fold))
        log.info("  fold %d: train=%d test=%d acc=%.4f", fold_idx, len(tr_idx), len(te_idx), acc)

    cv_report = {
        "mean_accuracy": float(np.mean(accuracies)) if accuracies else 0.0,
        "std_accuracy": float(np.std(accuracies)) if accuracies else 0.0,
        "n_folds": len(accuracies),
        "use_time_block_cv": use_time_block_cv,
    }

    # OOF probabilities indexed by X_tr's positional index (#154)
    oof_prob_pos = pd.Series(oof_prob_vals, index=oof_pos, dtype=float)

    # Fit final model on full train split
    ml_final = MetaLabeler()
    ml_final.fit(X_tr, y_tr)

    # Compute rho_avg: mean pairwise correlation of symbol daily returns
    rho_avg = 0.0
    if len(processed_symbols) > 1:
        daily_rets: dict[str, pd.Series] = {}
        for sym, sym_ohlcv in all_ohlcv_by_symbol.items():
            daily = sym_ohlcv["close"].resample("1D").last().dropna()
            daily_rets[sym] = daily.pct_change().dropna()
        ret_df = pd.DataFrame(daily_rets).dropna(how="all")
        if len(ret_df) > 1:
            corr_matrix = ret_df.corr()
            n_sym = len(corr_matrix)
            off_diag = corr_matrix.values[~np.eye(n_sym, dtype=bool)]
            rho_avg = float(off_diag.mean()) if len(off_diag) > 0 else 0.0

    from ml.reporting.cross_asset_compare import compute_effective_n  # noqa: PLC0415
    n_eff = compute_effective_n(len(processed_symbols), rho_avg)

    # Save artifacts
    output_dir.mkdir(parents=True, exist_ok=True)
    ml_final.save(output_dir)

    manifest_path = output_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({
        "strategy_id": "momo-kis-v1-pooled",
        "n_symbols": len(processed_symbols),
        "symbols": processed_symbols,
        "rho_avg": round(rho_avg, 6),
        "n_eff": round(n_eff, 4),
        "interval": interval,
        "holding_bars": holding_bars,
        "costs_bps": costs_bps,
        "use_time_block_cv": use_time_block_cv,
        "cv_score": cv_report,
        "label_config": {
            "tp_sigma": tp_sigma,
            "sl_sigma": sl_sigma,
            "holding_bars": holding_bars,
            "costs_bps": costs_bps,
        },
        "training_window": {
            "start": str(X_tr.index[0]) if len(X_tr) > 0 else "",
            "end": str(X_tr.index[-1]) if len(X_tr) > 0 else "",
            "n_events": int(len(X_tr)),
        },
        "positive_rate_train": float(y_tr.mean()),
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    artifact = SavedArtifact(
        output_dir=output_dir,
        manifest_path=manifest_path,
        cv_report_path=output_dir / "cv_report.json",
        feature_importance_path=None,
    )

    # Equity-curve raw artifacts for downstream bench (#154).
    # Not JSON-serialised here (DataFrames + dict). Caller pops/uses as needed.
    extras = {
        "events_concat": events_concat.loc[common].copy(),  # has 'symbol' col
        "labels_concat": labels_concat.loc[common].copy(),
        "ohlcv_per_symbol": all_ohlcv_by_symbol,
        "oof_prob_pos": oof_prob_pos,  # X_tr positional index → prob
        "X_tr_index": X_tr.index,
        "X_tr_symbol": features_concat.loc[common, "symbol"].iloc[:split].values,
        "train_split": split,
    }

    report = {
        "status": "ok",
        "n_symbols": len(processed_symbols),
        "rho_avg": rho_avg,
        "n_eff": n_eff,
        "interval": interval,
        "holding_bars": holding_bars,
        "costs_bps": costs_bps,
        "use_time_block_cv": use_time_block_cv,
        "positive_rate": pos_rate,
        "cv": cv_report,
        "_extras": extras,
    }
    return artifact, report
