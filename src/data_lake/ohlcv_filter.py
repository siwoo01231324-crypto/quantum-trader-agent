"""OHLCV noise-bar filter and sidecar marker (schema-preserving sidecar pattern).

Sidecar columns (_is_vi_halt, _is_single_price, _volume_zero) are extra and
intentionally outside OHLCV_SCHEMA. validate_schema() rejects extra columns,
so callers must NOT pass a marked DataFrame to validate_schema('ohlcv', ...).
Use filter_noise_bars() on a clean OHLCV DataFrame to get a schema-compliant result.
"""
from __future__ import annotations

import pandas as pd


def filter_noise_bars(
    df: pd.DataFrame,
    *,
    exclude_vi: bool = True,
    exclude_single_price: bool = True,
    exclude_zero_volume: bool = True,
) -> pd.DataFrame:
    """Return a filtered copy of df with noise bars removed.

    The returned DataFrame preserves the original OHLCV columns only.
    """
    marked = mark_noise_bars(df)
    mask = pd.Series(True, index=df.index)
    if exclude_zero_volume:
        mask &= ~marked["_volume_zero"]
    if exclude_vi:
        mask &= ~marked["_is_vi_halt"]
    if exclude_single_price:
        mask &= ~marked["_is_single_price"]
    return df[mask].copy()


def mark_noise_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Return df with three sidecar bool columns appended.

    Sidecar columns:
        _is_vi_halt      — True if bar is a VI-triggered halt (always False until
                           KIS API exposes the flag)
        _is_single_price — True if bar is a single-price auction bar (always False
                           until KIS API exposes the flag)
        _volume_zero     — True where volume == 0
    """
    out = df.copy()
    out["_is_vi_halt"] = False
    out["_is_single_price"] = False
    out["_volume_zero"] = out["volume"] == 0
    return out
