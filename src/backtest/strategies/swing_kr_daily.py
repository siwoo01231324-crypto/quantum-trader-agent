"""KRX swing strategies on daily bars (long-only, 5y backtest target).

Designed for daily OHLCV from FinanceDataReader. All strategies are pure
signal functions: `(df: DataFrame) -> Series[int]` where signal = 0 (flat)
or 1 (long). No leverage, no shorts (KOSPI retail constraint).

Strategies (researched 2026-05-06 from Korean YouTube traders + academic
papers + our docs/background/):

    1. momo_kis_daily       — Daily RSI divergence (옵션 A 일봉 변형 of momo-kis-v1)
    2. swing_bb_macd        — Bollinger lower rebound + MACD confirmation
                              (78% win rate variant per QuantifiedStrategies)
    3. swing_adx_ma         — ADX(14)>25 + 5/20 EMA golden cross + ATR trailing
                              (Sharpe 0.79 @ S&P500 baseline)
    4. swing_tsmom_12_1     — Moskowitz/Ooi/Pedersen 12-1 month momentum
                              (Sharpe 1.31 multi-asset, 1965-2009)

All functions:
- Use shift(1) on signal source columns to prevent look-ahead bias
- Return long-only signals (0/1)
- Include `_strategy_id` Series.name for downstream tracking

References:
- docs/background/44-time-series-momentum-crypto.md (TSMOM 학술)
- docs/background/45-donchian-breakout-turtle.md (breakout 패턴)
- web research (#XXX): Bollinger+MACD, ADX+MA combinations
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Indicator helpers (no external TA library — keep dependency surface small)
# ---------------------------------------------------------------------------

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    return rsi


def _macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
          ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """MACD line, signal line, histogram."""
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0
               ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Bollinger middle (SMA), upper (+nσ), lower (-nσ)."""
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    return mid, upper, lower


def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR. Requires high/low/close columns."""
    high, low, close = df["high"], df["low"], df["close"]
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def _adx(df: pd.DataFrame, period: int = 14
         ) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Directional Movement: returns ADX, +DI, -DI."""
    high, low, close = df["high"], df["low"], df["close"]
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr_smoothed = tr.ewm(alpha=1 / period, adjust=False).mean()

    plus_di = 100 * (plus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_smoothed)
    minus_di = 100 * (minus_dm.ewm(alpha=1 / period, adjust=False).mean() / atr_smoothed)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()
    return adx, plus_di, minus_di


# ---------------------------------------------------------------------------
# Strategy 1 — momo_kis_daily  (RSI divergence on daily)
# ---------------------------------------------------------------------------

def momo_kis_daily(
    df: pd.DataFrame,
    *,
    rsi_period: int = 14,
    rsi_oversold: float = 30.0,
    rsi_overbought: float = 65.0,
    lookback: int = 20,
    max_hold_bars: int = 30,
    stop_loss_pct: float = 0.08,
) -> pd.Series:
    """RSI divergence on daily bars — entry on bullish divergence, exit on
    overbought.

    Bullish divergence: price makes lower low while RSI makes higher low.
    The classic mean-reversion trigger; a daily timeframe variant of the
    intraday momo-kis-v1 strategy.

    Entry (signal=1):
        - rsi < oversold within last `lookback` bars
        - current bar's close < recent low while RSI > recent RSI low
          (price down, RSI up = bullish divergence)
    Exit (signal=0):
        - rsi crosses above `overbought`
    """
    close = df["close"]
    rsi = _rsi(close, rsi_period).shift(1)
    price_low = close.shift(1).rolling(lookback).min()
    rsi_low = rsi.rolling(lookback).min()

    bullish_div = (
        (close.shift(1) <= price_low * 1.005)
        & (rsi > rsi_low * 1.05)
        & (rsi.shift(2) < rsi_oversold)
    )
    overbought = rsi > rsi_overbought

    signal = pd.Series(0, index=df.index, dtype=int)
    in_position = False
    bars_held = 0
    entry_price = 0.0
    for i in range(len(df)):
        c = float(close.iloc[i])
        if not in_position:
            if bool(bullish_div.iloc[i]):
                in_position = True
                bars_held = 0
                entry_price = c
        else:
            bars_held += 1
            stop_hit = c < entry_price * (1 - stop_loss_pct)
            if bool(overbought.iloc[i]) or bars_held >= max_hold_bars or stop_hit:
                in_position = False
        signal.iloc[i] = 1 if in_position else 0
    signal.name = "momo_kis_daily"
    return signal


# ---------------------------------------------------------------------------
# Strategy 2 — swing_bb_macd  (Bollinger lower rebound + MACD confirmation)
# ---------------------------------------------------------------------------

def swing_bb_macd(
    df: pd.DataFrame,
    *,
    bb_period: int = 20,
    bb_std: float = 2.0,
    macd_fast: int = 12,
    macd_slow: int = 26,
    macd_signal: int = 9,
    macd_lookback: int = 3,
) -> pd.Series:
    """Bollinger lower-band rebound confirmed by MACD bullish crossover.

    Entry (signal=1) when ALL true:
        1. Prev close < BB lower (touched lower band)
        2. Current close > BB lower (rebounded inside band)
        3. MACD line crossed above signal within last `macd_lookback` bars
        4. MACD histogram > 0 currently

    Exit (signal=0) when:
        - close crosses above BB upper (full take profit)
        - OR close crosses below BB middle minus 2*std (stop)
    """
    close = df["close"]
    mid, upper, lower = _bollinger(close, bb_period, bb_std)
    mid, upper, lower = mid.shift(1), upper.shift(1), lower.shift(1)
    macd_line, signal_line, hist = _macd(close, macd_fast, macd_slow, macd_signal)
    macd_line, signal_line, hist = macd_line.shift(1), signal_line.shift(1), hist.shift(1)

    # MACD bullish crossover within last `macd_lookback` bars
    macd_cross = (macd_line > signal_line) & (
        macd_line.shift(1) <= signal_line.shift(1)
    )
    macd_cross_recent = macd_cross.rolling(macd_lookback).max().fillna(0).astype(bool)

    # Lower-band rebound: prev close was below lower, current is above
    lower_rebound = (close.shift(2) < lower.shift(1)) & (close.shift(1) > lower)

    entry = lower_rebound & macd_cross_recent & (hist > 0)
    exit_above_upper = close.shift(1) >= upper
    # Stop-loss: close drops below 2*std under middle (very oversold)
    stop_below_lower = close.shift(1) < (mid - 2 * (mid - lower))

    signal = pd.Series(0, index=df.index, dtype=int)
    in_position = False
    for i in range(len(df)):
        if not in_position and bool(entry.iloc[i]):
            in_position = True
        elif in_position and (
            bool(exit_above_upper.iloc[i]) or bool(stop_below_lower.iloc[i])
        ):
            in_position = False
        signal.iloc[i] = 1 if in_position else 0
    signal.name = "swing_bb_macd"
    return signal


# ---------------------------------------------------------------------------
# Strategy 3 — swing_adx_ma  (ADX-filtered MA cross + ATR trailing stop)
# ---------------------------------------------------------------------------

def swing_adx_ma(
    df: pd.DataFrame,
    *,
    fast_ema: int = 5,
    slow_ema: int = 20,
    adx_period: int = 14,
    adx_threshold: float = 25.0,
    atr_period: int = 14,
    atr_mult: float = 2.0,
    max_hold_bars: int = 20,
) -> pd.Series:
    """5/20 EMA golden cross filtered by ADX > 25, exit on ATR trailing stop.

    Entry (signal=1) when ALL true:
        1. EMA(fast) crosses above EMA(slow) on prev bar
        2. ADX > threshold (and rising vs 3 bars ago)
        3. +DI > -DI

    Exit (signal=0) when ANY:
        - close drops below trailing stop (highest_close - atr_mult × ATR)
        - -DI crosses above +DI
        - position held for `max_hold_bars`
    """
    close = df["close"]
    ema_fast = close.ewm(span=fast_ema, adjust=False).mean().shift(1)
    ema_slow = close.ewm(span=slow_ema, adjust=False).mean().shift(1)
    golden_cross = (ema_fast > ema_slow) & (ema_fast.shift(1) <= ema_slow.shift(1))

    adx, plus_di, minus_di = _adx(df, adx_period)
    adx, plus_di, minus_di = adx.shift(1), plus_di.shift(1), minus_di.shift(1)
    adx_rising = adx > adx.shift(3)
    di_bullish = plus_di > minus_di
    di_bearish = minus_di > plus_di
    atr = _atr(df, atr_period).shift(1)

    entry = golden_cross & (adx > adx_threshold) & adx_rising & di_bullish

    signal = pd.Series(0, index=df.index, dtype=int)
    in_position = False
    bars_held = 0
    highest_close = 0.0
    for i in range(len(df)):
        c = float(close.iloc[i])
        if not in_position:
            if bool(entry.iloc[i]):
                in_position = True
                bars_held = 0
                highest_close = c
            signal.iloc[i] = 0
            continue

        # In position: update trailing peak + check exits
        bars_held += 1
        highest_close = max(highest_close, c)
        atr_v = atr.iloc[i] if not pd.isna(atr.iloc[i]) else 0
        trail = highest_close - atr_mult * atr_v
        exit_trail = c < trail
        exit_di = bool(di_bearish.iloc[i])
        exit_time = bars_held >= max_hold_bars
        if exit_trail or exit_di or exit_time:
            in_position = False
            signal.iloc[i] = 0
        else:
            signal.iloc[i] = 1
    signal.name = "swing_adx_ma"
    return signal


# ---------------------------------------------------------------------------
# Strategy 4 — swing_tsmom_12_1  (12-1 month time-series momentum)
# ---------------------------------------------------------------------------

def swing_tsmom_12_1(
    df: pd.DataFrame,
    *,
    long_lookback: int = 252,   # 12 months × 21 trading days
    skip_lookback: int = 21,    # 1 month
    rebal_freq: int = 5,        # weekly rebalance (5 trading days)
    drawdown_guard_pct: float = 0.15,
) -> pd.Series:
    """Moskowitz/Ooi/Pedersen (2012) 12-1 month time-series momentum,
    weekly-rebalanced, with 15% drawdown circuit breaker (Daniel & Moskowitz
    2016 momentum-crash guard).

    Signal = 1 (long) if (close[0]/close[-252] - close[0]/close[-21]) > 0,
    where -252 ≈ 12 months ago and -21 ≈ 1 month ago. Skip the most recent
    month to remove short-term reversal bias.

    Drawdown guard: if (close - max_close_during_position) / max < -15%,
    force exit.

    Rebalanced every `rebal_freq` bars (default weekly = 5 trading days).
    """
    close = df["close"]
    if len(df) < long_lookback + skip_lookback + 5:
        return pd.Series(0, index=df.index, dtype=int, name="swing_tsmom_12_1")

    r_12 = close / close.shift(long_lookback) - 1
    r_1 = close / close.shift(skip_lookback) - 1
    r_12_1 = (r_12 - r_1).shift(1)

    # Long if r_12_1 > 0, but only update on rebalance days.
    raw_long = (r_12_1 > 0).fillna(False).to_numpy()
    rebal_mask = (np.arange(len(df)) % rebal_freq) == 0

    signal = pd.Series(0, index=df.index, dtype=int)
    in_position = False
    entry_peak = 0.0
    for i in range(len(df)):
        c = float(close.iloc[i])
        if rebal_mask[i]:
            wanted = bool(raw_long[i])
            if wanted and not in_position:
                in_position = True
                entry_peak = c
            elif not wanted and in_position:
                in_position = False

        if in_position:
            entry_peak = max(entry_peak, c)
            dd = (c - entry_peak) / entry_peak if entry_peak > 0 else 0
            if dd <= -drawdown_guard_pct:
                in_position = False  # crash guard
        signal.iloc[i] = 1 if in_position else 0
    signal.name = "swing_tsmom_12_1"
    return signal


# ---------------------------------------------------------------------------
# Strategy registry — for batch backtest
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: dict[str, dict] = {
    "momo_kis_daily": {
        "fn": momo_kis_daily,
        "desc": "Daily RSI divergence (옵션 A — momo-kis-v1 일봉 변형)",
    },
    "swing_bb_macd": {
        "fn": swing_bb_macd,
        "desc": "Bollinger lower rebound + MACD bullish crossover",
    },
    "swing_adx_ma": {
        "fn": swing_adx_ma,
        "desc": "5/20 EMA cross + ADX>25 filter + ATR trailing stop",
    },
    "swing_tsmom_12_1": {
        "fn": swing_tsmom_12_1,
        "desc": "12-1 month time-series momentum, weekly rebal",
    },
}
