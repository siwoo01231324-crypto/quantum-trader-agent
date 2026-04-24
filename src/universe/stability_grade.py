# Patent avoidance: 업리치 특허 청구항 (d) 의 A~F 등급 개념을 차용,
# 입력 변수 및 등급 기준 재정의로 회피.
"""Altcoin universe stability grading — pure function, no external I/O.

CoinGecko/market-data adapters live in a separate module (follow-up issue).
Callers are responsible for supplying mcap_usd, vol_30d_usd, dev_activity.
"""
from __future__ import annotations

import math
from typing import Optional


class StabilityGrade:
    """Grade a crypto asset A–F based on market cap, volume, and dev activity.

    Weights:
      Full  (dev_activity provided): mcap=0.4, volume=0.4, dev=0.2
      No-dev (dev_activity=None):    mcap=0.5, volume=0.5
    """

    WEIGHTS_FULL = {"mcap": 0.4, "volume": 0.4, "dev": 0.2}
    WEIGHTS_NO_DEV = {"mcap": 0.5, "volume": 0.5}

    # Grade thresholds on the composite [0, 1] score
    # Score >= threshold → grade
    _THRESHOLDS = [
        (0.85, "A"),
        (0.70, "B"),
        (0.50, "C"),
        (0.30, "D"),
        (0.15, "E"),
    ]

    # Log-scale anchors for normalisation (order-of-magnitude references)
    _MCAP_MAX = 2e12   # ~BTC peak market cap
    _VOL_MAX = 1e11    # ~BTC 30-day volume
    _DEV_MAX = 1000.0  # monthly commit count upper anchor

    @staticmethod
    def _log_norm(value: float, maximum: float) -> float:
        """Map value onto [0, 1] with log10 scaling; clamp to [0, 1]."""
        if value <= 0 or maximum <= 0:
            return 0.0
        ratio = value / maximum
        # log10(ratio) in [-inf, 0]; shift so log10(1) → 1, log10(1e-6) → 0
        log_val = math.log10(ratio)
        normalised = (log_val + 6) / 6  # range [-inf, 0] mapped to [0, 1] via 6-decade window
        return max(0.0, min(1.0, normalised))

    def grade(
        self,
        mcap_usd: float,
        vol_30d_usd: float,
        dev_activity: Optional[int] = None,
    ) -> str:
        """Return stability grade string 'A'–'F'.

        Args:
            mcap_usd: Current market capitalisation in USD.
            vol_30d_usd: 30-day trading volume in USD.
            dev_activity: Optional commit/PR count over the last 30 days.
                          When None, weight is redistributed to mcap and volume.
        """
        s_mcap = self._log_norm(mcap_usd, self._MCAP_MAX)
        s_vol = self._log_norm(vol_30d_usd, self._VOL_MAX)

        if dev_activity is None:
            w = self.WEIGHTS_NO_DEV
            score = w["mcap"] * s_mcap + w["volume"] * s_vol
        else:
            s_dev = self._log_norm(float(dev_activity), self._DEV_MAX)
            w = self.WEIGHTS_FULL
            score = w["mcap"] * s_mcap + w["volume"] * s_vol + w["dev"] * s_dev

        for threshold, letter in self._THRESHOLDS:
            if score >= threshold:
                return letter
        return "F"


def grade_symbol(
    mcap_usd: float,
    vol_30d_usd: float,
    dev_activity: Optional[int] = None,
) -> str:
    """Module-level convenience wrapper around StabilityGrade.grade()."""
    return StabilityGrade().grade(mcap_usd, vol_30d_usd, dev_activity)
