"""Trend / regime indicator helpers for live-scanner strategies (2026-05-26).

Pure functions consumed by ``LiveScannerMixin._check_trend_filter`` /
``_check_regime_filter``. Kept dependency-free (numpy + pandas only) so the
existing live-scanner contract — synchronous, stateless per-call — stays
identical and unit tests can synth small OHLCV frames without external libs.

Each function returns ``None`` (or NaN) when the history is too short, never
raises. Callers treat that as "indicator unavailable → don't filter" so a
warm-up window never falsifies entries.

Provenance:
    - ADX/EMA filter — pattern from Wilder (1978) + multi-TF discussion in
      FMZQuant ATR-Trailing+ADX writeup (Medium, 2024).
    - Hurst exponent — R/S analysis short-window estimator; cf. QuantAlgo
      "Hurst Exponent Adaptive Supertrend" (TradingView, 2025).
    - Choppiness Index — E.W. Dreiss formula; Fibonacci 38.2/61.8 thresholds.

These are the journal-evidence improvements for the 2026-05-22~05-25 BTC
sideways false-breakout cluster (-312 USDT cumulative) — see
``docs/journal/2026-05-25.md`` "내일을 위한 한 줄".
"""
from __future__ import annotations

import math
from typing import Final

import numpy as np
import pandas as pd

# ---- public sentinel constants — keep callers from magic-number drift -------

ADX_TREND_DEFAULT: Final[float] = 20.0
CHOPPINESS_RANGE_DEFAULT: Final[float] = 61.8   # > → ranging
CHOPPINESS_TREND_DEFAULT: Final[float] = 38.2   # < → trending
HURST_TREND_DEFAULT: Final[float] = 0.55        # > → persistent (trend)
HURST_MEANREV_DEFAULT: Final[float] = 0.45      # < → anti-persistent (meanrev)


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average. NaN until ``period`` bars available."""
    if period <= 0:
        raise ValueError(f"period must be > 0, got {period}")
    return series.astype(float).ewm(span=period, adjust=False, min_periods=period).mean()


def adx(history: pd.DataFrame, period: int = 14) -> float | None:
    """Wilder ADX. Returns last-bar value, or ``None`` if not enough bars.

    Needs at least ``2 * period + 1`` bars for a stable read (period bars for
    +DM/-DM smoothing, then another period for the DX → ADX smoothing).
    """
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")
    need = 2 * period + 1
    if len(history) < need:
        return None
    high = history["high"].astype(float).values
    low = history["low"].astype(float).values
    close = history["close"].astype(float).values

    up_move = high[1:] - high[:-1]
    down_move = low[:-1] - low[1:]
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])

    # Wilder smoothing — first value = sum of first `period`, then
    # next = prev - prev/period + current.
    def _wilder(arr: np.ndarray) -> np.ndarray:
        n = len(arr)
        out = np.full(n, np.nan)
        if n < period:
            return out
        out[period - 1] = arr[:period].sum()
        for i in range(period, n):
            out[i] = out[i - 1] - (out[i - 1] / period) + arr[i]
        return out

    tr_s = _wilder(tr)
    plus_dm_s = _wilder(plus_dm)
    minus_dm_s = _wilder(minus_dm)

    with np.errstate(divide="ignore", invalid="ignore"):
        plus_di = 100.0 * (plus_dm_s / tr_s)
        minus_di = 100.0 * (minus_dm_s / tr_s)
        dx_denom = plus_di + minus_di
        dx = np.where(dx_denom > 0, 100.0 * np.abs(plus_di - minus_di) / dx_denom, 0.0)

    # Final Wilder smoothing on DX → ADX
    valid_dx = dx[~np.isnan(dx)]
    if len(valid_dx) < period:
        return None
    adx_val = float(np.mean(valid_dx[:period]))
    for v in valid_dx[period:]:
        adx_val = (adx_val * (period - 1) + v) / period
    if math.isnan(adx_val) or math.isinf(adx_val):
        return None
    return adx_val


def choppiness_index(history: pd.DataFrame, period: int = 14) -> float | None:
    """Choppiness Index (Dreiss). 0~100.

    >61.8 → ranging / choppy. <38.2 → trending. Needs ``period + 1`` bars.
    """
    if period < 2:
        raise ValueError(f"period must be >= 2, got {period}")
    if len(history) < period + 1:
        return None
    high = history["high"].astype(float).values[-period - 1:]
    low = history["low"].astype(float).values[-period - 1:]
    close = history["close"].astype(float).values[-period - 1:]

    prev_close = close[:-1]
    tr = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    atr_sum = float(tr.sum())
    range_hi = float(high[1:].max())
    range_lo = float(low[1:].min())
    range_span = range_hi - range_lo
    if atr_sum <= 0 or range_span <= 0:
        return None
    ratio = atr_sum / range_span
    if ratio <= 0:
        return None
    ci = 100.0 * math.log10(ratio) / math.log10(period)
    if math.isnan(ci) or math.isinf(ci):
        return None
    return ci


def hurst_exponent(close: pd.Series, lookback: int = 100) -> float | None:
    """R/S Hurst exponent on log-returns. Returns ``None`` if too short.

    H > 0.5: persistent (trending). H < 0.5: anti-persistent (mean-reverting).
    H ≈ 0.5: random walk. Uses 4 sub-period sizes; truncates to multiples.
    """
    if lookback < 20:
        raise ValueError(f"lookback must be >= 20, got {lookback}")
    if len(close) < lookback:
        return None
    series = close.astype(float).values[-lookback:]
    log_returns = np.diff(np.log(np.clip(series, 1e-12, None)))
    n = len(log_returns)
    if n < 16:
        return None

    sub_sizes = [n // k for k in (10, 5, 2, 1) if n // k >= 8]
    if len(sub_sizes) < 2:
        return None

    rs_pairs = []
    for size in sub_sizes:
        n_chunks = n // size
        if n_chunks < 1 or size < 8:
            continue
        rs_vals = []
        for i in range(n_chunks):
            chunk = log_returns[i * size:(i + 1) * size]
            mean = chunk.mean()
            dev = chunk - mean
            cum = np.cumsum(dev)
            r = cum.max() - cum.min()
            s = chunk.std(ddof=0)
            if s > 0 and r > 0:
                rs_vals.append(r / s)
        if rs_vals:
            rs_pairs.append((size, float(np.mean(rs_vals))))

    if len(rs_pairs) < 2:
        return None
    log_sizes = np.log([p[0] for p in rs_pairs])
    log_rs = np.log([p[1] for p in rs_pairs])
    slope, _ = np.polyfit(log_sizes, log_rs, 1)
    if math.isnan(slope) or math.isinf(slope):
        return None
    return float(slope)
