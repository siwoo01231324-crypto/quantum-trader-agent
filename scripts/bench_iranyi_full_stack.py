#!/usr/bin/env python3
"""Pre-registered factorial bench: Iranyi 12-Rules D0~D9 full stack.

Issue #185. Reference plan:
    docs/work/active/000185-iranyi-12rules-5m/01_plan.md (status=team-plan finalized)

Variant matrix (FROZEN — sha256 of VARIANT_REGISTRY embedded in output):
    D0: 4h  VWMA cross + ema_slope>0 + ATR 1.5x stop + 7% take (B5 baseline)
    D1: 4h  D0 + regime_r4_bull + donchian_20 + time_gate
    D2: 4h  D1 + ma_alignment_50_100 + forward_ma_projection
    D3: 4h  D1 + price_ma_zscore + ma200_magnet
    D4: 4h  D1 + vpvr_poc_support + volume_burst
    D5: 4h  D2+D3+D4 full stack
    D6: 5m  D1 rules + multi_tf_gate_1h + multi_tf_gate_1d
    D7: 5m  D6 + ubai_relative_strength_top_quartile (universe: top10_alt)
    D8: 5m  D7 + turning_point_only (universe: top10_alt)
    D9: 5m  D8 + metalabeler_winprob>=0.6 (universe: top10_alt)

Usage:
    python scripts/bench_iranyi_full_stack.py --smoke
    python scripts/bench_iranyi_full_stack.py \\
        --variants D0 \\
        --start 2020-01-01 --end 2025-12-31 \\
        --output-dir docs/work/active/000185-iranyi-12rules-5m

Output: ``<output-dir>/bench_output_full_stack.json`` with per-variant metrics.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import logging
import math
import platform
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from src.features import (  # noqa: E402
    ema_slope,
    multi_tf_alignment,
    time_gate,
    vwma,
    vwma_cross,
)
from src.backtest.risk import stop_take as _stop_take  # noqa: E402

simulate_stop_take = _stop_take.simulate_stop_take
StopTakeConfig = _stop_take.StopTakeConfig
from src.ml.cv import PurgedKFold  # noqa: E402
from src.ml.validation import (  # noqa: E402
    deflated_sharpe_ratio,
    probability_of_backtest_overfitting,
)

logger = logging.getLogger("bench_iranyi_full_stack")

# ---------------------------------------------------------------------------
# FROZEN variant registry (DO NOT MODIFY — sha256 witness in output JSON)
# ---------------------------------------------------------------------------

VARIANT_REGISTRY: dict[str, dict[str, Any]] = {
    "D0": {"tf": "4h", "rules": ["vwma_cross", "ema_slope_gt_0", "atr_stop_2x_atr14", "take_7pct"]},
    "D1": {"tf": "4h", "rules": ["vwma_cross", "ema_slope_gt_0", "regime_r4_bull", "donchian_20", "time_gate", "atr_stop_2x_atr14", "take_7pct"]},
    "D2": {"tf": "4h", "rules": ["D1", "ma_alignment_50_100", "forward_ma_projection"]},
    "D3": {"tf": "4h", "rules": ["D1", "price_ma_zscore", "ma200_magnet"]},
    "D4": {"tf": "4h", "rules": ["D1", "vpvr_poc_support", "volume_burst"]},
    "D5": {"tf": "4h", "rules": ["D2_rules", "D3_rules", "D4_rules"]},
    "D6": {"tf": "5m", "rules": ["D1_rules", "multi_tf_gate_1h", "multi_tf_gate_1d"], "take_pct": 0.05},
    "D7": {"tf": "5m", "rules": ["D6_rules", "ubai_relative_strength_top_quartile"], "take_pct": 0.05, "universe": "top10_alt"},
    "D8": {"tf": "5m", "rules": ["D7_rules", "turning_point_only"], "take_pct": 0.05, "universe": "top10_alt"},
    "D9": {"tf": "5m", "rules": ["D8_rules", "metalabeler_winprob_ge_0_6"], "take_pct": 0.05, "universe": "top10_alt"},
}

# Canonical sha256 of the frozen registry
_REGISTRY_SHA256_CACHE: str | None = None


def variant_registry_sha256() -> str:
    global _REGISTRY_SHA256_CACHE
    if _REGISTRY_SHA256_CACHE is None:
        payload = json.dumps(VARIANT_REGISTRY, sort_keys=True, separators=(",", ":")).encode()
        _REGISTRY_SHA256_CACHE = hashlib.sha256(payload).hexdigest()
    return _REGISTRY_SHA256_CACHE


# ---------------------------------------------------------------------------
# CV / Gate parameters (pre-defined, §4 of 01_plan.md)
# ---------------------------------------------------------------------------

CV_PARAMS: dict[str, Any] = {
    "n_splits": 5,
    "embargo_frac": 0.01,
    "cscv_n_S": 16,
}

GATE_PARAMS: dict[str, Any] = {
    "DSR_min": 0.95,
    "DSR_n_trials": 10,
    "PBO_max": 0.20,
    "OOS_MDD_max": 0.25,
    "monthly_hit_rate_min": 0.50,
    "min_n_obs": 60,
    "min_n_trades_per_variant": 30,
}

TAKER_FEE_ROUND_TRIP = 0.0008

# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class VariantResult:
    variant_id: str
    status: str = "ok"
    n_trades: int = 0
    sharpe: float | None = None
    sortino: float | None = None
    mdd: float | None = None
    calmar: float | None = None
    avg_rr: float | None = None
    turnover: float | None = None
    monthly_hit_rate: float | None = None
    skew: float | None = None
    kurtosis_excess: float | None = None
    daily_returns: list[float] = field(default_factory=list)
    stop_take_params_used: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Data loading (reused from bench_iranyi_variants with 4h/5m resample support)
# ---------------------------------------------------------------------------


def load_ohlcv(
    data_dir: Path | None,
    symbol: str,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame | None:
    """Load OHLCV from Hive-partitioned 1m parquet lake."""
    if data_dir is None or not data_dir.exists():
        return None

    pattern_a = data_dir / f"{symbol}.parquet"
    pattern_b = data_dir / symbol / "1m"
    pattern_hive_1m = data_dir / "ohlcv" / "freq=1m"
    pattern_hive_5m = data_dir / "ohlcv" / "freq=5m"

    df: pd.DataFrame | None = None

    if pattern_a.exists():
        df = pd.read_parquet(pattern_a)
    elif pattern_b.exists():
        files = sorted(pattern_b.rglob("*.parquet"))
        if files:
            df = pd.concat([pd.read_parquet(f) for f in files], axis=0)
    else:
        # Prefer 1m hive (canonical, deterministic resample base). Fall back to 5m
        # only when symbol has no 1m partition (e.g. alt coins fetched at 5m).
        for pattern_hive in (pattern_hive_1m, pattern_hive_5m):
            if not pattern_hive.exists():
                continue
            years = range(int(start.year), int(end.year) + 1)
            files_list: list[Path] = []
            for y in years:
                year_dir = pattern_hive / f"year={y}"
                if not year_dir.exists():
                    continue
                for sym_dir in year_dir.glob(f"month=*/symbol={symbol}"):
                    files_list.extend(sym_dir.glob("*.parquet"))
            if files_list:
                df = pd.concat([pd.read_parquet(f) for f in sorted(files_list)], axis=0)
                break

    if df is None or df.empty:
        return None

    if not isinstance(df.index, pd.DatetimeIndex):
        for col in ("ts", "timestamp", "open_time"):
            if col in df.columns:
                df = df.set_index(pd.DatetimeIndex(df[col])).drop(columns=[col])
                break
    df = df.sort_index()
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df = df[~df.index.duplicated(keep="last")]
    df = df.loc[start:end]
    if {"close", "volume"} - set(df.columns):
        return None
    return df


def resample_ohlcv(frame: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Resample OHLCV to target freq with causal (right-label) alignment.

    Note: pandas freq strings — "1min"/"5min"/"15min"/"30min"/"1h"/"4h"/"1D" are correct.
    Plain "5m" is interpreted as 5 *months* (deprecated alias). We normalize "5m"→"5min" etc.
    """
    f = freq.lower().strip()
    # Normalize minute aliases: "5m"→"5min", "15m"→"15min" (avoid month confusion)
    if f.endswith("m") and not f.endswith("min") and not f.endswith("bm") and not f.endswith("am"):
        # extract integer prefix; treat as minutes
        prefix = f[:-1]
        if prefix.isdigit():
            f = prefix + "min"
    if f in ("1min", "1m", "1t"):
        return frame
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    avail = {k: v for k, v in agg.items() if k in frame.columns}
    return (
        frame.resample(f, label="right", closed="right")
        .agg(avail)
        .dropna(subset=["close"])
    )


def synthetic_ohlcv(
    start: pd.Timestamp,
    n_bars: int = 30 * 24 * 60,
    seed: int = 42,
) -> pd.DataFrame:
    """1-minute synthetic OHLCV for smoke tests."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start=start, periods=n_bars, freq="1min", tz="UTC")
    log_returns = rng.normal(loc=0.00002, scale=0.001, size=n_bars)
    close = 30000.0 * np.exp(log_returns.cumsum())
    volume = rng.lognormal(mean=2.0, sigma=0.5, size=n_bars)
    return pd.DataFrame(
        {
            "open": close,
            "high": close * (1 + np.abs(rng.normal(scale=0.0005, size=n_bars))),
            "low": close * (1 - np.abs(rng.normal(scale=0.0005, size=n_bars))),
            "close": close,
            "volume": volume,
        },
        index=idx,
    )


# ---------------------------------------------------------------------------
# Signal computation per variant
# ---------------------------------------------------------------------------


def _compute_d0_signal(df: pd.DataFrame) -> pd.Series:
    """D0: VWMA cross + ema_slope > 0 (B5 baseline)."""
    close = df["close"]
    volume = df["volume"]
    cross = vwma_cross(close, volume, window=100)
    slope = ema_slope(close, span=100, slope_window=5)
    entry_filter = (slope > 0).fillna(False)

    cross_arr = cross.to_numpy()
    filt_arr = entry_filter.to_numpy()
    pos = np.zeros(len(close), dtype=int)
    state = 0
    for i in range(len(close)):
        if state == 0:
            if cross_arr[i] == "golden" and bool(filt_arr[i]):
                state = 1
        else:
            if cross_arr[i] == "dead":
                state = 0
        pos[i] = state
    return pd.Series(pos, index=close.index, name="signal_D0")


def _compute_d1_signal(df: pd.DataFrame) -> pd.Series:
    """D1: D0 + time_gate.  regime_r4_bull + donchian_20 stubs (fallback=True)."""
    close = df["close"]
    volume = df["volume"]
    cross = vwma_cross(close, volume, window=100)
    slope = ema_slope(close, span=100, slope_window=5)
    tgate = time_gate(close.index)

    # Donchian 20-bar breakout above prior 20-bar high
    high = df.get("high", close)
    donchian_high = high.rolling(20).max().shift(1)
    donchian_ok = (close >= donchian_high).fillna(False)

    entry_filter = (
        (slope > 0).fillna(False)
        & tgate.astype(bool)
        & donchian_ok
    )

    cross_arr = cross.to_numpy()
    filt_arr = entry_filter.to_numpy()
    pos = np.zeros(len(close), dtype=int)
    state = 0
    for i in range(len(close)):
        if state == 0:
            if cross_arr[i] == "golden" and bool(filt_arr[i]):
                state = 1
        else:
            if cross_arr[i] == "dead":
                state = 0
        pos[i] = state
    return pd.Series(pos, index=close.index, name="signal_D1")


def _compute_d2_signal(df: pd.DataFrame) -> pd.Series:
    """D2: D1 + MA alignment (50/100) + forward MA projection (stub: always True)."""
    base = _compute_d1_signal(df)
    close = df["close"]

    ma50 = close.ewm(span=50, adjust=False).mean()
    ma100 = close.ewm(span=100, adjust=False).mean()
    # MA alignment: short MA above long MA for lookback=10 consecutive bars
    ma_aligned = (ma50 > ma100).rolling(10).min().fillna(0).astype(bool)

    # forward_ma_projection stub: accept all (W1 implements real version)
    return (base & ma_aligned).astype(int).rename("signal_D2")


def _compute_d3_signal(df: pd.DataFrame) -> pd.Series:
    """D3: D1 + price_ma_zscore in range + ma200 magnet (stub)."""
    base = _compute_d1_signal(df)
    close = df["close"]

    ma100 = close.ewm(span=100, adjust=False).mean()
    # price_ma_zscore: (close - ma) / rolling std of (close - ma)
    diff = close - ma100
    zscore = diff / diff.rolling(100).std(ddof=1).replace(0, np.nan).fillna(method="ffill")
    zscore_ok = (zscore.abs() < 2.0).fillna(False)

    return (base & zscore_ok).astype(int).rename("signal_D3")


def _compute_d4_signal(df: pd.DataFrame) -> pd.Series:
    """D4: D1 + VPVR POC support + volume burst (stub)."""
    base = _compute_d1_signal(df)
    volume = df["volume"]

    vol_zscore = (volume - volume.rolling(20).mean()) / volume.rolling(20).std(ddof=1).replace(0, np.nan)
    vol_burst = (vol_zscore > 0).fillna(False)

    return (base & vol_burst).astype(int).rename("signal_D4")


def _compute_d5_signal(df: pd.DataFrame) -> pd.Series:
    """D5: Full stack — intersection of D2 + D3 + D4 conditions."""
    d2 = _compute_d2_signal(df)
    d3 = _compute_d3_signal(df)
    d4 = _compute_d4_signal(df)
    return (d2.astype(bool) & d3.astype(bool) & d4.astype(bool)).astype(int).rename("signal_D5")


def _compute_d6_signal(df: pd.DataFrame) -> pd.Series:
    """D6: D1 rules on 5m + multi_tf gate (1h and 1d alignment)."""
    base = _compute_d1_signal(df)
    close = df["close"]
    volume = df["volume"]

    align_1h = multi_tf_alignment(close, volume, higher_tf="1h", vwma_window=100).astype(bool)
    align_1d = multi_tf_alignment(close, volume, higher_tf="1D", vwma_window=100).astype(bool)

    return (base.astype(bool) & align_1h & align_1d).astype(int).rename("signal_D6")


def _compute_d7_signal(df: pd.DataFrame) -> pd.Series:
    """D7: D6 + UBAI relative strength top quartile (stub: ema cross-sectional RS)."""
    base = _compute_d6_signal(df)
    close = df["close"]
    # Stub: proxy RS as EMA-50 > EMA-200 (W2 wires real UBAI index)
    ema50 = close.ewm(span=50, adjust=False).mean()
    ema200 = close.ewm(span=200, adjust=False).mean()
    rs_ok = (ema50 > ema200).fillna(False)
    return (base.astype(bool) & rs_ok).astype(int).rename("signal_D7")


def _compute_d8_signal(df: pd.DataFrame) -> pd.Series:
    """D8: D7 + turning_point_only (stub: local swing reversal)."""
    base = _compute_d7_signal(df)
    close = df["close"]
    # Turning point stub: price must have reversed from a local low in prior 5 bars
    roll_low = close.rolling(5).min().shift(1)
    turning = (close > roll_low * 1.001).fillna(False)
    return (base.astype(bool) & turning).astype(int).rename("signal_D8")


def _compute_d9_signal(df: pd.DataFrame) -> pd.Series:
    """D9: D8 + metalabeler win_prob >= 0.6 (stub: always True — requires model)."""
    # Real metalabeler requires trained model (W1/W2 scope); stub passes all.
    base = _compute_d8_signal(df)
    return base.rename("signal_D9")


_SIGNAL_DISPATCH: dict[str, Any] = {
    "D0": _compute_d0_signal,
    "D1": _compute_d1_signal,
    "D2": _compute_d2_signal,
    "D3": _compute_d3_signal,
    "D4": _compute_d4_signal,
    "D5": _compute_d5_signal,
    "D6": _compute_d6_signal,
    "D7": _compute_d7_signal,
    "D8": _compute_d8_signal,
    "D9": _compute_d9_signal,
}


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
# Trade-by-trade backtest (matches bench_vwma_stoploss_variants B5 logic)
# ---------------------------------------------------------------------------


def run_trades_with_stop_take(
    df: pd.DataFrame,
    entry_signal: pd.Series,
    cross: pd.Series,
    atr_multiplier: float = 2.0,
    take_profit_pct: float = 0.07,
    fee_round_trip: float = TAKER_FEE_ROUND_TRIP,
) -> list[dict[str, Any]]:
    """Walk bars chronologically; open on entry_signal golden, close on ATR stop / take / dead cross."""
    atr_series = _atr(df)
    dead_mask = cross == "dead"
    trades: list[dict[str, Any]] = []

    in_trade = False
    entry_price: float = 0.0
    entry_bar: pd.Timestamp | None = None
    entry_stop_loss_pct: float | None = None

    for i, (ts, row) in enumerate(df.iterrows()):
        if not in_trade:
            if entry_signal.iloc[i] != 1:
                continue
            # Enter at close of signal bar (next-bar open proxy, same as B5)
            entry_price = float(row["close"])
            entry_bar = ts
            in_trade = True
            atr_val = float(atr_series.iloc[i]) if not pd.isna(atr_series.iloc[i]) else float("nan")
            if not math.isnan(atr_val) and entry_price > 0:
                entry_stop_loss_pct = (atr_multiplier * atr_val) / entry_price
            else:
                entry_stop_loss_pct = 0.02  # fallback 2%
        else:
            current_bar = df.iloc[i: i + 1]
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
                # Round-trip fee on entry+exit
                pnl_pct = (exit_price - entry_price) / entry_price - fee_round_trip
                trades.append({
                    "entry_bar": str(entry_bar),
                    "exit_bar": str(result.triggered_at),
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "reason": result.reason,
                    "pnl_pct": pnl_pct,
                })
                in_trade = False
                entry_price = 0.0
                entry_bar = None
                entry_stop_loss_pct = None

    return trades


def compute_metrics_from_trades(trades: list[dict[str, Any]]) -> dict[str, float | None]:
    """Compute metrics from per-trade returns (matches B5 methodology)."""
    if not trades:
        return dict(sharpe=None, sortino=None, mdd=None, calmar=None,
                    avg_rr=None, monthly_hit_rate=None, skew=None, kurtosis_excess=None)

    returns = np.array([t["pnl_pct"] for t in trades], dtype=float)
    n = len(returns)
    mean_r = float(returns.mean())
    std_r = float(returns.std(ddof=1)) if n > 1 else float("nan")

    # Sharpe: per-trade returns scaled by sqrt(365) — matches B5
    sharpe = float((mean_r / std_r) * math.sqrt(365)) if std_r > 0 and not math.isnan(std_r) else None

    # Sortino: downside std of per-trade returns
    downside = returns[returns < 0]
    down_std = float(downside.std(ddof=1)) if len(downside) > 1 else float("nan")
    sortino = float((mean_r / down_std) * math.sqrt(365)) if down_std > 0 and not math.isnan(down_std) else None

    # MDD on cumulative trade equity curve
    cum = np.cumprod(1.0 + returns)
    running_max = np.maximum.accumulate(cum)
    drawdowns = (cum - running_max) / running_max
    mdd = float(drawdowns.min()) if len(drawdowns) > 0 else None

    annual_return = float(cum[-1] - 1.0) if len(cum) > 0 else None
    calmar = float(annual_return / abs(mdd)) if mdd is not None and mdd != 0 and annual_return is not None else None

    pos_trades = returns[returns > 0]
    neg_trades = returns[returns < 0]
    avg_rr = (
        float(pos_trades.mean() / abs(neg_trades.mean()))
        if len(pos_trades) > 0 and len(neg_trades) > 0 else None
    )

    # monthly_hit_rate: fraction of trades with positive PnL (matches B5 "hit_rate")
    monthly_hit_rate = float((returns > 0).mean()) if n > 0 else None

    skew = float(pd.Series(returns).skew()) if n >= 3 else None
    kurt = float(pd.Series(returns).kurt()) if n >= 4 else None

    return dict(
        sharpe=sharpe, sortino=sortino, mdd=mdd, calmar=calmar,
        avg_rr=avg_rr, monthly_hit_rate=monthly_hit_rate,
        skew=skew, kurtosis_excess=kurt,
    )


# Keep legacy bar-by-bar function for D6-D9 (5m multi-asset, no ATR stop yet)
def backtest_signal_bar_by_bar(
    df: pd.DataFrame,
    signal: pd.Series,
    fee_round_trip: float = TAKER_FEE_ROUND_TRIP,
) -> tuple[pd.Series, int, float]:
    close = df["close"]
    bar_ret = close.pct_change().fillna(0.0)
    position = signal.shift(1).fillna(0).astype(int)
    pnl = position * bar_ret
    pos_change = position.diff().abs().fillna(0)
    fee = pos_change * fee_round_trip
    pnl -= fee
    n_trades = int(pos_change.sum())
    turnover = float(pos_change.sum()) / max(len(position), 1)
    return pnl, n_trades, turnover


def compute_metrics_bar_by_bar(per_bar_returns: pd.Series) -> dict[str, float | None]:
    if per_bar_returns.empty or per_bar_returns.abs().sum() == 0:
        return dict(sharpe=None, sortino=None, mdd=None, calmar=None,
                    avg_rr=None, monthly_hit_rate=None, skew=None, kurtosis_excess=None)

    daily = per_bar_returns.resample("1D").sum()
    if len(daily) < 2:
        return dict(sharpe=None, sortino=None, mdd=None, calmar=None,
                    avg_rr=None, monthly_hit_rate=None, skew=None, kurtosis_excess=None)

    mean = float(daily.mean())
    std = float(daily.std(ddof=1))
    sharpe = float((mean / std) * math.sqrt(252)) if std > 0 else None

    downside = daily[daily < 0]
    sortino = float((mean / downside.std(ddof=1)) * math.sqrt(252)) if len(downside) > 1 and downside.std(ddof=1) > 0 else None

    equity = (1.0 + daily).cumprod()
    mdd = float((equity / equity.cummax() - 1.0).min())
    annual_return = (1.0 + mean) ** 252 - 1.0
    calmar = float(annual_return / abs(mdd)) if mdd != 0 else None

    pos_days = daily[daily > 0]
    neg_days = daily[daily < 0]
    avg_rr = float(pos_days.mean() / abs(neg_days.mean())) if len(pos_days) > 0 and len(neg_days) > 0 else None

    monthly = daily.resample("ME").sum()
    monthly_count = daily.resample("ME").count()
    monthly = monthly[monthly_count >= 5]
    monthly_hit_rate = float((monthly > 0).mean()) if len(monthly) > 0 else None

    return dict(
        sharpe=sharpe, sortino=sortino, mdd=mdd, calmar=calmar,
        avg_rr=avg_rr, monthly_hit_rate=monthly_hit_rate,
        skew=float(daily.skew()), kurtosis_excess=float(daily.kurt()),
    )


# ---------------------------------------------------------------------------
# Stop/Take rule parser + B5-style full-IS backtest (#147 reproduction)
# ---------------------------------------------------------------------------

def _resolve_leaf_rules(
    rules: list[str],
    owner: str = "",
    visited: set[str] | None = None,
) -> list[str]:
    """Resolve compound rule references (D1, D1_rules, ...) into leaf rule strings."""
    if visited is None:
        visited = set()
    leaves: list[str] = []
    for r in rules:
        if r == owner:
            continue
        if r in VARIANT_REGISTRY and r not in visited:
            visited.add(r)
            leaves.extend(_resolve_leaf_rules(VARIANT_REGISTRY[r]["rules"], r, visited))
            continue
        if r.endswith("_rules"):
            ref = r.rsplit("_", 1)[0]
            if ref in VARIANT_REGISTRY and ref not in visited:
                visited.add(ref)
                leaves.extend(_resolve_leaf_rules(VARIANT_REGISTRY[ref]["rules"], ref, visited))
                continue
        leaves.append(r)
    return leaves


def _extract_stop_take_params(variant_id: str) -> dict[str, Any]:
    """Parse stop/take rules into bench params.

    Returns dict with keys:
      - has_stop_take (bool)
      - atr_multiplier (Optional[float])
      - atr_window (int, default 14)
      - take_pct (Optional[float])
      - stop_loss_pct (Optional[float])
      - ema_slope_filter (bool)
      - fee_round_trip (float)  — 0.0 for sanity reproduction (D0), TAKER_FEE_ROUND_TRIP otherwise
    """
    spec = VARIANT_REGISTRY[variant_id]
    leaves = _resolve_leaf_rules(spec["rules"], variant_id)
    out: dict[str, Any] = {
        "has_stop_take": False,
        "atr_multiplier": None,
        "atr_window": 14,
        "take_pct": spec.get("take_pct"),
        "stop_loss_pct": None,
        "ema_slope_filter": "ema_slope_gt_0" in leaves,
        # D0 is the #147 B5 reproduction sanity check — match exactly (no fee).
        # Other variants use realistic fee.
        "fee_round_trip": 0.0 if variant_id == "D0" else TAKER_FEE_ROUND_TRIP,
    }
    for r in leaves:
        if r.startswith("atr_stop_"):
            # parse "atr_stop_2x_atr14" → multiplier=2.0, window=14
            tokens = r.split("_")
            for t in tokens:
                if t.endswith("x") and t[:-1].replace(".", "", 1).isdigit():
                    try:
                        out["atr_multiplier"] = float(t[:-1])
                    except ValueError:
                        pass
                elif t.startswith("atr") and t[3:].isdigit():
                    out["atr_window"] = int(t[3:])
            out["has_stop_take"] = True
        elif r.startswith("take_"):
            # parse "take_7pct" → 0.07
            for t in r.split("_"):
                if t.endswith("pct"):
                    try:
                        out["take_pct"] = float(t[:-3]) / 100.0
                    except ValueError:
                        pass
            out["has_stop_take"] = True
        elif r.startswith("stop_loss_"):
            for t in r.split("_"):
                if t.endswith("pct"):
                    try:
                        out["stop_loss_pct"] = float(t[:-3]) / 100.0
                    except ValueError:
                        pass
            out["has_stop_take"] = True
    return out


def _build_entry_filter_for_variant(variant_id: str, df: pd.DataFrame) -> pd.Series:
    """Per-bar entry filter mask for a variant (excludes vwma_cross trigger and stop/take exit-side rules).

    Mirrors the filter logic embedded in _compute_dN_signal so that the stop/take
    backtest path (`_run_variant_with_stop_take`) receives the same gating each
    variant uses in its CV-fold path.
    """
    close = df["close"]
    volume = df["volume"]
    leaves = set(_resolve_leaf_rules(VARIANT_REGISTRY[variant_id]["rules"], variant_id))

    entry_filter = pd.Series(True, index=close.index)

    if "ema_slope_gt_0" in leaves:
        slope = ema_slope(close, span=100, slope_window=5)
        entry_filter &= (slope > 0).fillna(False)

    if "time_gate" in leaves:
        tgate = time_gate(close.index)
        entry_filter &= tgate.astype(bool)

    if "donchian_20" in leaves:
        high = df.get("high", close)
        donchian_high = high.rolling(20).max().shift(1)
        entry_filter &= (close >= donchian_high).fillna(False)

    if "ma_alignment_50_100" in leaves:
        ma50 = close.ewm(span=50, adjust=False).mean()
        ma100 = close.ewm(span=100, adjust=False).mean()
        ma_aligned = (ma50 > ma100).rolling(10).min().fillna(0).astype(bool)
        entry_filter &= ma_aligned

    if "price_ma_zscore" in leaves:
        ma100 = close.ewm(span=100, adjust=False).mean()
        diff = close - ma100
        std = diff.rolling(100).std(ddof=1).replace(0, np.nan).ffill()
        zscore = diff / std
        entry_filter &= (zscore.abs() < 2.0).fillna(False)

    if "volume_burst" in leaves:
        vol_zscore = (volume - volume.rolling(20).mean()) / volume.rolling(20).std(ddof=1).replace(0, np.nan)
        entry_filter &= (vol_zscore > 0).fillna(False)

    # === #206 — newly wired filters (previously stubs) ===

    if "multi_tf_gate_1h" in leaves:
        # 5m → 1h alignment: True when last completed 1h bar's close > VWMA(100) on 1h
        align_1h = multi_tf_alignment(close, volume, higher_tf="1h", vwma_window=100)
        entry_filter &= align_1h.astype(bool)

    if "multi_tf_gate_1d" in leaves:
        align_1d = multi_tf_alignment(close, volume, higher_tf="1D", vwma_window=100)
        entry_filter &= align_1d.astype(bool)

    if "turning_point_only" in leaves:
        # Long-only entry: prior `lookback` bars contain a local low AND price reverses up
        from src.features.turning_point import is_local_low_then_up
        tp = is_local_low_then_up(close, lookback=5)
        entry_filter &= tp.astype(bool)

    # Remaining stubs (not yet wired):
    #   regime_r4_bull, forward_ma_projection, ma200_magnet, vpvr_poc_support,
    #   ubai_relative_strength_top_quartile (handled at multi-asset routing layer),
    #   metalabeler_winprob_ge_0_6 (requires trained model — out of scope for #206 iter 1)

    return entry_filter


def _run_variant_with_stop_take(
    variant_id: str,
    df: pd.DataFrame,
    params: dict[str, Any],
) -> VariantResult:
    """Full in-sample run with per-trade ATR stop / take (matches #147 B5 methodology)."""
    res = VariantResult(variant_id=variant_id)

    if "high" not in df.columns or "low" not in df.columns:
        res.status = "missing_ohlcv"
        return res

    close = df["close"]
    volume = df["volume"]

    cross = vwma_cross(close, volume, window=100)
    entry_filter = _build_entry_filter_for_variant(variant_id, df)
    entry_signal = ((cross == "golden") & entry_filter).astype(int)

    trades = run_trades_with_stop_take(
        df=df,
        entry_signal=entry_signal,
        cross=cross,
        atr_multiplier=params["atr_multiplier"] if params["atr_multiplier"] is not None else 2.0,
        take_profit_pct=params["take_pct"] if params["take_pct"] is not None else 0.07,
        fee_round_trip=params["fee_round_trip"],
    )

    if not trades:
        res.status = "ok"
        res.n_trades = 0
        return res

    metrics = compute_metrics_from_trades(trades)
    res.n_trades = len(trades)
    res.sharpe = metrics["sharpe"]
    res.sortino = metrics["sortino"]
    res.mdd = metrics["mdd"]
    res.calmar = metrics["calmar"]
    res.avg_rr = metrics["avg_rr"]
    res.turnover = float(len(trades) / max(len(df), 1))
    res.monthly_hit_rate = metrics["monthly_hit_rate"]
    res.skew = metrics["skew"]
    res.kurtosis_excess = metrics["kurtosis_excess"]
    # Build daily returns from trades (cumprod equity curve resampled to daily)
    if trades:
        ts_returns = pd.Series(
            [t["pnl_pct"] for t in trades],
            index=pd.to_datetime([t["exit_bar"] for t in trades]),
        )
        ts_returns = ts_returns[~ts_returns.index.duplicated(keep="last")]
        daily = ts_returns.resample("1D").sum().fillna(0.0)
        res.daily_returns = daily.tolist()
    return res


# ---------------------------------------------------------------------------
# Multi-asset routing (#206 — D7+ universe='top10_alt' real evaluation)
# ---------------------------------------------------------------------------

_TOP10_ALT_UNIVERSE = (
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "ATOMUSDT",
)


def _build_cross_asset_rs_filter(
    symbols_data: dict[str, pd.DataFrame],
    lookback_bars: int = 24,  # ~2 hours on 5m bars
) -> pd.DataFrame:
    """For each (timestamp, symbol), return True if symbol's trailing return is in
    cross-asset top quartile within the universe.

    Wide DataFrame indexed by timestamp, columns = symbols.
    """
    # Build returns wide-DF (NaN where symbol has no data at that ts)
    series = {}
    for sym, df in symbols_data.items():
        series[sym] = df["close"].pct_change(lookback_bars)
    rets = pd.DataFrame(series).sort_index()

    # Rank cross-asset (axis=1, descending: rank 1 = highest return)
    ranks = rets.rank(axis=1, ascending=False, method="min")
    n_universe = ranks.notna().sum(axis=1)
    quartile_thresh = (n_universe * 0.25).round().clip(lower=1)
    top_q = ranks.le(quartile_thresh, axis=0).fillna(False)
    return top_q


def _run_variant_multi_asset(
    variant_id: str,
    symbols_data: dict[str, pd.DataFrame],
    params: dict[str, Any],
) -> VariantResult:
    """Multi-asset full-IS run: per-symbol trade simulation + cross-asset RS filter."""
    res = VariantResult(variant_id=variant_id)

    leaves = set(_resolve_leaf_rules(VARIANT_REGISTRY[variant_id]["rules"], variant_id))
    use_rs_filter = "ubai_relative_strength_top_quartile" in leaves

    # Pre-compute cross-asset RS filter once (across all symbols)
    if use_rs_filter:
        rs_top_q = _build_cross_asset_rs_filter(symbols_data, lookback_bars=24)
    else:
        rs_top_q = None

    all_trades: list[dict[str, Any]] = []
    per_symbol_n_trades: dict[str, int] = {}

    for sym, df in symbols_data.items():
        if "high" not in df.columns or "low" not in df.columns:
            continue

        close = df["close"]
        volume = df["volume"]

        cross = vwma_cross(close, volume, window=100)
        entry_filter = _build_entry_filter_for_variant(variant_id, df)
        if rs_top_q is not None and sym in rs_top_q.columns:
            sym_rs = rs_top_q[sym].reindex(close.index, method="ffill").fillna(False)
            entry_filter &= sym_rs.astype(bool)

        entry_signal = ((cross == "golden") & entry_filter).astype(int)

        sym_trades = run_trades_with_stop_take(
            df=df,
            entry_signal=entry_signal,
            cross=cross,
            atr_multiplier=params["atr_multiplier"] if params["atr_multiplier"] is not None else 2.0,
            take_profit_pct=params["take_pct"] if params["take_pct"] is not None else 0.05,
            fee_round_trip=params["fee_round_trip"],
        )
        for t in sym_trades:
            t["symbol"] = sym
        all_trades.extend(sym_trades)
        per_symbol_n_trades[sym] = len(sym_trades)

    if not all_trades:
        res.status = "ok"
        res.n_trades = 0
        return res

    # Sort all trades by exit_bar for sequential equity curve
    all_trades.sort(key=lambda t: t["exit_bar"])

    metrics = compute_metrics_from_trades(all_trades)
    res.n_trades = len(all_trades)
    res.sharpe = metrics["sharpe"]
    res.sortino = metrics["sortino"]
    res.mdd = metrics["mdd"]
    res.calmar = metrics["calmar"]
    res.avg_rr = metrics["avg_rr"]
    res.turnover = float(len(all_trades) / max(sum(len(d) for d in symbols_data.values()), 1))
    res.monthly_hit_rate = metrics["monthly_hit_rate"]
    res.skew = metrics["skew"]
    res.kurtosis_excess = metrics["kurtosis_excess"]

    # Build daily returns from all trades
    if all_trades:
        ts_returns = pd.Series(
            [t["pnl_pct"] for t in all_trades],
            index=pd.to_datetime([t["exit_bar"] for t in all_trades]),
        )
        ts_returns = ts_returns.groupby(ts_returns.index).sum()  # multi-asset same-bar exits
        daily = ts_returns.resample("1D").sum().fillna(0.0)
        res.daily_returns = daily.tolist()
    return res


def run_variant(
    variant_id: str,
    df: pd.DataFrame,
    cv_splits: list[tuple[np.ndarray, np.ndarray]],
    symbols_data: dict[str, pd.DataFrame] | None = None,
) -> VariantResult:
    res = VariantResult(variant_id=variant_id)

    # Branch: variants with stop/take rules use full-IS B5-style trade simulation
    stop_take_params = _extract_stop_take_params(variant_id)
    if stop_take_params["has_stop_take"]:
        spec = VARIANT_REGISTRY[variant_id]
        # Multi-asset path: variant has universe='top10_alt' and symbols_data provided
        if spec.get("universe") == "top10_alt" and symbols_data:
            sub = _run_variant_multi_asset(variant_id, symbols_data, stop_take_params)
            sub.stop_take_params_used = {
                "atr_multiplier": stop_take_params["atr_multiplier"],
                "atr_window": stop_take_params["atr_window"],
                "take_pct": stop_take_params["take_pct"],
                "stop_loss_pct": stop_take_params["stop_loss_pct"],
                "ema_slope_filter": stop_take_params["ema_slope_filter"],
                "fee_round_trip": stop_take_params["fee_round_trip"],
                "universe": "top10_alt",
                "n_universe_symbols": len(symbols_data),
            }
            return sub

        sub = _run_variant_with_stop_take(variant_id, df, stop_take_params)
        sub.stop_take_params_used = {
            "atr_multiplier": stop_take_params["atr_multiplier"],
            "atr_window": stop_take_params["atr_window"],
            "take_pct": stop_take_params["take_pct"],
            "stop_loss_pct": stop_take_params["stop_loss_pct"],
            "ema_slope_filter": stop_take_params["ema_slope_filter"],
            "fee_round_trip": stop_take_params["fee_round_trip"],
        }
        return sub

    min_trades = GATE_PARAMS["min_n_trades_per_variant"]

    fn = _SIGNAL_DISPATCH.get(variant_id)
    if fn is None:
        res.status = "unknown_variant"
        return res

    try:
        signal = fn(df)
    except Exception as exc:
        logger.warning("Signal computation failed for %s: %s", variant_id, exc)
        res.status = "signal_error"
        return res

    if signal.abs().sum() == 0:
        res.status = "ok"
        res.n_trades = 0
        return res

    oos_pieces: list[pd.Series] = []
    n_trades_total = 0
    turnover_sum = 0.0

    for _, test_idx in cv_splits:
        if len(test_idx) == 0:
            continue
        idx_slice = df.index[test_idx]
        sub_df = df.loc[idx_slice]
        sub_signal = signal.loc[idx_slice]
        per_bar, n_t, turnover = backtest_signal(sub_df, sub_signal)
        oos_pieces.append(per_bar)
        n_trades_total += n_t
        turnover_sum += turnover

    if not oos_pieces:
        return res

    oos = pd.concat(oos_pieces).sort_index()

    if n_trades_total < min_trades:
        res.status = "insufficient_sample"
        res.n_trades = n_trades_total
        return res

    metrics = compute_metrics(oos)
    res.n_trades = n_trades_total
    res.sharpe = metrics["sharpe"]
    res.sortino = metrics["sortino"]
    res.mdd = metrics["mdd"]
    res.calmar = metrics["calmar"]
    res.avg_rr = metrics["avg_rr"]
    res.turnover = float(turnover_sum / max(len(cv_splits), 1))
    res.monthly_hit_rate = metrics["monthly_hit_rate"]
    res.skew = metrics["skew"]
    res.kurtosis_excess = metrics["kurtosis_excess"]
    res.daily_returns = oos.resample("1D").sum().fillna(0.0).tolist()
    return res


# ---------------------------------------------------------------------------
# DSR / PBO scoring (same as bench_iranyi_variants)
# ---------------------------------------------------------------------------


def aggregate_and_score(
    results: dict[str, VariantResult],
    n_obs_per_variant: int,
) -> dict[str, Any]:
    eligible = [r for r in results.values() if r.status == "ok" and r.sharpe is not None]
    n_actual = len(eligible)
    if n_actual < 2:
        return dict(dsr=None, pbo=None, dsr_n_trials=n_actual,
                    winning_variant=None, gate_passed=False,
                    gate_reason="insufficient eligible variants for DSR/PBO")

    sr_estimates = np.array([r.sharpe for r in eligible], dtype=float)
    best = max(eligible, key=lambda r: r.sharpe)
    dsr = deflated_sharpe_ratio(
        observed_sr=float(best.sharpe),
        sr_estimates=sr_estimates,
        n_obs=n_obs_per_variant,
        skew=float(best.skew or 0.0),
        kurtosis_excess=float(best.kurtosis_excess or 0.0),
        n_trials=n_actual,
    )

    daily_lengths = [len(r.daily_returns) for r in eligible]
    t_max = max(daily_lengths)
    matrix = np.zeros((t_max, n_actual), dtype=float)
    for i, r in enumerate(eligible):
        d = np.asarray(r.daily_returns, dtype=float)
        matrix[: len(d), i] = d

    n_groups = 16 if t_max >= 16 else (8 if t_max >= 8 else 4)
    pbo = probability_of_backtest_overfitting(matrix, n_groups=n_groups) if t_max >= n_groups and n_actual >= 2 else None

    reasons: list[str] = []
    if dsr is None or dsr < GATE_PARAMS["DSR_min"]:
        reasons.append(f"DSR={dsr} < {GATE_PARAMS['DSR_min']}")
    if pbo is None or pbo > GATE_PARAMS["PBO_max"]:
        reasons.append(f"PBO={pbo} > {GATE_PARAMS['PBO_max']}")
    if best.mdd is not None and best.mdd < -GATE_PARAMS["OOS_MDD_max"]:
        reasons.append(f"MDD={best.mdd:.4f} < -{GATE_PARAMS['OOS_MDD_max']}")
    if best.monthly_hit_rate is None or best.monthly_hit_rate < GATE_PARAMS["monthly_hit_rate_min"]:
        reasons.append(f"monthly_hit_rate={best.monthly_hit_rate} < {GATE_PARAMS['monthly_hit_rate_min']}")

    return dict(
        dsr=float(dsr) if dsr is not None else None,
        pbo=float(pbo) if pbo is not None else None,
        dsr_n_trials=n_actual,
        winning_variant=best.variant_id,
        gate_passed=len(reasons) == 0,
        gate_reason="; ".join(reasons) if reasons else "all gates passed",
    )


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


def git_commit_hash() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def cv_split_hash(cv_splits: list[tuple[np.ndarray, np.ndarray]], df: pd.DataFrame) -> str:
    """SHA-256 of each fold's (train_first, train_last, test_first, test_last) timestamps."""
    pieces: list[bytes] = []
    for train_idx, test_idx in cv_splits:
        if len(train_idx) == 0 or len(test_idx) == 0:
            continue
        t_first = df.index[train_idx[0]].isoformat().encode()
        t_last = df.index[train_idx[-1]].isoformat().encode()
        te_first = df.index[test_idx[0]].isoformat().encode()
        te_last = df.index[test_idx[-1]].isoformat().encode()
        pieces.extend([t_first, t_last, te_first, te_last])
    return hashlib.sha256(b"".join(pieces)).hexdigest()


def variant_to_dict(r: VariantResult) -> dict[str, Any]:
    out = dict(
        variant_id=r.variant_id, status=r.status, n_trades=r.n_trades,
        sharpe=r.sharpe, sortino=r.sortino, mdd=r.mdd, calmar=r.calmar,
        avg_rr=r.avg_rr, turnover=r.turnover,
        monthly_hit_rate=r.monthly_hit_rate,
        skew=r.skew, kurtosis_excess=r.kurtosis_excess,
    )
    if r.stop_take_params_used is not None:
        out["stop_take_params_used"] = r.stop_take_params_used
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument(
        "--variants",
        type=str,
        default=None,
        help="Comma-separated variant IDs to run (e.g. D0,D1). Defaults to all.",
    )
    p.add_argument("--data-dir", type=Path, default=None,
                   help="Lake root dir (contains ohlcv/freq=1m/...)")
    p.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "docs/work/active/000185-iranyi-12rules-5m",
    )
    p.add_argument("--start", type=str, default="2020-01-01")
    p.add_argument("--end", type=str, default="2025-12-31")
    p.add_argument("--n-splits", type=int, default=CV_PARAMS["n_splits"])
    p.add_argument("--embargo-frac", type=float, default=CV_PARAMS["embargo_frac"])
    p.add_argument("--symbol", type=str, default="BTCUSDT",
                   help="Primary symbol for single-asset variants (D0-D6).")
    p.add_argument(
        "--smoke",
        action="store_true",
        help="30-day synthetic data smoke test (no real data needed).",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)

    start = pd.Timestamp(args.start, tz="UTC")
    end = pd.Timestamp(args.end, tz="UTC")

    # Determine which variants to run
    if args.variants:
        variant_ids = [v.strip() for v in args.variants.split(",") if v.strip()]
        unknown = [v for v in variant_ids if v not in VARIANT_REGISTRY]
        if unknown:
            logger.error("Unknown variants: %s. Valid: %s", unknown, list(VARIANT_REGISTRY))
            return 1
    else:
        variant_ids = list(VARIANT_REGISTRY.keys())

    # Load / generate data
    if args.smoke:
        raw_1m = synthetic_ohlcv(start=start, n_bars=30 * 24 * 60)
        data_source = "synthetic-smoke"
        logger.info("Smoke mode: using 30 days of synthetic 1m OHLCV.")
    else:
        raw_1m = load_ohlcv(args.data_dir or (REPO_ROOT / "lake"), args.symbol, start, end)
        if raw_1m is None or raw_1m.empty:
            logger.error(
                "OHLCV unavailable from %s for %s. Run with --smoke or fetch data first.",
                args.data_dir, args.symbol,
            )
            return 2
        data_source = f"{args.data_dir or 'lake'}::{args.symbol}"

    results: dict[str, VariantResult] = {}
    all_cv_splits: dict[str, list] = {}

    # Multi-asset universe data — loaded lazily when first variant with
    # universe='top10_alt' is encountered.
    multi_asset_data: dict[str, pd.DataFrame] | None = None

    def _load_multi_asset_5m() -> dict[str, pd.DataFrame]:
        """Load 5m OHLCV for all top-10 alt symbols."""
        out: dict[str, pd.DataFrame] = {}
        lake_root = args.data_dir or (REPO_ROOT / "lake")
        for sym in _TOP10_ALT_UNIVERSE:
            sym_raw = load_ohlcv(lake_root, sym, start, end)
            if sym_raw is None or sym_raw.empty:
                logger.warning("Multi-asset: %s OHLCV unavailable, skipping.", sym)
                continue
            sym_5m = resample_ohlcv(sym_raw, "5m")
            if len(sym_5m) < 200:
                logger.warning("Multi-asset: %s only %d 5m bars, skipping.", sym, len(sym_5m))
                continue
            out[sym] = sym_5m
        logger.info("Multi-asset universe loaded: %d/%d symbols (%s)",
                    len(out), len(_TOP10_ALT_UNIVERSE), ", ".join(sorted(out.keys())))
        return out

    for variant_id in variant_ids:
        spec = VARIANT_REGISTRY[variant_id]
        tf = spec.get("tf", "4h")

        # Resample to target timeframe
        df = resample_ohlcv(raw_1m, tf)

        if len(df) < 200:
            logger.warning(
                "Variant %s: only %d bars after resample to %s, marking insufficient_sample.",
                variant_id, len(df), tf,
            )
            res = VariantResult(variant_id=variant_id, status="insufficient_sample")
            results[variant_id] = res
            continue

        # Build CV splits
        horizon = pd.Timedelta(hours=4) if tf == "4h" else pd.Timedelta(minutes=30)
        last_ts = df.index[-1]
        t1_series = pd.Series(df.index + horizon, index=df.index)
        t1_series = t1_series.where(t1_series <= last_ts, last_ts)
        cv = PurgedKFold(n_splits=args.n_splits, embargo_frac=args.embargo_frac)
        splits = list(cv.split(df, t1_series))
        all_cv_splits[variant_id] = splits

        # Lazy-load multi-asset data when needed
        symbols_data: dict[str, pd.DataFrame] | None = None
        if spec.get("universe") == "top10_alt":
            if multi_asset_data is None and not args.smoke:
                multi_asset_data = _load_multi_asset_5m()
            symbols_data = multi_asset_data

        logger.info("Running variant %s (tf=%s, bars=%d, multi_asset=%s)",
                    variant_id, tf, len(df),
                    bool(symbols_data) and len(symbols_data) > 1)
        results[variant_id] = run_variant(variant_id, df, splits, symbols_data=symbols_data)

    # Score (only variants that share same tf — D0-D5 are 4h, D6-D9 are 5m)
    def _score_group(ids: list[str]) -> dict[str, Any]:
        group = {k: results[k] for k in ids if k in results}
        if not group:
            return {}
        # Use length of the largest df for n_obs
        n_obs = max(
            (len(resample_ohlcv(raw_1m, VARIANT_REGISTRY[k].get("tf", "4h")))
             for k in ids if k in results),
            default=1,
        )
        return aggregate_and_score(group, n_obs // args.n_splits)

    group_4h = [v for v in variant_ids if VARIANT_REGISTRY[v].get("tf") == "4h"]
    group_5m = [v for v in variant_ids if VARIANT_REGISTRY[v].get("tf") == "5m"]

    # cv_split_hash: use first variant's splits as representative
    first_id = variant_ids[0] if variant_ids else None
    first_splits = all_cv_splits.get(first_id, []) if first_id else []
    first_df = resample_ohlcv(raw_1m, VARIANT_REGISTRY[first_id].get("tf", "4h")) if first_id else raw_1m
    split_hash = cv_split_hash(first_splits, first_df) if first_splits else "no_splits"

    output = {
        "schema_version": "iranyi-12rules-185/v1",
        "issue": 185,
        "data_source": data_source,
        "symbol": args.symbol,
        "start": str(start),
        "end": str(end),
        "git_commit": git_commit_hash(),
        "python_version": platform.python_version(),
        "variant_registry_sha256": variant_registry_sha256(),
        "cv_params": {
            **CV_PARAMS,
            "n_splits": args.n_splits,
            "embargo_frac": args.embargo_frac,
        },
        "cv_split_hash": split_hash,
        "gate_params": GATE_PARAMS,
        "fee_round_trip": TAKER_FEE_ROUND_TRIP,
        "variants": [variant_to_dict(r) for r in results.values()],
        "scoring_4h": _score_group(group_4h) if group_4h else {},
        "scoring_5m": _score_group(group_5m) if group_5m else {},
        "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    out_path = args.output_dir / "bench_output_full_stack.json"
    out_path.write_text(json.dumps(output, indent=2, default=str), encoding="utf-8")
    logger.info("Wrote %s", out_path)

    # Human-readable summary
    print()
    print("=" * 70)
    print(f"Iranyi 12-Rules #185 -- full stack bench: {out_path}")
    print(f"  registry sha256: {variant_registry_sha256()[:16]}...")
    print(f"  cv_split_hash:   {split_hash[:16]}...")
    print(f"  git_commit:      {output['git_commit'][:12]}...")
    print()
    print(f"{'ID':<4} {'tf':<4} {'status':<22} {'n_trades':>9} {'Sharpe':>8} {'MDD':>8} {'mhr':>6}")
    print("-" * 70)
    for r in results.values():
        print(
            f"{r.variant_id:<4} {VARIANT_REGISTRY[r.variant_id].get('tf','?'):<4} {r.status:<22} "
            f"{r.n_trades:>9d} "
            f"{(f'{r.sharpe:.3f}' if r.sharpe is not None else 'NA'):>8} "
            f"{(f'{r.mdd:.4f}' if r.mdd is not None else 'NA'):>8} "
            f"{(f'{r.monthly_hit_rate:.2f}' if r.monthly_hit_rate is not None else 'NA'):>6}"
        )
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main())
