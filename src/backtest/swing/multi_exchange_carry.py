"""Multi-exchange funding carry strategies F0-F5 (issue #174).

Strategy functions follow the same pure-function interface as strategies.py:
    signal_fn(df, **params) -> pd.Series  # position: 0 / +1 / -1

Each function requires specific columns on the input DataFrame:
    F0/F1: _funding_rate_binance  (or _funding_rate fallback from #172)
    F2/F4: _funding_rate_binance, _funding_rate_okx
    F3:    _funding_rate_binance, _funding_rate_okx, _funding_rate_bybit
    F5:    ensemble of F0, F2, F4

lake/funding_rate partition convention (issue #174):
    lake/funding_rate/exchange=binance/symbol=BTCUSDT/part-0.parquet
    lake/funding_rate/exchange=okx/symbol=BTCUSDT/part-0.parquet
    lake/funding_rate/exchange=bybit/symbol=BTCUSDT/part-0.parquet

Data-unavailable: if required columns are missing, returns a signal named
    f"{strategy_id}_signal_unavailable" with all zeros (bench scripts detect this).
"""
from __future__ import annotations

import pandas as pd

_UNAVAIL_SUFFIX = "_signal_unavailable"
_DEFAULT_THRESHOLD_NEG = -0.005e-2  # -0.005% per 8h (matches S4 baseline)
_DEFAULT_SPREAD_THRESHOLD = 0.001e-2  # 0.001% spread to trigger arb


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _unavailable(name: str, index: pd.Index) -> pd.Series:
    return pd.Series(0, index=index, name=name + _UNAVAIL_SUFFIX)


def _check_cols(df: pd.DataFrame, required: list[str]) -> list[str]:
    return [c for c in required if c not in df.columns]


# ---------------------------------------------------------------------------
# F0 — Baseline single-exchange (mirrors S4 from strategies.py)
# ---------------------------------------------------------------------------

def f0_baseline(
    df: pd.DataFrame,
    threshold_neg: float = _DEFAULT_THRESHOLD_NEG,
) -> pd.Series:
    """F0: Binance-only funding carry (8h rebalance). Reproduces S4 (#172 baseline).

    Long when funding_rate < threshold_neg (negative funding → long side gets paid).
    Requires column: _funding_rate_binance (or _funding_rate as fallback).
    """
    col = "_funding_rate_binance" if "_funding_rate_binance" in df.columns else "_funding_rate"
    if col not in df.columns:
        return _unavailable("f0_baseline", df.index)

    funding = df[col].shift(1)
    return (funding < threshold_neg).astype(int).rename("f0_baseline_signal")


# ---------------------------------------------------------------------------
# F1 — Higher-frequency rebalance (1h signal, same Binance data)
# ---------------------------------------------------------------------------

def f1_hourly_rebalance(
    df: pd.DataFrame,
    threshold_neg: float = _DEFAULT_THRESHOLD_NEG,
) -> pd.Series:
    """F1: F0 with 1h bar data.

    Identical logic to F0 but intended for 1h OHLCV bars to increase rebalance frequency.
    At 1h resolution, each 8h funding event spans 8 bars → more granular entry/exit.
    Requires column: _funding_rate_binance (or _funding_rate fallback).
    """
    col = "_funding_rate_binance" if "_funding_rate_binance" in df.columns else "_funding_rate"
    if col not in df.columns:
        return _unavailable("f1_hourly_rebalance", df.index)

    # Forward-fill funding rate across intra-period 1h bars (funding updates every 8h)
    funding = df[col].ffill().shift(1)
    return (funding < threshold_neg).astype(int).rename("f1_hourly_rebalance_signal")


# ---------------------------------------------------------------------------
# F2 — Binance-OKX spread arbitrage
# ---------------------------------------------------------------------------

def f2_binance_okx_spread(
    df: pd.DataFrame,
    spread_threshold: float = _DEFAULT_SPREAD_THRESHOLD,
) -> pd.Series:
    """F2: Enter when Binance funding < OKX funding by spread_threshold.

    Market-neutral: long Binance perpetual (receive negative funding) while
    short OKX perpetual (pay the opposing side). Net: collect spread.

    Signal +1 when binance_rate - okx_rate < -spread_threshold
        (Binance rate significantly more negative → long Binance side pays less / receives more)
    Signal 0 otherwise (spread too narrow to cover costs).

    Requires columns: _funding_rate_binance, _funding_rate_okx
    """
    missing = _check_cols(df, ["_funding_rate_binance", "_funding_rate_okx"])
    if missing:
        return _unavailable("f2_binance_okx_spread", df.index)

    spread = (df["_funding_rate_binance"] - df["_funding_rate_okx"]).shift(1)
    return (spread < -spread_threshold).astype(int).rename("f2_binance_okx_spread_signal")


# ---------------------------------------------------------------------------
# F3 — Three-exchange best spread
# ---------------------------------------------------------------------------

def f3_three_exchange(
    df: pd.DataFrame,
    spread_threshold: float = _DEFAULT_SPREAD_THRESHOLD,
) -> pd.Series:
    """F3: Select the max cross-exchange spread among Binance, OKX, Bybit.

    Compute pairwise spreads (BN-OKX, BN-BYBIT, OKX-BYBIT). Enter long/short
    on the pair with the widest spread exceeding threshold.

    Signal encoding (simplified for bench):
        +1 = at least one pair exceeds spread_threshold
        0  = no qualifying spread

    Requires columns: _funding_rate_binance, _funding_rate_okx, _funding_rate_bybit
    """
    missing = _check_cols(df, ["_funding_rate_binance", "_funding_rate_okx", "_funding_rate_bybit"])
    if missing:
        return _unavailable("f3_three_exchange", df.index)

    bn = df["_funding_rate_binance"].shift(1)
    okx = df["_funding_rate_okx"].shift(1)
    bybit = df["_funding_rate_bybit"].shift(1)

    spread_bn_okx = (bn - okx).abs()
    spread_bn_bybit = (bn - bybit).abs()
    spread_okx_bybit = (okx - bybit).abs()

    max_spread = pd.concat([spread_bn_okx, spread_bn_bybit, spread_okx_bybit], axis=1).max(axis=1)
    return (max_spread > spread_threshold).astype(int).rename("f3_three_exchange_signal")


# ---------------------------------------------------------------------------
# F4 — F2 + 1h rebalance
# ---------------------------------------------------------------------------

def f4_binance_okx_hourly(
    df: pd.DataFrame,
    spread_threshold: float = _DEFAULT_SPREAD_THRESHOLD,
) -> pd.Series:
    """F4: F2 logic on 1h bars with forward-filled funding rates.

    Combines higher rebalance frequency (F1) with cross-exchange spread (F2).
    Requires columns: _funding_rate_binance, _funding_rate_okx
    """
    missing = _check_cols(df, ["_funding_rate_binance", "_funding_rate_okx"])
    if missing:
        return _unavailable("f4_binance_okx_hourly", df.index)

    bn = df["_funding_rate_binance"].ffill().shift(1)
    okx = df["_funding_rate_okx"].ffill().shift(1)
    spread = bn - okx
    return (spread < -spread_threshold).astype(int).rename("f4_binance_okx_hourly_signal")


# ---------------------------------------------------------------------------
# F5 — Ensemble of F0, F2, F4
# ---------------------------------------------------------------------------

def f5_ensemble(
    df: pd.DataFrame,
    w0: float = 0.4,
    w2: float = 0.4,
    w4: float = 0.2,
    threshold_neg: float = _DEFAULT_THRESHOLD_NEG,
    spread_threshold: float = _DEFAULT_SPREAD_THRESHOLD,
) -> pd.Series:
    """F5: Weighted ensemble of F0, F2, F4 signals.

    Weighted vote: signal = 1 if weighted_sum >= 0.5, else 0.
    Weights default: F0=0.4, F2=0.4, F4=0.2.

    Requires at minimum _funding_rate_binance; F2/F4 gracefully degrade to 0
    if OKX columns are absent (reduces ensemble to F0-only weighted).
    """
    s0 = f0_baseline(df, threshold_neg=threshold_neg)
    s2 = f2_binance_okx_spread(df, spread_threshold=spread_threshold)
    s4 = f4_binance_okx_hourly(df, spread_threshold=spread_threshold)

    # Replace unavailable signals with zeros
    if _UNAVAIL_SUFFIX in s0.name:
        s0 = pd.Series(0, index=df.index)
    if _UNAVAIL_SUFFIX in s2.name:
        s2 = pd.Series(0, index=df.index)
    if _UNAVAIL_SUFFIX in s4.name:
        s4 = pd.Series(0, index=df.index)

    weighted = w0 * s0 + w2 * s2 + w4 * s4
    return (weighted >= 0.5).astype(int).rename("f5_ensemble_signal")


# ---------------------------------------------------------------------------
# Registry for bench scripts
# ---------------------------------------------------------------------------

VARIANT_REGISTRY: dict[str, object] = {
    "F0": f0_baseline,
    "F1": f1_hourly_rebalance,
    "F2": f2_binance_okx_spread,
    "F3": f3_three_exchange,
    "F4": f4_binance_okx_hourly,
    "F5": f5_ensemble,
}
