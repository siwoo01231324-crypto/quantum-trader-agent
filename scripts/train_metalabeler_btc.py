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
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.cv import PurgedKFold  # noqa: E402
from ml.labeling import triple_barrier_label  # noqa: E402
from ml.meta_labeler import MetaLabeler, MetaLabelerConfig  # noqa: E402
from signals.atr import compute_atr  # noqa: E402
from signals.rsi import compute_rsi, detect_divergence  # noqa: E402


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_ohlcv_from_lake(lake_dir: Path, symbol: str, interval: str) -> pd.DataFrame:
    """Concat all parquet shards for the symbol/interval into one DataFrame."""
    base = lake_dir / "ohlcv" / f"freq={interval}"
    if not base.exists():
        raise FileNotFoundError(f"Lake not found: {base}")

    shards = sorted(base.glob(f"year=*/month=*/symbol={symbol}/part-*.parquet"))
    if not shards:
        raise FileNotFoundError(f"No shards for {symbol} @ {interval}")

    frames = [pd.read_parquet(s) for s in shards]
    df = pd.concat(frames, axis=0, ignore_index=True)
    # Lake shards carry `ts` as a column, not an index. Promote it to DatetimeIndex.
    if "ts" in df.columns:
        df["ts"] = pd.to_datetime(df["ts"], utc=True)
        df = df.set_index("ts").sort_index()
    elif not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
        df = df.sort_index()
    df = df[~df.index.duplicated(keep="first")]
    print(f"[data] loaded {len(df)} bars from {len(shards)} shards ({df.index.min()} → {df.index.max()})")
    return df


# ---------------------------------------------------------------------------
# Event/feature extraction
# ---------------------------------------------------------------------------

RSI_PERIOD = 14
LOOKBACK = 14
FEATURE_NAMES = [
    "rsi", "atr", "divergence_magnitude", "bars_since_pivot",
    "confidence", "close", "volume",
]


def _confidence(div_magnitude: float, atr: float, bars_since_pivot: int) -> float:
    if atr <= 0.0:
        return 0.0
    return max(0.0, min(1.0, abs(div_magnitude) / atr * min(bars_since_pivot / LOOKBACK, 1.0)))


def build_events_and_features(
    ohlcv: pd.DataFrame,
    holding_bars: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """For every bar where bullish divergence fires, build an event row + feature row.

    Returns
    -------
    events: DataFrame indexed by entry_ts, columns [side:+1, t1:datetime]
    features: DataFrame indexed by entry_ts, columns = FEATURE_NAMES
    """
    close = ohlcv["close"]
    high = ohlcv["high"]
    low = ohlcv["low"]
    volume = ohlcv["volume"]

    rsi = compute_rsi(close, RSI_PERIOD)
    div = detect_divergence(close, rsi, LOOKBACK)
    atr = compute_atr(high, low, close)

    events: list[tuple] = []
    feature_rows: list[dict] = []
    min_bars = RSI_PERIOD + LOOKBACK * 2 + 1

    for i in range(min_bars, len(ohlcv) - holding_bars):
        if div.iloc[i] != "bullish":
            continue
        entry_ts = ohlcv.index[i]
        t1 = ohlcv.index[i + holding_bars]
        atr_val = float(atr.iloc[i]) if not np.isnan(atr.iloc[i]) else 0.0
        div_magnitude = float(close.iloc[i] - close.iloc[i - LOOKBACK])
        # bars_since_pivot in recent window
        recent = div.iloc[i - LOOKBACK:i + 1]
        bullish_pos = [k for k, v in enumerate(recent) if v == "bullish"]
        bars_since_pivot = (len(recent) - bullish_pos[0]) if bullish_pos else LOOKBACK
        conf = _confidence(div_magnitude, atr_val, bars_since_pivot)

        events.append((entry_ts, 1, t1))
        feature_rows.append({
            "rsi": float(rsi.iloc[i]),
            "atr": atr_val,
            "divergence_magnitude": div_magnitude,
            "bars_since_pivot": bars_since_pivot,
            "confidence": conf,
            "close": float(close.iloc[i]),
            "volume": float(volume.iloc[i]),
        })

    if not events:
        raise RuntimeError("No bullish divergence events found — widen data window or check RSI.")

    idx = [e[0] for e in events]
    events_df = pd.DataFrame({
        "side": [e[1] for e in events],
        "t1": [e[2] for e in events],
    }, index=pd.DatetimeIndex(idx, name="entry_ts"))
    features_df = pd.DataFrame(feature_rows, index=events_df.index)[FEATURE_NAMES]

    print(f"[events] {len(events_df)} bullish divergences")
    return events_df, features_df


# ---------------------------------------------------------------------------
# Labeling
# ---------------------------------------------------------------------------

def label_events(
    ohlcv: pd.DataFrame,
    events: pd.DataFrame,
    tp_sigma: float,
    sl_sigma: float,
    costs_bps: float,
) -> pd.DataFrame:
    """Triple-barrier labels. tp/sl widths = sigma_window_20 × multiplier."""
    close = ohlcv["close"]
    ret = close.pct_change()
    sigma = ret.rolling(20).std().reindex(events.index, method="ffill").fillna(ret.std())

    tp = sigma * tp_sigma
    sl = sigma * sl_sigma

    labels_df = triple_barrier_label(
        prices=close,
        events=events,
        tp=tp,
        sl=sl,
        costs_bps=costs_bps,
    )
    pos_rate = float(labels_df["label"].mean())
    print(f"[labels] positive rate = {pos_rate:.4f} ({int(labels_df['label'].sum())}/{len(labels_df)})")
    return labels_df


# ---------------------------------------------------------------------------
# Training / CV
# ---------------------------------------------------------------------------

def run_cv(X: pd.DataFrame, y: pd.Series, t1: pd.Series, n_splits: int = 5, embargo: float = 0.01) -> dict:
    cv = PurgedKFold(n_splits=n_splits, embargo_frac=embargo)
    accuracies: list[float] = []
    fold_info: list[dict] = []

    for fold_idx, (train_idx, test_idx) in enumerate(cv.split(X, t1)):
        if len(train_idx) < 50 or len(test_idx) < 5:
            fold_info.append({"fold": fold_idx, "skipped": True, "train": len(train_idx), "test": len(test_idx)})
            continue
        X_tr = X.iloc[train_idx]; y_tr = y.iloc[train_idx]
        X_te = X.iloc[test_idx]; y_te = y.iloc[test_idx]
        ml = MetaLabeler()
        ml.fit(X_tr, y_tr)
        preds = (ml.win_probability(X_te) >= 0.5).astype(int)
        acc = float((preds == y_te.values).mean())
        accuracies.append(acc)
        fold_info.append({"fold": fold_idx, "train": len(train_idx), "test": len(test_idx), "accuracy": acc})
        print(f"  fold {fold_idx}: train={len(train_idx)}, test={len(test_idx)}, acc={acc:.4f}")

    mean_acc = float(np.mean(accuracies)) if accuracies else 0.0
    std_acc = float(np.std(accuracies)) if accuracies else 0.0
    print(f"[cv] mean_accuracy = {mean_acc:.4f} ± {std_acc:.4f} over {len(accuracies)} folds")
    return {
        "mean_accuracy": mean_acc,
        "std_accuracy": std_acc,
        "n_folds": len(accuracies),
        "embargo_frac": embargo,
        "folds": fold_info,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Train MetaLabeler on BTC historical data")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--holding-bars", type=int, default=24)  # 24 × 15m = 6h
    parser.add_argument("--tp-sigma", type=float, default=2.0)
    parser.add_argument("--sl-sigma", type=float, default=1.5)
    parser.add_argument("--costs-bps", type=float, default=4.0)  # Binance spot fee ~10bps round-trip; conservatively 4bps for futures
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--output-dir", type=Path, default=WORKTREE / "models/momo-btc-v2")
    args = parser.parse_args()

    ohlcv = load_ohlcv_from_lake(args.lake_dir, args.symbol, args.interval)
    events, features = build_events_and_features(ohlcv, args.holding_bars)
    labels_df = label_events(ohlcv, events, args.tp_sigma, args.sl_sigma, args.costs_bps)

    # align
    common = features.index.intersection(labels_df.index)
    X = features.loc[common]
    y = labels_df.loc[common, "label"].astype(int)
    t1 = labels_df.loc[common, "t_touch"]

    # train/holdout split by time
    n = len(X)
    split = int(n * args.train_frac)
    X_tr, X_ho = X.iloc[:split], X.iloc[split:]
    y_tr, y_ho = y.iloc[:split], y.iloc[split:]
    t1_tr = t1.iloc[:split]
    print(f"[split] train={len(X_tr)}, holdout={len(X_ho)}")

    # CV on train
    cv_report = run_cv(X_tr, y_tr, t1_tr)

    # final fit on full train
    ml = MetaLabeler()
    ml.fit(X_tr, y_tr)

    # holdout evaluation
    if len(X_ho) > 0:
        ho_preds = (ml.win_probability(X_ho) >= 0.5).astype(int)
        ho_acc = float((ho_preds == y_ho.values).mean())
        print(f"[holdout] accuracy = {ho_acc:.4f} over {len(X_ho)} samples")
    else:
        ho_acc = None

    # save
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out_dir = args.output_dir / ts
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=WORKTREE, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        git_sha = "unknown"

    # Use MetaLabeler.save (writes model.lgbm + manifest.json)
    saved = ml.save(out_dir)
    print(f"[save] base artifact at {saved}")

    # Augment manifest with training-level info (load, merge, resave).
    manifest_path = out_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    manifest.update({
        "strategy_id": "momo-btc-v2",
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "git_sha": git_sha,
        "feature_names": FEATURE_NAMES,
        "label_config": {
            "tp_sigma": args.tp_sigma,
            "sl_sigma": args.sl_sigma,
            "holding_bars": args.holding_bars,
            "costs_bps": args.costs_bps,
        },
        "cv_score": {
            "mean_accuracy": cv_report["mean_accuracy"],
            "std_accuracy": cv_report["std_accuracy"],
            "n_folds": cv_report["n_folds"],
            "embargo_frac": cv_report["embargo_frac"],
        },
        "holdout_accuracy": ho_acc,
        "training_window": {
            "start": str(ohlcv.index[0]),
            "end": str(ohlcv.index[int(n * args.train_frac) - 1]) if split > 0 else None,
            "n_events": int(len(X_tr)),
        },
        "positive_rate_train": float(y_tr.mean()),
    })
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")

    # Save CV report
    (out_dir / "cv_report.json").write_text(json.dumps(cv_report, indent=2), encoding="utf-8")

    # Save feature importance (permutation on train)
    try:
        importance = ml.feature_importance(method="permutation")
        (out_dir / "feature_importance.json").write_text(
            json.dumps(importance.to_dict(), indent=2), encoding="utf-8"
        )
    except Exception as exc:
        print(f"[warn] feature importance failed: {exc}")

    print(f"\n[done] Model artifacts saved to: {out_dir}")
    print(f"  CV mean accuracy: {cv_report['mean_accuracy']:.4f}")
    if ho_acc is not None:
        print(f"  Holdout accuracy: {ho_acc:.4f}")

    # AC gate for training step: mean_accuracy >= 0.55
    if cv_report["mean_accuracy"] < 0.55:
        print(f"[WARN] CV mean accuracy {cv_report['mean_accuracy']:.4f} < 0.55 - metalabeler deployment not recommended")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
