"""Tests for src/features/* (issue #99 Stage 3)."""
from __future__ import annotations

import math
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from src.features import (
    aggregate_orderbook_features,
    compute_ubai,
    ema_projection,
    ema_slope,
    microprice_mid_gap,
    multi_tf_alignment,
    order_book_imbalance,
    order_flow_imbalance,
    point_of_control,
    relative_strength,
    rs_quartile,
    time_gate,
    vwma,
    vwma_cross,
)
from src.signals.lookahead_guard import assert_no_lookahead


@pytest.fixture
def ohlcv_1m() -> pd.DataFrame:
    """200 bars of 1-minute synthetic OHLCV."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2026-01-01", periods=200, freq="1min", tz="UTC")
    close = pd.Series(100.0 + rng.normal(scale=0.5, size=200).cumsum(), index=idx)
    volume = pd.Series(rng.uniform(1.0, 10.0, size=200), index=idx)
    return pd.DataFrame({"close": close, "volume": volume})


# --------- VWMA -----------------------------------------------------------


def test_vwma_equals_sma_when_volume_constant() -> None:
    idx = pd.date_range("2026-01-01", periods=50, freq="1min", tz="UTC")
    close = pd.Series(np.linspace(1.0, 50.0, 50), index=idx)
    volume = pd.Series(np.ones(50), index=idx)
    vw = vwma(close, volume, window=10)
    sma = close.rolling(10).mean()
    pd.testing.assert_series_equal(vw.dropna(), sma.dropna(), check_names=False)


def test_vwma_window_warmup() -> None:
    idx = pd.date_range("2026-01-01", periods=10, freq="1min", tz="UTC")
    close = pd.Series(np.arange(10.0), index=idx)
    volume = pd.Series(np.ones(10), index=idx)
    vw = vwma(close, volume, window=5)
    assert vw.iloc[:4].isna().all()
    assert not pd.isna(vw.iloc[4])


def test_vwma_cross_causal(ohlcv_1m: pd.DataFrame) -> None:
    assert_no_lookahead(
        vwma,
        ohlcv_1m,
        inputs=["close", "volume"],
        window=20,
    )


# --------- MA projection --------------------------------------------------


def test_ema_slope_positive_uptrend() -> None:
    idx = pd.date_range("2026-01-01", periods=120, freq="1min", tz="UTC")
    close = pd.Series(np.linspace(100.0, 200.0, 120), index=idx)
    s = ema_slope(close, span=20, slope_window=5)
    last_valid = s.dropna()
    assert (last_valid > 0).all()


def test_ema_projection_non_decreasing_in_strict_uptrend() -> None:
    idx = pd.date_range("2026-01-01", periods=200, freq="1min", tz="UTC")
    close = pd.Series(np.linspace(100.0, 300.0, 200), index=idx)
    proj = ema_projection(close, span=20, horizon=5, slope_window=5)
    valid = proj["ema_proj_n"].dropna()
    diffs = valid.diff().dropna()
    assert (diffs > 0).all()


def test_ema_projection_columns(ohlcv_1m: pd.DataFrame) -> None:
    proj = ema_projection(ohlcv_1m["close"], span=20, horizon=5, slope_window=5)
    assert set(proj.columns) == {"ema_proj_n", "eta_to_cross", "price_to_ema_gap_at_n"}


# --------- Multi-TF -------------------------------------------------------


def test_multi_tf_no_lookahead() -> None:
    """Resampled multi-tf alignment must be causal across the append-tail-bar
    invariance check."""
    idx = pd.date_range("2026-01-01", periods=180, freq="1min", tz="UTC")
    rng = np.random.default_rng(7)
    df = pd.DataFrame(
        {
            "close_1m": 100.0 + rng.normal(scale=0.5, size=180).cumsum(),
            "volume_1m": rng.uniform(1.0, 5.0, size=180),
        },
        index=idx,
    )
    assert_no_lookahead(
        multi_tf_alignment,
        df,
        inputs=["close_1m", "volume_1m"],
        higher_tf="15min",
        vwma_window=3,
    )


def test_multi_tf_alignment_runs(ohlcv_1m: pd.DataFrame) -> None:
    """End-to-end smoke test: produces a bool series of correct length."""
    out = multi_tf_alignment(
        ohlcv_1m["close"],
        ohlcv_1m["volume"],
        higher_tf="30min",
        vwma_window=2,
    )
    assert len(out) == len(ohlcv_1m)
    assert out.dtype == bool


# --------- Time-of-day ----------------------------------------------------


def test_time_gate_blocks_weekend() -> None:
    idx = pd.DatetimeIndex(
        ["2026-01-03 12:00", "2026-01-04 12:00", "2026-01-05 12:00"],
        tz="UTC",
    )
    out = time_gate(idx, blocked_hours=[], block_weekends=True)
    # 2026-01-03 is Saturday, 2026-01-04 Sunday (KST equivalent), 2026-01-05 Monday
    assert out.iloc[0] is False or out.iloc[0] == False  # Saturday
    assert out.iloc[1] == False  # Sunday
    assert out.iloc[2] == True  # Monday (allowed)


def test_time_gate_blocks_1030_kst() -> None:
    # 10:30 KST == 01:30 UTC
    idx = pd.DatetimeIndex(
        ["2026-01-05 01:25", "2026-01-05 01:35", "2026-01-05 02:05"],
        tz="UTC",
    )
    out = time_gate(idx)
    assert out.iloc[0] == True  # 10:25 KST — before block
    assert out.iloc[1] == False  # 10:35 KST — blocked
    assert out.iloc[2] == True  # 11:05 KST — after block


# --------- Cross-sectional RS --------------------------------------------


def test_rs_quartile_rank() -> None:
    idx = pd.date_range("2026-01-01", periods=30, freq="D")
    benchmark = pd.Series(np.zeros(30), index=idx, name="bench")
    asset_returns = pd.DataFrame(
        {
            "alpha": np.full(30, 0.05),
            "beta": np.full(30, 0.02),
            "gamma": np.full(30, -0.01),
            "delta": np.full(30, -0.05),
        },
        index=idx,
    )
    quartiles = rs_quartile(asset_returns, benchmark, window=5)
    # On the last day with full warm-up, the highest-RS asset must be quartile 1.
    last = quartiles.iloc[-1].astype(float)
    best = last.idxmin()
    assert best == "alpha"
    worst = last.idxmax()
    assert worst == "delta"


def test_relative_strength_requires_aligned_index() -> None:
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    asset = pd.Series(np.arange(10), index=idx)
    bench = pd.Series(np.arange(10), index=idx + pd.Timedelta(days=1))
    with pytest.raises(ValueError):
        relative_strength(asset, bench, window=3)


def test_compute_ubai_with_mock_fetcher() -> None:
    fetcher = MagicMock()
    fetcher.list_markets.return_value = ["KRW-BTC", "KRW-ETH", "KRW-SOL", "KRW-ADA"]

    def daily_ohlcv(market, start, end):
        if market in ("KRW-SOL", "KRW-ADA"):
            dates = pd.date_range(start=start, end=end, freq="D")
            return pd.DataFrame(
                {
                    "close": np.linspace(100.0, 110.0, len(dates)),
                    "market_cap": np.full(len(dates), 1e9),
                },
                index=dates,
            )
        return pd.DataFrame()

    fetcher.daily_ohlcv.side_effect = daily_ohlcv
    out = compute_ubai(
        pd.Timestamp("2026-01-01"),
        pd.Timestamp("2026-01-10"),
        universe_size=2,
        fetcher=fetcher,
    )
    assert out.name == "ubai_return"
    assert len(out) > 0


# --------- POC ------------------------------------------------------------


def test_poc_single_price() -> None:
    idx = pd.date_range("2026-01-01", periods=20, freq="1min", tz="UTC")
    close = pd.Series(np.full(20, 100.0), index=idx)
    volume = pd.Series(np.ones(20), index=idx)
    poc = point_of_control(close, volume, n_bins=10, window=5)
    valid = poc.dropna()
    assert (valid["poc_price"] == 100.0).all()
    # poc_distance is 0 when close == poc_price
    assert (valid["poc_distance"].abs() < 1e-12).all()


# --------- Orderbook flow -------------------------------------------------


def test_obi_range() -> None:
    rng = np.random.default_rng(0)
    idx = pd.date_range("2026-01-01", periods=200, freq="1s", tz="UTC")
    bid = pd.Series(rng.uniform(0.0, 100.0, size=200), index=idx)
    ask = pd.Series(rng.uniform(0.0, 100.0, size=200), index=idx)
    obi = order_book_imbalance(bid, ask)
    valid = obi.dropna()
    assert ((valid >= -1.0) & (valid <= 1.0)).all()


def test_microprice_mid_gap_symmetric() -> None:
    idx = pd.date_range("2026-01-01", periods=10, freq="1s", tz="UTC")
    bid_p = pd.Series(np.full(10, 100.0), index=idx)
    ask_p = pd.Series(np.full(10, 101.0), index=idx)
    bid_v = pd.Series(np.full(10, 50.0), index=idx)
    ask_v = pd.Series(np.full(10, 50.0), index=idx)
    gap = microprice_mid_gap(bid_p, ask_p, bid_v, ask_v)
    assert (gap.abs() < 1e-12).all()


def test_aggregate_orderbook_resample_label_right() -> None:
    """Aggregation must use closed='right' so the bar timestamp denotes the
    end of the interval (no future leakage)."""
    idx = pd.date_range("2026-01-01 00:00", periods=120, freq="1s", tz="UTC")
    df = pd.DataFrame(
        {
            "bid_price": np.full(120, 100.0),
            "ask_price": np.full(120, 101.0),
            "bid_vol": np.full(120, 10.0),
            "ask_vol": np.full(120, 10.0),
        },
        index=idx,
    )
    out = aggregate_orderbook_features(df, resample_freq="1min")
    # With label='right', closed='right' the bar at T covers (T-freq, T].
    # 120 seconds spanning 00:00..01:59 maps to bars at 00:00, 00:01, 00:02.
    assert len(out) == 3
    # The 00:00 bar must contain only the single snapshot at exactly 00:00,
    # which has constant inputs -> obi_mean == 0, spread_mean == 1.
    first_bar = out.iloc[0]
    assert first_bar["spread_mean"] == 1.0
    assert first_bar["obi_mean"] == 0.0


def test_ofi_sign_convention() -> None:
    idx = pd.date_range("2026-01-01", periods=3, freq="1s", tz="UTC")
    bid_v = pd.Series([10.0, 12.0, 8.0], index=idx)
    ask_v = pd.Series([10.0, 9.0, 11.0], index=idx)
    bid_prev = bid_v.shift(1)
    ask_prev = ask_v.shift(1)
    ofi = order_flow_imbalance(bid_v, ask_v, bid_prev, ask_prev)
    # bar 1: Δbid=+2, Δask=-1 → OFI = 2 - (-1) = 3
    assert math.isclose(ofi.iloc[1], 3.0, abs_tol=1e-12)
    # bar 2: Δbid=-4, Δask=+2 → OFI = -4 - 2 = -6
    assert math.isclose(ofi.iloc[2], -6.0, abs_tol=1e-12)
