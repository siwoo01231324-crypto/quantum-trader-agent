"""Append-tail-bar invariance check.

A factor is causal iff computing it on ``ohlcv[:-1]`` yields the same values
at indices ``0..N-2`` as computing it on the full ``ohlcv``. If appending one
future bar changes any earlier result, the factor is leaking the future.

This does NOT enforce lag-1 on decision-facing signals — that is the signal
function's own responsibility (see ``src/signals/.ai.md``).
"""
from __future__ import annotations

from typing import Any, Callable

import numpy as np
import pandas as pd


def _slice_inputs(ohlcv: pd.DataFrame, inputs: list[str], upto: int | None = None) -> dict[str, pd.Series]:
    frame = ohlcv if upto is None else ohlcv.iloc[:upto]
    return {col: frame[col] for col in inputs}


def _equal_with_nan(a: Any, b: Any) -> bool:
    """Elementwise equality that treats NaN==NaN and None==None as equal."""
    if isinstance(a, float) and isinstance(b, float):
        if np.isnan(a) and np.isnan(b):
            return True
        return a == b
    if a is None and b is None:
        return True
    if (a is None) != (b is None):
        return False
    try:
        if pd.isna(a) and pd.isna(b):
            return True
    except (TypeError, ValueError):
        pass
    return a == b


def _compare_series(short: pd.Series, full: pd.Series) -> list[int]:
    """Return indices where short differs from full[:-1]."""
    truncated = full.iloc[: len(short)]
    bad: list[int] = []
    if short.dtype.kind in "fi" and truncated.dtype.kind in "fi":
        diff_mask = ~(
            (short.to_numpy() == truncated.to_numpy())
            | (pd.isna(short).to_numpy() & pd.isna(truncated).to_numpy())
        )
        bad = list(np.where(diff_mask)[0])
    else:
        for i, (s_val, f_val) in enumerate(zip(short, truncated, strict=True)):
            if not _equal_with_nan(s_val, f_val):
                bad.append(i)
    return bad


def assert_no_lookahead(
    factor_func: Callable[..., pd.Series | pd.DataFrame],
    ohlcv: pd.DataFrame,
    *,
    inputs: list[str],
    **kwargs: Any,
) -> None:
    """Verify ``factor_func`` is causal.

    Computes the factor on ``ohlcv[:-1]`` and on the full ``ohlcv`` and asserts
    that values at indices ``0..N-2`` are identical. Raises ``AssertionError``
    with a human-readable diagnostic if any bar changed.
    """
    if len(ohlcv) < 2:
        raise ValueError("need at least 2 bars to run the append-tail-bar guard")

    short_out = factor_func(**_slice_inputs(ohlcv, inputs, upto=len(ohlcv) - 1), **kwargs)
    full_out = factor_func(**_slice_inputs(ohlcv, inputs, upto=None), **kwargs)

    if isinstance(short_out, pd.DataFrame):
        if not isinstance(full_out, pd.DataFrame):
            raise AssertionError("factor output types inconsistent between short/full runs")
        mismatches: list[tuple[str, list[int]]] = []
        for col in short_out.columns:
            bad = _compare_series(short_out[col], full_out[col])
            if bad:
                mismatches.append((col, bad))
        if mismatches:
            raise AssertionError(
                f"lookahead detected: columns differ at bars {mismatches}"
            )
        return

    bad = _compare_series(short_out, full_out)
    if bad:
        raise AssertionError(f"lookahead detected at bars {bad}")
