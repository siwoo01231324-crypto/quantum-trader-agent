"""Regime-switching strategy router for pre-registered variant matrix.

Routes R0-R5 variants for issue #173 bench. Each variant defines
how to select between S2c (Donchian vol-target) and S4 (funding carry)
based on regime classification.

Variant Matrix (frozen, issue #173):
    R0: S2c always (baseline, no regime)
    R1: S4 always (baseline 2)
    R2: HMM-2state on returns (vol regime) -> high-vol=S4, low-vol=S2c
    R3: HMM-3state (returns + funding) -> bull=S2c, bear/sideways=S4, crash=flat
    R4: Threshold-based switch (30d return > 0 = S2c, funding < 0 = S4)
    R5: Ensemble vote (R2 + R3 + R4 majority)
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from src.backtest.swing.strategies import s2_donchian_voltarget, s4_funding_carry
from src.ml.regime.hmm import GaussianHMMRegime
from src.ml.regime.threshold import ThresholdRegime


def _run_s2c(
    df: pd.DataFrame,
    s2c_params: dict[str, Any] | None = None,
) -> tuple[pd.Series, pd.Series]:
    params = s2c_params or {}
    return s2_donchian_voltarget(df, **params)


def _run_s4(
    df: pd.DataFrame,
    s4_params: dict[str, Any] | None = None,
) -> pd.Series:
    params = s4_params or {}
    return s4_funding_carry(df, **params)


def route_r0(
    df: pd.DataFrame,
    **kwargs: Any,
) -> tuple[pd.Series, pd.Series | None]:
    """R0: S2c always (baseline, no regime switching)."""
    signal, pos_size = _run_s2c(df, kwargs.get("s2c_params"))
    return signal, pos_size


def route_r1(
    df: pd.DataFrame,
    **kwargs: Any,
) -> tuple[pd.Series, pd.Series | None]:
    """R1: S4 always (baseline 2, no regime switching)."""
    signal = _run_s4(df, kwargs.get("s4_params"))
    return signal, None


def route_r2(
    df: pd.DataFrame,
    hmm_lookback: int = 500,
    **kwargs: Any,
) -> tuple[pd.Series, pd.Series | None]:
    """R2: HMM-2state vol regime -> high-vol=S4, low-vol=S2c."""
    returns = df["close"].pct_change().dropna()

    s2c_signal, s2c_pos = _run_s2c(df, kwargs.get("s2c_params"))
    s4_signal = _run_s4(df, kwargs.get("s4_params"))

    model = GaussianHMMRegime(n_components=2, random_state=42)
    fit_data = returns.iloc[-hmm_lookback:] if len(returns) > hmm_lookback else returns
    result = model.fit_predict(fit_data)

    full_result = model.predict(returns)
    states = pd.Series(
        full_result.states, index=returns.index, dtype=int
    ).reindex(df.index, method="ffill").fillna(0).astype(int)

    low_vol_state = int(np.argmin(result.variances))

    combined_signal = pd.Series(0, index=df.index, dtype=int)
    combined_pos = pd.Series(0.0, index=df.index)

    low_vol_mask = states == low_vol_state
    combined_signal[low_vol_mask] = s2c_signal[low_vol_mask]
    combined_pos[low_vol_mask] = s2c_pos[low_vol_mask]

    high_vol_mask = ~low_vol_mask
    combined_signal[high_vol_mask] = s4_signal[high_vol_mask]

    return combined_signal.rename("r2_signal"), combined_pos.rename("r2_pos_size")


def route_r3(
    df: pd.DataFrame,
    hmm_lookback: int = 500,
    **kwargs: Any,
) -> tuple[pd.Series, pd.Series | None]:
    """R3: HMM-3state -> bull=S2c, bear/sideways=S4, crash=flat."""
    returns = df["close"].pct_change().dropna()

    s2c_signal, s2c_pos = _run_s2c(df, kwargs.get("s2c_params"))
    s4_signal = _run_s4(df, kwargs.get("s4_params"))

    model = GaussianHMMRegime(n_components=3, random_state=42)
    fit_data = returns.iloc[-hmm_lookback:] if len(returns) > hmm_lookback else returns
    result = model.fit_predict(fit_data)

    full_result = model.predict(returns)
    states = pd.Series(
        full_result.states, index=returns.index, dtype=int
    ).reindex(df.index, method="ffill").fillna(0).astype(int)

    sorted_by_vol = np.argsort(result.variances.flatten())
    low_vol_state = sorted_by_vol[0]
    mid_vol_state = sorted_by_vol[1]
    high_vol_state = sorted_by_vol[2]

    combined_signal = pd.Series(0, index=df.index, dtype=int)
    combined_pos = pd.Series(0.0, index=df.index)

    bull_mask = states == low_vol_state
    combined_signal[bull_mask] = s2c_signal[bull_mask]
    combined_pos[bull_mask] = s2c_pos[bull_mask]

    bear_mask = states == mid_vol_state
    combined_signal[bear_mask] = s4_signal[bear_mask]

    return combined_signal.rename("r3_signal"), combined_pos.rename("r3_pos_size")


def route_r4(
    df: pd.DataFrame,
    return_lookback: int = 180,
    **kwargs: Any,
) -> tuple[pd.Series, pd.Series | None]:
    """R4: Threshold-based switch (return > 0 = S2c, funding < 0 = S4)."""
    s2c_signal, s2c_pos = _run_s2c(df, kwargs.get("s2c_params"))
    s4_signal = _run_s4(df, kwargs.get("s4_params"))

    funding = df.get("_funding_rate")
    classifier = ThresholdRegime(
        return_lookback=return_lookback,
        return_threshold=0.0,
        funding_threshold=0.0,
    )
    result = classifier.classify(df["close"], funding_rate=funding)
    regime_states = pd.Series(result.states, index=df.index)

    combined_signal = pd.Series(0, index=df.index, dtype=int)
    combined_pos = pd.Series(0.0, index=df.index)

    bullish_mask = regime_states == 0
    combined_signal[bullish_mask] = s2c_signal[bullish_mask]
    combined_pos[bullish_mask] = s2c_pos[bullish_mask]

    funding_neg_mask = regime_states == 1
    combined_signal[funding_neg_mask] = s4_signal[funding_neg_mask]

    return combined_signal.rename("r4_signal"), combined_pos.rename("r4_pos_size")


def route_r5(
    df: pd.DataFrame,
    hmm_lookback: int = 500,
    return_lookback: int = 180,
    **kwargs: Any,
) -> tuple[pd.Series, pd.Series | None]:
    """R5: Ensemble vote (R2 + R3 + R4 majority).

    At each bar, if >= 2 of {R2, R3, R4} choose S2c, use S2c.
    Otherwise use S4.
    """
    s2c_signal, s2c_pos = _run_s2c(df, kwargs.get("s2c_params"))
    s4_signal = _run_s4(df, kwargs.get("s4_params"))

    returns = df["close"].pct_change().dropna()

    # R2 vote: low_vol -> S2c (1), high_vol -> S4 (0)
    hmm2 = GaussianHMMRegime(n_components=2, random_state=42)
    fit_data = returns.iloc[-hmm_lookback:] if len(returns) > hmm_lookback else returns
    r2_result = hmm2.fit_predict(fit_data)
    r2_full = hmm2.predict(returns)
    r2_states = pd.Series(
        r2_full.states, index=returns.index
    ).reindex(df.index, method="ffill").fillna(0).astype(int)
    low_vol_2 = int(np.argmin(r2_result.variances))
    vote_r2 = (r2_states == low_vol_2).astype(int)

    # R3 vote: low_vol -> S2c (1), else -> S4 (0)
    hmm3 = GaussianHMMRegime(n_components=3, random_state=42)
    r3_result = hmm3.fit_predict(fit_data)
    r3_full = hmm3.predict(returns)
    r3_states = pd.Series(
        r3_full.states, index=returns.index
    ).reindex(df.index, method="ffill").fillna(0).astype(int)
    low_vol_3 = int(np.argsort(r3_result.variances.flatten())[0])
    vote_r3 = (r3_states == low_vol_3).astype(int)

    # R4 vote: bullish -> S2c (1), else -> S4 (0)
    funding = df.get("_funding_rate")
    classifier = ThresholdRegime(
        return_lookback=return_lookback,
        return_threshold=0.0,
        funding_threshold=0.0,
    )
    t_result = classifier.classify(df["close"], funding_rate=funding)
    vote_r4 = pd.Series(
        (t_result.states == 0).astype(int), index=df.index
    )

    # Majority: >= 2 votes for S2c
    total_votes = vote_r2 + vote_r3 + vote_r4
    use_s2c = total_votes >= 2

    combined_signal = pd.Series(0, index=df.index, dtype=int)
    combined_pos = pd.Series(0.0, index=df.index)

    combined_signal[use_s2c] = s2c_signal[use_s2c]
    combined_pos[use_s2c] = s2c_pos[use_s2c]
    combined_signal[~use_s2c] = s4_signal[~use_s2c]

    return combined_signal.rename("r5_signal"), combined_pos.rename("r5_pos_size")


VARIANT_REGISTRY: dict[str, Any] = {
    "R0": {"fn": route_r0, "desc": "S2c always (baseline)"},
    "R1": {"fn": route_r1, "desc": "S4 always (baseline 2)"},
    "R2": {"fn": route_r2, "desc": "HMM-2state vol regime"},
    "R3": {"fn": route_r3, "desc": "HMM-3state (bull/bear/crash)"},
    "R4": {"fn": route_r4, "desc": "Threshold-based switch"},
    "R5": {"fn": route_r5, "desc": "Ensemble vote (R2+R3+R4)"},
}
