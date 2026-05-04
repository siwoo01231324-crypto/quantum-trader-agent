"""Exchange funding rate fetchers with a common interface.

Each exchange module exposes:
    fetch_funding_history(symbol, start, end) -> pd.DataFrame

Returned DataFrame columns: [ts (UTC DatetimeTZ), funding_rate (float64)]
Partition path convention: lake/funding_rate/exchange={exchange}/symbol={symbol}/part-0.parquet
"""
from __future__ import annotations

from typing import Protocol

import pandas as pd


class FundingFetcher(Protocol):
    """Common interface for exchange funding rate fetchers."""

    def fetch_funding_history(
        self,
        symbol: str,
        start: str,
        end: str,
    ) -> pd.DataFrame:
        """Fetch funding rate history.

        Parameters
        ----------
        symbol : Exchange-native symbol, e.g. "BTC-USDT-SWAP" (OKX), "BTCUSDT" (Bybit)
        start  : ISO date string e.g. "2020-09-01"
        end    : ISO date string e.g. "2025-12-31"

        Returns
        -------
        pd.DataFrame with columns [ts, funding_rate].
        ts is UTC-aware, funding_rate is float64.
        """
        ...


__all__ = ["FundingFetcher"]
