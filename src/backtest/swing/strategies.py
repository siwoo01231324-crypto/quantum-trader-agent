"""Swing strategy candidates (S1-S5 + iter 3 variants) for issue #99.

Each strategy is a pure function:
  signal_fn(df: pd.DataFrame, **params) -> pd.Series  # 0 / +1 / -1 (long / short)

Stateful position tracking handled by run_strategy() in bench script.

Sources:
  S1: Moskowitz/Ooi/Pedersen 2012 JFE — time-series momentum
  S2: Donchian channel breakout (Turtle Trading, 1983)
  S2a: S2 + ATR(14) trailing stop (2*ATR distance)
  S2b: S2 + hard stop -1% / take-profit +7%
  S2c: S2 + vol-target position sizing (annualized vol target 15%)
  S3: Avellaneda/Lee 2010 + Wilder 1978 — EMA pullback + RSI
  S4: Funding rate carry (perpetual futures)
  S4a: S4 bidirectional (positive funding -> short, negative -> long)
  S5: BTC-ETH pairs trading (log-ratio mean reversion)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest.swing.atr import wilder_atr


def s1_tsmom(df: pd.DataFrame, lookback: int = 6) -> pd.Series:
    """S1: Time-series momentum 4h.

    Long if rolling N-bar return > 0, flat otherwise.
    Source: Moskowitz/Ooi/Pedersen 2012 JFE.
    """
    ret = df["close"].pct_change(lookback).shift(1)  # shift to avoid lookahead
    return (ret > 0).astype(int).rename("s1_signal")


def s2_donchian(
    df: pd.DataFrame,
    entry_lookback: int = 20,
    exit_lookback: int = 10,
) -> pd.Series:
    """S2: Donchian breakout 4h with 10-period exit.

    Long entry: close >= rolling_max(close, entry_lookback) (excluding current bar).
    Exit: close <= rolling_min(close, exit_lookback).
    Returns position series (sticky 0/1).
    """
    high_ent = df["close"].rolling(entry_lookback).max().shift(1)
    low_exit = df["close"].rolling(exit_lookback).min().shift(1)

    enter = df["close"] > high_ent
    exit_sig = df["close"] < low_exit

    pos = np.zeros(len(df), dtype=int)
    state = 0
    enter_arr = enter.fillna(False).to_numpy()
    exit_arr = exit_sig.fillna(False).to_numpy()
    for i in range(len(df)):
        if state == 0 and enter_arr[i]:
            state = 1
        elif state == 1 and exit_arr[i]:
            state = 0
        pos[i] = state
    return pd.Series(pos, index=df.index, name="s2_signal")


def s3_ema_pullback(
    df: pd.DataFrame,
    ema_trend: int = 200,
    rsi_lookback: int = 14,
    rsi_threshold: float = 30.0,
) -> pd.Series:
    """S3: EMA200 uptrend + RSI pullback.

    Long when close > EMA200 AND RSI(14) < 30. Exit when close crosses below EMA200.
    Source: Avellaneda/Lee 2010 + Wilder 1978.
    """
    ema = (
        df["close"]
        .ewm(span=ema_trend, adjust=False, min_periods=ema_trend)
        .mean()
        .shift(1)
    )

    # Wilder RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(
        alpha=1 / rsi_lookback, adjust=False, min_periods=rsi_lookback
    ).mean()
    avg_loss = loss.ewm(
        alpha=1 / rsi_lookback, adjust=False, min_periods=rsi_lookback
    ).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = (100 - 100 / (1 + rs)).shift(1)

    uptrend = df["close"] > ema
    oversold = rsi < rsi_threshold

    enter = uptrend & oversold
    exit_sig = df["close"] < ema  # trend break exit

    pos = np.zeros(len(df), dtype=int)
    state = 0
    enter_arr = enter.fillna(False).to_numpy()
    exit_arr = exit_sig.fillna(False).to_numpy()
    for i in range(len(df)):
        if state == 0 and enter_arr[i]:
            state = 1
        elif state == 1 and exit_arr[i]:
            state = 0
        pos[i] = state
    return pd.Series(pos, index=df.index, name="s3_signal")


def s4_funding_carry(
    df: pd.DataFrame,
    threshold_neg: float = -0.005e-2,
) -> pd.Series:
    """S4: Funding rate carry. Requires '_funding_rate' column on df.

    DATA_UNAVAILABLE fallback if column missing.
    """
    if "_funding_rate" not in df.columns:
        return pd.Series(0, index=df.index, name="s4_signal_unavailable")
    funding = df["_funding_rate"].shift(1)
    pos = (funding < threshold_neg).astype(int)
    return pos.rename("s4_signal")


def s5_pairs(
    btc_df: pd.DataFrame,
    eth_df: pd.DataFrame,
    lookback: int = 60,
    z_entry: float = 2.0,
    z_exit: float = 0.5,
) -> pd.DataFrame:
    """S5: BTC-ETH pairs trading.

    Returns DataFrame with columns ['btc_pos', 'eth_pos'] in {-1, 0, +1}.
    z = (log_ratio - mean) / std over rolling lookback.
    z > z_entry -> BTC short / ETH long. z < -z_entry -> reverse. |z| < z_exit -> flat.
    """
    aligned = pd.DataFrame(
        {"btc": btc_df["close"], "eth": eth_df["close"]}
    ).dropna()
    log_ratio = np.log(aligned["btc"] / aligned["eth"])
    mean = log_ratio.rolling(lookback).mean().shift(1)
    std = log_ratio.rolling(lookback).std().shift(1)
    z = ((log_ratio.shift(1) - mean) / std).fillna(0)

    btc_pos = np.zeros(len(aligned), dtype=int)
    eth_pos = np.zeros(len(aligned), dtype=int)
    state = 0  # 0 flat, 1 short BTC long ETH, -1 long BTC short ETH
    for i in range(len(aligned)):
        zi = z.iloc[i]
        if state == 0:
            if zi > z_entry:
                state = 1
            elif zi < -z_entry:
                state = -1
        else:
            if abs(zi) < z_exit:
                state = 0
        if state == 1:
            btc_pos[i] = -1
            eth_pos[i] = 1
        elif state == -1:
            btc_pos[i] = 1
            eth_pos[i] = -1
    return pd.DataFrame(
        {"btc_pos": btc_pos, "eth_pos": eth_pos}, index=aligned.index
    )


# -- Iter 3 variants ---------------------------------------------------------


def s2_donchian_atr_stop(
    df: pd.DataFrame,
    entry_lookback: int = 20,
    exit_lookback: int = 10,
    atr_period: int = 14,
    atr_multiplier: float = 2.0,
) -> pd.Series:
    """S2a: Donchian breakout + ATR(14) trailing stop at 2*ATR distance.

    Same entry/exit logic as S2, but adds a trailing stop: if price drops
    more than atr_multiplier * ATR below the trailing high since entry,
    the position is closed.
    """
    high_ent = df["close"].rolling(entry_lookback).max().shift(1)
    low_exit = df["close"].rolling(exit_lookback).min().shift(1)

    enter = df["close"] > high_ent
    exit_donchian = df["close"] < low_exit

    atr = wilder_atr(df, period=atr_period)

    enter_arr = enter.fillna(False).to_numpy()
    exit_arr = exit_donchian.fillna(False).to_numpy()
    close_arr = df["close"].to_numpy()
    atr_arr = atr.fillna(0.0).to_numpy()

    pos = np.zeros(len(df), dtype=int)
    state = 0
    trailing_high = 0.0

    for i in range(len(df)):
        if state == 0:
            if enter_arr[i]:
                state = 1
                trailing_high = close_arr[i]
        else:
            # Update trailing high
            if close_arr[i] > trailing_high:
                trailing_high = close_arr[i]
            # ATR trailing stop check
            stop_level = trailing_high - atr_multiplier * atr_arr[i]
            if close_arr[i] < stop_level or exit_arr[i]:
                state = 0
                trailing_high = 0.0
        pos[i] = state

    return pd.Series(pos, index=df.index, name="s2a_signal")


def s2_donchian_hard_rr(
    df: pd.DataFrame,
    entry_lookback: int = 20,
    exit_lookback: int = 10,
    stop_pct: float = 0.01,
    tp_pct: float = 0.07,
) -> pd.Series:
    """S2b: Donchian breakout + hard stop -1% / take-profit +7%.

    Entry via Donchian breakout. Exit when price moves stop_pct below entry
    price (stop-loss) or tp_pct above entry price (take-profit), or via
    Donchian exit channel.
    """
    high_ent = df["close"].rolling(entry_lookback).max().shift(1)
    low_exit = df["close"].rolling(exit_lookback).min().shift(1)

    enter = df["close"] > high_ent
    exit_donchian = df["close"] < low_exit

    enter_arr = enter.fillna(False).to_numpy()
    exit_arr = exit_donchian.fillna(False).to_numpy()
    close_arr = df["close"].to_numpy()

    pos = np.zeros(len(df), dtype=int)
    state = 0
    entry_price = 0.0

    for i in range(len(df)):
        if state == 0:
            if enter_arr[i]:
                state = 1
                entry_price = close_arr[i]
        else:
            pnl_pct = (close_arr[i] - entry_price) / entry_price
            if pnl_pct <= -stop_pct or pnl_pct >= tp_pct or exit_arr[i]:
                state = 0
                entry_price = 0.0
        pos[i] = state

    return pd.Series(pos, index=df.index, name="s2b_signal")


def s2_donchian_voltarget(
    df: pd.DataFrame,
    entry_lookback: int = 20,
    exit_lookback: int = 10,
    vol_target: float = 0.15,
    vol_lookback: int = 60,
) -> tuple[pd.Series, pd.Series]:
    """S2c: Donchian breakout + vol-target position sizing.

    Signal is same as S2 (binary 0/1). Position size is
    vol_target / realized_vol, capped at 1.0.

    Returns (signal, position_size) tuple. The bench script should multiply
    returns by position_size.

    Parameters
    ----------
    vol_target   : Annualized volatility target (default 0.15 = 15%).
    vol_lookback : Rolling window for realized vol estimation (default 60 bars).
    """
    # Base S2 signal
    signal = s2_donchian(df, entry_lookback=entry_lookback, exit_lookback=exit_lookback)

    # Realized vol: annualized from bar returns
    bar_ret = df["close"].pct_change()
    # 4h bars: ~6 bars/day * 365 days/year = 2190 bars/year
    bars_per_year = 6 * 365
    realized_vol = bar_ret.rolling(vol_lookback).std() * np.sqrt(bars_per_year)
    realized_vol = realized_vol.shift(1)  # causal

    # Position size = target / realized, capped at 1.0
    pos_size = (vol_target / realized_vol.replace(0, np.nan)).clip(upper=1.0).fillna(0.0)

    return signal.rename("s2c_signal"), pos_size.rename("s2c_pos_size")


def s2c_x_s4_composite(
    df: pd.DataFrame,
    entry_lookback: int = 20,
    exit_lookback: int = 10,
    threshold_neg: float = -0.005e-2,
    vol_target: float = 0.15,
    vol_lookback: int = 60,
) -> tuple[pd.Series, pd.Series]:
    """W3: S2c x S4 AND-gate composite.

    Long when:
      (a) Donchian breakout active (S2 sticky position == 1)
      AND
      (b) funding rate < threshold_neg (S4 long signal)
    Position size = vol-target / realized_vol (capped at 1.0).

    Returns (signal, position_size) tuple matching S2c interface.
    """
    # Reuse existing functions for clarity
    s2_pos = s2_donchian(df, entry_lookback, exit_lookback)  # 0/1 sticky
    s4_signal = s4_funding_carry(df, threshold_neg)  # 0/1 stateless

    # If funding data unavailable, S4 returns all zeros -> composite all zeros
    combined = (s2_pos.astype(int) & s4_signal.astype(int)).astype(int)

    # Vol-target sizing (same as S2c logic)
    bar_ret = df["close"].pct_change()
    bars_per_year = 6 * 365  # 4h bars: ~6 bars/day * 365 days/year = 2190
    realized_vol = bar_ret.rolling(vol_lookback).std() * np.sqrt(bars_per_year)
    realized_vol = realized_vol.shift(1)  # causal

    pos_size = (vol_target / realized_vol.replace(0, np.nan)).clip(upper=1.0).fillna(0.0)

    return combined.rename("w3_signal"), pos_size.rename("w3_pos_size")


def s4_funding_both(
    df: pd.DataFrame,
    threshold_pos: float = 0.0005,
    threshold_neg: float = -0.00005,
) -> pd.Series:
    """S4a: Bidirectional funding rate carry.

    Positive funding > threshold_pos -> short (-1): longs pay shorts.
    Negative funding < threshold_neg -> long (+1): shorts pay longs.
    Otherwise flat (0).

    Requires '_funding_rate' column on df.
    """
    if "_funding_rate" not in df.columns:
        return pd.Series(0, index=df.index, name="s4a_signal_unavailable")

    funding = df["_funding_rate"].shift(1)

    pos = pd.Series(0, index=df.index, dtype=int)
    pos = pos.where(~(funding > threshold_pos), -1)
    pos = pos.where(~(funding < threshold_neg), 1)

    return pos.rename("s4a_signal")
