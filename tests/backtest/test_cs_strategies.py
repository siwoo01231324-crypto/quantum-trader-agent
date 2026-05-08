"""Smoke + invariant tests for cross-sectional universe-scan strategies (#218).

7 modules covered:
  cs_rsi_div_kr · cs_bb_macd_kr · cs_adx_ma_kr ·
  cs_rsi_div_crypto · cs_macd_vol_crypto ·
  cs_tsmom_kr_daily · cs_tsmom_crypto_daily

Common invariants:
  - compute_weights(...) returns DataFrame [date, ticker]
  - Each row's sum is in [0, 1] (long-only, fractional allocation)
  - Equal-weight semantics: nonzero weights are 1/k where k = picks count
  - No look-ahead: weights at row i depend only on close[<i]
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

# All strategies share the same import shape
from backtest.strategies import (
    cs_adx_ma_kr,
    cs_bb_macd_kr,
    cs_macd_vol_crypto,
    cs_rsi_div_crypto,
    cs_rsi_div_kr,
    cs_tsmom_crypto_daily,
    cs_tsmom_kr_daily,
)
from backtest.strategies._cs_helpers import (
    adx_panel,
    atr_panel,
    bollinger_panel,
    build_weights,
    daily_returns_from_weights,
    liquid_mask_panel,
    macd_panel,
    rsi_panel,
)


# ---------------------------------------------------------------------------
# Synthetic data fixtures
# ---------------------------------------------------------------------------

def make_panel(n_bars: int = 500, n_tickers: int = 30, seed: int = 42
               ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate synthetic OHLC + turnover panel via gbm random walk."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="B")
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    drift = rng.uniform(-0.0005, 0.001, size=n_tickers)
    vol = rng.uniform(0.01, 0.04, size=n_tickers)
    rets = rng.normal(drift, vol, size=(n_bars, n_tickers))
    closes = 1000 * np.exp(np.cumsum(rets, axis=0))
    high = closes * (1 + np.abs(rng.normal(0, 0.005, size=closes.shape)))
    low = closes * (1 - np.abs(rng.normal(0, 0.005, size=closes.shape)))
    turnover = rng.uniform(1e9, 1e11, size=closes.shape)
    return (
        pd.DataFrame(closes, index=dates, columns=tickers),
        pd.DataFrame(high, index=dates, columns=tickers),
        pd.DataFrame(low, index=dates, columns=tickers),
        pd.DataFrame(turnover, index=dates, columns=tickers),
    )


@pytest.fixture
def panels():
    close, high, low, turnover = make_panel()
    return {"close": close, "high": high, "low": low, "turnover": turnover}


# ---------------------------------------------------------------------------
# Helper module tests
# ---------------------------------------------------------------------------

def test_rsi_panel_shape_and_range(panels):
    rsi = rsi_panel(panels["close"], period=14)
    assert rsi.shape == panels["close"].shape
    valid = rsi.dropna(how="all").iloc[20:]  # past warmup
    assert ((valid >= 0) & (valid <= 100)).all().all()


def test_macd_panel_components(panels):
    macd, sig, hist = macd_panel(panels["close"])
    assert macd.shape == sig.shape == hist.shape == panels["close"].shape
    assert np.allclose((macd - sig).iloc[40:], hist.iloc[40:], atol=1e-10)


def test_bollinger_panel_ordering(panels):
    mid, upper, lower = bollinger_panel(panels["close"], 20)
    valid = mid.iloc[25:]
    assert (upper.iloc[25:] >= valid).all().all()
    assert (lower.iloc[25:] <= valid).all().all()


def test_atr_panel_nonneg(panels):
    atr = atr_panel(panels["high"], panels["low"], panels["close"], 14)
    assert (atr.iloc[20:] >= 0).all().all()


def test_adx_panel_in_range(panels):
    adx = adx_panel(panels["high"], panels["low"], panels["close"], 14)
    valid = adx.iloc[40:]
    assert ((valid >= 0) & (valid <= 100)).all().all()


def test_build_weights_rows_sum_at_most_one(panels):
    close = panels["close"]
    score = pd.DataFrame(np.random.RandomState(0).rand(*close.shape),
                          index=close.index, columns=close.columns)

    def score_fn(i):
        return score.iloc[i]

    w = build_weights(close, score_fn, top_n=5, rebal_freq=5, warmup=20)
    sums = w.sum(axis=1)
    assert (sums <= 1.0 + 1e-9).all()
    assert (sums >= 0.0 - 1e-9).all()


def test_build_weights_equal_weight_topn(panels):
    close = panels["close"]
    score = pd.DataFrame(np.arange(close.size).reshape(close.shape).astype(float),
                          index=close.index, columns=close.columns)
    w = build_weights(close, lambda i: score.iloc[i], top_n=3, rebal_freq=5, warmup=20)
    nonzero = w[w > 0]
    valid = nonzero.dropna(how="all")
    if not valid.empty:
        # 비-제로 가중치는 모두 1/3 ≈ 0.333
        assert np.allclose(valid.dropna().values, 1.0 / 3.0, atol=1e-6)


def test_daily_returns_from_weights_no_lookahead(panels):
    close = panels["close"]
    w = pd.DataFrame(0.0, index=close.index, columns=close.columns)
    w.iloc[100:200, :3] = 1.0 / 3.0  # 3종목 동일가중 보유
    ret = daily_returns_from_weights(w, close, cost_bps=20)
    # 첫 100바는 보유 없음 → 수익 0 (cost 도 0)
    assert np.allclose(ret.iloc[:99], 0.0)


def test_liquid_mask_filters_below_threshold(panels):
    close = panels["close"]
    # 일부 종목 turnover 매우 낮게
    turnover = panels["turnover"].copy()
    turnover.iloc[:, 0] = 1e6  # 첫 종목 평균 미만
    mask_fn = liquid_mask_panel(turnover, close, min_turnover=1e9, min_price=100, window=60)
    mask = mask_fn(100)
    assert mask.iloc[0] == False  # 필터링됨
    assert mask.iloc[5] == True


# ---------------------------------------------------------------------------
# Per-strategy smoke tests
# ---------------------------------------------------------------------------

def assert_valid_weights(w: pd.DataFrame, close: pd.DataFrame, *, top_n: int) -> None:
    assert w.shape == close.shape
    sums = w.sum(axis=1)
    assert (sums <= 1.0 + 1e-9).all(), f"row sum > 1: max={sums.max()}"
    assert (sums >= 0.0 - 1e-9).all()
    # 비-제로 행은 보유 종목 ≤ top_n
    nonzero_count = (w > 0).sum(axis=1)
    assert (nonzero_count <= top_n).all(), f"too many holdings: max={nonzero_count.max()}"


def test_cs_rsi_div_kr_smoke(panels):
    w = cs_rsi_div_kr.compute_weights(
        panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_turnover=1e8, min_price=100,
    )
    assert_valid_weights(w, panels["close"], top_n=5)


def test_cs_bb_macd_kr_smoke(panels):
    w = cs_bb_macd_kr.compute_weights(
        panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_turnover=1e8, min_price=100,
    )
    assert_valid_weights(w, panels["close"], top_n=5)


def test_cs_adx_ma_kr_smoke(panels):
    w = cs_adx_ma_kr.compute_weights(
        panels["high"], panels["low"], panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_turnover=1e8, min_price=100,
    )
    assert_valid_weights(w, panels["close"], top_n=5)


def test_cs_rsi_div_crypto_smoke(panels):
    w = cs_rsi_div_crypto.compute_weights(
        panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_quote_vol=1e8,
    )
    assert_valid_weights(w, panels["close"], top_n=5)


def test_cs_macd_vol_crypto_smoke(panels):
    w = cs_macd_vol_crypto.compute_weights(
        panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_quote_vol=1e8, vol_ceiling=2.0,
    )
    assert_valid_weights(w, panels["close"], top_n=5)


def test_cs_tsmom_kr_daily_smoke(panels):
    # synthetic 500 bars 만 있어서 252 lookback 후 warmup 가능
    w = cs_tsmom_kr_daily.compute_weights(
        panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_turnover=1e8, min_price=100,
    )
    assert_valid_weights(w, panels["close"], top_n=5)
    # warmup=252 까지는 가중치 0
    assert np.allclose(w.iloc[:251].sum(axis=1), 0.0)


def test_cs_tsmom_crypto_daily_smoke(panels):
    w = cs_tsmom_crypto_daily.compute_weights(
        panels["close"], panels["turnover"],
        top_n=5, rebal_freq=5, min_quote_vol=1e8,
    )
    assert_valid_weights(w, panels["close"], top_n=5)


# ---------------------------------------------------------------------------
# Score panel direct tests
# ---------------------------------------------------------------------------

def test_tsmom_score_matches_log_ratio(panels):
    """cs_tsmom score = log(close[t-21] / close[t-252])."""
    close = panels["close"]
    score = cs_tsmom_kr_daily.score_panel(close, long_lb=252, skip_lb=21)
    expected = np.log(close.shift(21) / close.shift(252))
    pd.testing.assert_frame_equal(score, expected, check_names=False)


def test_macd_vol_score_zero_when_high_vol(panels):
    """변동성 ceiling 보다 크면 score = 0."""
    close = panels["close"]
    # 매우 낮은 ceiling 으로 모든 종목 stride out
    score = cs_macd_vol_crypto.score_panel(close, vol_ceiling=0.001)
    assert np.allclose(score.dropna(how="all").values, 0.0)
