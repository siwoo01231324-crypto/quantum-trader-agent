"""Rolling Point of Control (POC) — volume-profile features.

Reference: ``docs/background/39-orderbook-flow-features.md``.

For each bar, build a histogram of volume against price over the
previous ``window`` bars, then locate the price bin with the maximum
volume — the POC. Returns the POC price, the signed distance from
the current close to the POC, and the volume concentration ratio.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def point_of_control(
    close: pd.Series,
    volume: pd.Series,
    n_bins: int = 50,
    window: int = 100,
) -> pd.DataFrame:
    """Compute rolling POC features.

    Parameters
    ----------
    close:
        Per-bar close prices.
    volume:
        Per-bar volumes (same index).
    n_bins:
        Number of price bins for the volume histogram.
    window:
        Lookback in bars.

    Returns
    -------
    pd.DataFrame with columns:
        - ``poc_price``: price at the POC
        - ``poc_distance``: ``(close - poc_price) / close`` (signed)
        - ``poc_volume_ratio``: ``vol_at_poc / total_window_volume``
    """
    if not close.index.equals(volume.index):
        raise ValueError("close and volume must share the same index")
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    if n_bins < 2:
        raise ValueError(f"n_bins must be >= 2, got {n_bins}")

    poc_price = pd.Series(np.nan, index=close.index, name="poc_price")
    poc_volume_ratio = pd.Series(np.nan, index=close.index, name="poc_volume_ratio")

    close_arr = close.to_numpy()
    volume_arr = volume.to_numpy()

    for t in range(window - 1, len(close)):
        win_close = close_arr[t - window + 1 : t + 1]
        win_volume = volume_arr[t - window + 1 : t + 1]
        if not np.isfinite(win_close).all() or not np.isfinite(win_volume).all():
            continue

        c_min = float(win_close.min())
        c_max = float(win_close.max())
        total_vol = float(win_volume.sum())

        if c_max <= c_min or total_vol <= 0:
            poc_price.iloc[t] = c_min
            poc_volume_ratio.iloc[t] = 1.0 if total_vol > 0 else 0.0
            continue

        bin_edges = np.linspace(c_min, c_max, n_bins + 1)
        hist, _ = np.histogram(win_close, bins=bin_edges, weights=win_volume)
        peak = int(np.argmax(hist))
        # Centre of the winning bin
        peak_price = 0.5 * (bin_edges[peak] + bin_edges[peak + 1])
        poc_price.iloc[t] = peak_price
        poc_volume_ratio.iloc[t] = float(hist[peak]) / total_vol

    poc_distance = ((close - poc_price) / close).rename("poc_distance")

    return pd.DataFrame(
        {
            "poc_price": poc_price,
            "poc_distance": poc_distance,
            "poc_volume_ratio": poc_volume_ratio,
        }
    )
