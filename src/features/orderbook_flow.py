"""Order-book flow features.

Reference: ``docs/background/39-orderbook-flow-features.md``.

Implements:
    - OBI (Order Book Imbalance, top-of-book)
    - OFI (Order Flow Imbalance, change-driven)
    - Microprice − Mid gap (Stoikov 2018)
    - 1s -> 1m aggregation with causal label='right' resampling.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def order_book_imbalance(
    bid_vol: pd.Series,
    ask_vol: pd.Series,
) -> pd.Series:
    """OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol). Range [-1, 1]."""
    if not bid_vol.index.equals(ask_vol.index):
        raise ValueError("bid_vol and ask_vol must share the same index")
    total = bid_vol + ask_vol
    with np.errstate(divide="ignore", invalid="ignore"):
        obi = (bid_vol - ask_vol) / total
    obi = obi.where(total > 0, other=np.nan)
    return obi.rename("obi")


def order_flow_imbalance(
    bid_vol: pd.Series,
    ask_vol: pd.Series,
    bid_vol_prev: pd.Series,
    ask_vol_prev: pd.Series,
) -> pd.Series:
    """OFI = Δbid_vol − Δask_vol (cumulative order-flow pressure).

    Cont, Kukanov & Stoikov (2014) §3. The convention here treats
    increases in bid depth and decreases in ask depth as positive
    flow (= buy pressure).
    """
    for ref in (bid_vol, ask_vol_prev, bid_vol_prev):
        if not ask_vol.index.equals(ref.index):
            raise ValueError("OFI inputs must share the same index")

    delta_bid = bid_vol - bid_vol_prev
    delta_ask = ask_vol - ask_vol_prev
    return (delta_bid - delta_ask).rename("ofi")


def microprice_mid_gap(
    bid_price: pd.Series,
    ask_price: pd.Series,
    bid_vol: pd.Series,
    ask_vol: pd.Series,
) -> pd.Series:
    """Microprice − mid gap (Stoikov 2018).

    ``microprice = (bid_price * ask_vol + ask_price * bid_vol)
                   / (bid_vol + ask_vol)``

    ``mid       = (bid_price + ask_price) / 2``

    Positive gap => buy pressure; negative => sell pressure.
    Returns NaN where ``bid_vol + ask_vol == 0``.
    """
    for ref in (ask_price, bid_vol, ask_vol):
        if not bid_price.index.equals(ref.index):
            raise ValueError("microprice inputs must share the same index")

    total = bid_vol + ask_vol
    with np.errstate(divide="ignore", invalid="ignore"):
        microprice = (bid_price * ask_vol + ask_price * bid_vol) / total
    mid = (bid_price + ask_price) / 2.0
    gap = (microprice - mid).where(total > 0, other=np.nan)
    return gap.rename("microprice_mid_gap")


def aggregate_orderbook_features(
    orderbook_1s: pd.DataFrame,
    resample_freq: str = "1min",
) -> pd.DataFrame:
    """Aggregate raw 1-second order-book snapshots to a coarser frequency.

    Required columns: ``ts`` (DatetimeIndex or column), ``bid_price``,
    ``ask_price``, ``bid_vol``, ``ask_vol``.

    Resamples with ``label='right'`` and ``closed='right'`` so the
    aggregated bar at time ``T`` reflects the half-open interval
    ``(T - resample_freq, T]`` — strictly causal (no future leakage).

    Returns
    -------
    pd.DataFrame with columns:
        - ``obi_mean``: mean OBI within the interval
        - ``ofi_cumsum``: net OFI over the interval
        - ``microprice_gap_mean``: mean microprice − mid gap
        - ``spread_mean``: mean of ``ask_price − bid_price``
    """
    needed = {"bid_price", "ask_price", "bid_vol", "ask_vol"}
    df = orderbook_1s.copy()
    if not isinstance(df.index, pd.DatetimeIndex):
        if "ts" not in df.columns:
            raise ValueError("orderbook_1s must have a DatetimeIndex or a 'ts' column")
        df = df.set_index(pd.DatetimeIndex(df["ts"])).drop(columns=["ts"])
    missing = needed - set(df.columns)
    if missing:
        raise ValueError(f"orderbook_1s missing columns: {sorted(missing)}")

    df = df.sort_index().ffill()
    obi = order_book_imbalance(df["bid_vol"], df["ask_vol"])
    bid_prev = df["bid_vol"].shift(1)
    ask_prev = df["ask_vol"].shift(1)
    ofi = order_flow_imbalance(df["bid_vol"], df["ask_vol"], bid_prev, ask_prev)
    micro_gap = microprice_mid_gap(
        df["bid_price"], df["ask_price"], df["bid_vol"], df["ask_vol"]
    )
    spread = df["ask_price"] - df["bid_price"]

    res = pd.DataFrame(
        {
            "obi_mean": obi,
            "ofi_cumsum": ofi.fillna(0.0),
            "microprice_gap_mean": micro_gap,
            "spread_mean": spread,
        }
    )

    agg = res.resample(resample_freq, label="right", closed="right").agg(
        {
            "obi_mean": "mean",
            "ofi_cumsum": "sum",
            "microprice_gap_mean": "mean",
            "spread_mean": "mean",
        }
    )
    return agg
