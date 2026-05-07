"""Volume burst features — rolling z-score of log1p(volume).

Rule #10 in Iranyi 12-rule stack. A volume burst is detected when the
rolling z-score of log1p(volume) exceeds a threshold.

Hawkes intensity extension is noted as a future enhancement but not
implemented here — the z-score approach is sufficient for the current
variant matrix.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def volume_zscore(volume: pd.Series, lookback: int = 20) -> pd.Series:
    """Rolling z-score of log1p(volume) over `lookback` bars.

    Parameters
    ----------
    volume:
        Per-bar raw volume.
    lookback:
        Rolling window size.

    Returns
    -------
    pd.Series of float — NaN for bars before the first full window,
    and for bars where the input window contains NaN. When std == 0,
    returns 0.0.

    Future extension: Hawkes intensity (self-exciting point process) to
    model clustering of volume bursts — replace the rolling std with
    an estimated Hawkes kernel.
    """
    log_vol = np.log1p(volume)
    roll = log_vol.rolling(window=lookback, min_periods=lookback)
    mean = roll.mean()
    std = roll.std(ddof=1)
    # Avoid division by zero: when std==0, z-score is 0
    z = (log_vol - mean) / std.replace(0.0, np.nan)
    z = z.where(std != 0.0, other=0.0)
    return z.rename("volume_zscore")


def volume_burst_signal(
    volume: pd.Series,
    lookback: int = 20,
    z_threshold: float = 2.0,
) -> pd.Series:
    """Boolean Series: True when volume_zscore > z_threshold (strict).

    Parameters
    ----------
    volume:
        Per-bar raw volume.
    lookback:
        Rolling window passed to volume_zscore.
    z_threshold:
        Threshold for burst detection (strict >).

    Returns
    -------
    pd.Series[bool] — NaN bars become False.
    """
    z = volume_zscore(volume, lookback=lookback)
    signal = z > z_threshold
    # NaN z-scores → False
    signal = signal.fillna(False)
    return signal.rename("volume_burst_signal")
