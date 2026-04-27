"""Multi-timeframe alignment.

Reference: ``docs/background/41-multi-tf-fractal-trading.md`` §4.1.

Resamples 1-minute close/volume to a higher timeframe, computes VWMA on
the higher timeframe, and asserts that the higher-TF close exceeds its
VWMA (bullish alignment). The result is then forward-filled back to
the original 1-minute index. Resample uses ``label='right',
closed='right'`` so the higher-TF bar at time ``T`` only contains the
1-minute bars in ``(T - higher_tf, T]`` — strictly causal.
"""
from __future__ import annotations

import pandas as pd

from src.features.vwma import vwma


def multi_tf_alignment(
    close_1m: pd.Series,
    volume_1m: pd.Series,
    higher_tf: str = "1h",
    vwma_window: int = 100,
) -> pd.Series:
    """Higher-timeframe VWMA bullish alignment indicator.

    Parameters
    ----------
    close_1m:
        1-minute close prices, DatetimeIndex.
    volume_1m:
        1-minute volumes, same index.
    higher_tf:
        Pandas offset alias for the higher timeframe (``"1h"``,
        ``"15min"``, ``"4h"``, ``"1D"``, …).
    vwma_window:
        VWMA window applied on the higher-timeframe series.

    Returns
    -------
    pd.Series[bool]
        Indexed identically to ``close_1m``. ``True`` when the most
        recent completed higher-TF bar's close is above its VWMA(window).
        ``False`` (or NaN -> False) before the first higher-TF bar
        completes.
    """
    if not close_1m.index.equals(volume_1m.index):
        raise ValueError("close_1m and volume_1m must share the same index")

    htf_close = close_1m.resample(higher_tf, label="right", closed="right").last()
    htf_volume = volume_1m.resample(higher_tf, label="right", closed="right").sum()

    htf_vwma = vwma(htf_close, htf_volume, window=vwma_window)
    htf_alignment = (htf_close > htf_vwma).astype("boolean")

    # Map back to 1m index using forward-fill. The HTF bar timestamped
    # at ``T`` is only available *after* T (because it uses up to and
    # including the bar that closes at T). Shift one HTF step so the
    # value at 1m bar ``t`` reflects HTF bars that closed strictly
    # before ``t``.
    htf_freq = pd.tseries.frequencies.to_offset(higher_tf)
    htf_alignment_shifted = htf_alignment.copy()
    htf_alignment_shifted.index = htf_alignment.index + htf_freq

    aligned = htf_alignment_shifted.reindex(
        close_1m.index, method="ffill"
    ).fillna(False).astype(bool)

    return aligned.rename("multi_tf_alignment")
