"""Cross-sectional relative strength + UBAI benchmark.

Reference: ``docs/background/42-cross-sectional-momentum-crypto.md``.

UBAI (업비트 알트코인 인덱스) is computed as a market-cap weighted
daily index over the top 20 KRW-pair altcoins on Upbit, excluding BTC
and ETH, rebalanced monthly. The fallback (when the Upbit API is
unavailable) is the inverse of BTC dominance.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np
import pandas as pd


def relative_strength(
    asset_returns: pd.Series,
    benchmark_returns: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Rolling relative strength of an asset against a benchmark.

    ``RS_t(w) = mean(asset_returns[t-w+1:t+1])
                - mean(benchmark_returns[t-w+1:t+1])``

    Parameters
    ----------
    asset_returns:
        Asset daily returns.
    benchmark_returns:
        Benchmark daily returns (e.g., UBAI), same index.
    window:
        Lookback in periods (default 20 ≈ 1 month of trading days).
    """
    if benchmark_returns is None:
        raise ValueError("benchmark_returns is required")
    if not asset_returns.index.equals(benchmark_returns.index):
        raise ValueError(
            "asset_returns and benchmark_returns must share the same index"
        )

    asset_mean = asset_returns.rolling(window=window, min_periods=window).mean()
    bench_mean = benchmark_returns.rolling(window=window, min_periods=window).mean()
    return (asset_mean - bench_mean).rename("relative_strength")


def rs_quartile(
    asset_returns: pd.DataFrame,
    benchmark_returns: pd.Series,
    window: int = 20,
) -> pd.DataFrame:
    """Cross-sectional RS-quartile assignment.

    For each column (asset) in ``asset_returns``, computes RS vs
    ``benchmark_returns`` and at each timestamp assigns a quartile rank
    (1=top, 2, 3, 4=bottom) using percentile binning across the columns.

    Returns
    -------
    pd.DataFrame
        Same columns and index as ``asset_returns``. Values in {1, 2, 3, 4}
        with NaN where the rolling RS is undefined (warm-up).
    """
    if benchmark_returns is None:
        raise ValueError("benchmark_returns is required")
    if not asset_returns.index.equals(benchmark_returns.index):
        raise ValueError(
            "asset_returns and benchmark_returns must share the same index"
        )

    rs_frame = pd.DataFrame(index=asset_returns.index, columns=asset_returns.columns)
    for col in asset_returns.columns:
        rs_frame[col] = relative_strength(
            asset_returns[col], benchmark_returns, window=window
        )

    # Per-row quartile rank across columns (1=top RS, 4=bottom RS).
    quartiles = rs_frame.apply(_row_quartile, axis=1)
    quartiles.columns = asset_returns.columns
    return quartiles


def _row_quartile(row: pd.Series) -> pd.Series:
    """Assign quartile labels {1, 2, 3, 4} across a row, NaN-safe."""
    valid = row.dropna()
    out = pd.Series(np.nan, index=row.index)
    if valid.empty:
        return out
    # ``qcut`` with 4 bins, reverse so 1 = highest RS.
    try:
        ranks = pd.qcut(
            valid, 4, labels=[4, 3, 2, 1], duplicates="drop"
        )
    except ValueError:
        # Too few unique values — fall back to rank-based binning.
        ranks_pct = valid.rank(method="average", pct=True)
        ranks = pd.cut(
            ranks_pct,
            bins=[0.0, 0.25, 0.5, 0.75, 1.0001],
            labels=[4, 3, 2, 1],
            include_lowest=True,
        )
    out.loc[valid.index] = ranks.astype(float)
    return out


def compute_ubai(
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    *,
    universe_size: int = 20,
    excluded: tuple[str, ...] = ("KRW-BTC", "KRW-ETH"),
    fetcher: Any | None = None,
) -> pd.Series:
    """Compute the Upbit altcoin index (UBAI) daily return series.

    Definition
    ----------
    Top ``universe_size`` KRW-pair altcoins on Upbit by market cap,
    excluding BTC and ETH, market-cap weighted, rebalanced on the first
    trading day of each calendar month.

    Parameters
    ----------
    start_date / end_date:
        Inclusive date range for the resulting series.
    universe_size:
        Number of altcoins in the universe.
    excluded:
        Upbit market codes to exclude (default BTC and ETH KRW pairs).
    fetcher:
        Optional dependency-injected adapter exposing two methods:
        ``list_markets() -> list[str]`` and
        ``daily_ohlcv(market: str, start, end) -> pd.DataFrame`` with
        columns ``["close", "market_cap"]``. Allows unit tests to mock
        the Upbit REST API without network access.

    Returns
    -------
    pd.Series
        Daily simple return series of UBAI, indexed by date.

    Notes
    -----
    The default network implementation (using requests against the
    Upbit ``/v1/market/all`` and ``/v1/ticker`` endpoints, rate-limited
    to 600/min, no API key required) is intentionally not implemented
    here to keep the unit tests offline. Pass a ``fetcher`` to drive
    the function. The bench script (``scripts/bench_iranyi_variants.py``)
    is responsible for wiring the production adapter and falling back
    to the BTC dominance inverse when the Upbit API is unavailable
    (see issue #99 plan §3.5).
    """
    if fetcher is None:
        raise NotImplementedError(
            "compute_ubai requires a fetcher adapter. "
            "Wire the production Upbit REST adapter in the bench script "
            "or pass a mock for unit tests."
        )

    start = pd.Timestamp(start_date).normalize()
    end = pd.Timestamp(end_date).normalize()

    # Discover monthly rebalance dates (first day of each month within range).
    rebalance_dates = pd.date_range(start=start, end=end, freq="MS")
    if len(rebalance_dates) == 0 or rebalance_dates[0] > start:
        rebalance_dates = rebalance_dates.insert(0, start)

    daily_index_returns: list[pd.Series] = []

    for r_idx, r_date in enumerate(rebalance_dates):
        period_end = (
            rebalance_dates[r_idx + 1] - dt.timedelta(days=1)
            if r_idx + 1 < len(rebalance_dates)
            else end
        )

        markets = fetcher.list_markets()
        candidates = [m for m in markets if m.startswith("KRW-") and m not in excluded]

        # Pick top universe_size by market cap as of r_date.
        snapshot = []
        for market in candidates:
            df = fetcher.daily_ohlcv(market, r_date, r_date)
            if df is None or df.empty or "market_cap" not in df.columns:
                continue
            mcap = float(df["market_cap"].iloc[0])
            snapshot.append((market, mcap))
        snapshot.sort(key=lambda kv: kv[1], reverse=True)
        universe = [m for m, _ in snapshot[:universe_size]]
        weights = np.array([m for _, m in snapshot[:universe_size]], dtype=float)
        if weights.sum() <= 0:
            continue
        weights = weights / weights.sum()

        # Pull daily closes and compute returns over the period.
        period_returns: list[pd.Series] = []
        for market in universe:
            df = fetcher.daily_ohlcv(market, r_date, period_end)
            if df is None or df.empty:
                continue
            close = df["close"].astype(float)
            period_returns.append(close.pct_change().rename(market))

        if not period_returns:
            continue

        ret_frame = pd.concat(period_returns, axis=1)
        ret_frame = ret_frame.reindex(columns=universe)
        # Weighted return (skip-NaN, normalising weights to available columns).
        valid_mask = ret_frame.notna()
        weighted = (ret_frame.fillna(0.0).values * weights).sum(axis=1)
        weight_used = (valid_mask.values * weights).sum(axis=1)
        with np.errstate(divide="ignore", invalid="ignore"):
            ubai_ret = pd.Series(
                np.where(weight_used > 0, weighted / weight_used, np.nan),
                index=ret_frame.index,
            )
        daily_index_returns.append(ubai_ret)

    if not daily_index_returns:
        return pd.Series(dtype=float, name="ubai_return")

    out = pd.concat(daily_index_returns).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out.loc[start:end]
    return out.rename("ubai_return")
