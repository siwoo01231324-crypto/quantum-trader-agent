"""VPVR (Volume Profile Visible Range) POC wrapper + support zones.

Rule #9 in Iranyi 12-rule stack.

Wraps src.features.poc.point_of_control and extends it with top-k
highest-volume support zones for each rolling window.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.features.poc import point_of_control


def volume_profile_support_zones(
    ohlcv: pd.DataFrame,
    window: int = 200,
    n_bins: int = 24,
    top_k: int = 3,
) -> pd.DataFrame:
    """For each bar t, compute POC + top_k support zones over rolling window.

    Parameters
    ----------
    ohlcv:
        DataFrame with columns: open, high, low, close, volume.
    window:
        Lookback in bars for the rolling volume profile.
    n_bins:
        Number of price bins for the volume histogram.
    top_k:
        Number of highest-volume bins to return as support zones.

    Returns
    -------
    pd.DataFrame with columns:
        - ``poc_price``: price at the point of control
        - ``zone_1`` .. ``zone_{top_k}``: centre prices of top-k bins
          sorted by volume (descending). zone_1 == poc_price.
    """
    close = ohlcv["close"]
    volume = ohlcv["volume"]

    # Get base POC from shared implementation
    poc_df = point_of_control(close, volume, n_bins=n_bins, window=window)

    # Build zone columns
    zone_cols: dict[str, pd.Series] = {}
    for k in range(1, top_k + 1):
        zone_cols[f"zone_{k}"] = pd.Series(np.nan, index=close.index)

    close_arr = close.to_numpy(dtype=float)
    volume_arr = volume.to_numpy(dtype=float)
    n = len(close_arr)

    for t in range(window - 1, n):
        win_close = close_arr[t - window + 1 : t + 1]
        win_volume = volume_arr[t - window + 1 : t + 1]

        if not np.isfinite(win_close).all() or not np.isfinite(win_volume).all():
            continue

        c_min = float(win_close.min())
        c_max = float(win_close.max())
        total_vol = float(win_volume.sum())

        if total_vol <= 0:
            continue

        if c_max <= c_min:
            # All prices identical — one zone
            for k in range(1, top_k + 1):
                zone_cols[f"zone_{k}"].iloc[t] = c_min
            continue

        bin_edges = np.linspace(c_min, c_max, n_bins + 1)
        hist, _ = np.histogram(win_close, bins=bin_edges, weights=win_volume)
        bin_centres = 0.5 * (bin_edges[:-1] + bin_edges[1:])

        # Sort bins by volume descending
        top_indices = np.argsort(hist)[::-1]
        for k in range(1, top_k + 1):
            idx_k = k - 1
            if idx_k < len(top_indices):
                zone_cols[f"zone_{k}"].iloc[t] = float(bin_centres[top_indices[idx_k]])

    result = pd.DataFrame({"poc_price": poc_df["poc_price"]})
    for k in range(1, top_k + 1):
        result[f"zone_{k}"] = zone_cols[f"zone_{k}"]

    return result


def near_support_zone(
    close: pd.Series,
    support_zones_df: pd.DataFrame,
    tolerance_pct: float = 0.005,
) -> pd.Series:
    """True if `close` is within `tolerance_pct` of any support zone.

    Parameters
    ----------
    close:
        Per-bar close prices.
    support_zones_df:
        DataFrame from volume_profile_support_zones — must share the
        same index as close.
    tolerance_pct:
        Fractional tolerance (e.g. 0.005 = 0.5%).

    Returns
    -------
    pd.Series[bool]
    """
    result = pd.Series(False, index=close.index, dtype=bool)
    zone_cols = [c for c in support_zones_df.columns if c.startswith("zone_") or c == "poc_price"]

    for col in zone_cols:
        zone = support_zones_df[col]
        within = (close - zone).abs() <= close.abs() * tolerance_pct
        result = result | within.fillna(False)

    return result.rename("near_support_zone")
