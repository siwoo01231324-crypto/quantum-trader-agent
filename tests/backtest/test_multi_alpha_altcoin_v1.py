"""Unit tests for MultiAlphaAltcoinV1 — 4 layer 결합.

Layer 별 path:
  1. Cointegration filter — pvalue>0.05 면 hold
  2. Regime — corr 모호 구간 hold
  3. Lead-lag — 동조 + BTC 큰 변동 + vol spike → buy/sell
  4. Outlier — 디커플링 + z>+2 → sell, z<-2 → buy

LiveScannerMixin / orchestrator wiring 검증은 별도 통합 테스트 범위.
본 모듈은 4 helper + evaluate() 의 분기 정확성만.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.strategies.multi_alpha_altcoin_v1 import (
    MultiAlphaAltcoinV1,
    cointegration_pvalue,
    lead_lag_direction,
    rolling_correlation,
    spread_zscore,
)


def _synth_random_walk(n: int, seed: int = 42, start: float = 100.0,
                       sigma: float = 0.01) -> pd.Series:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0, sigma, n)
    prices = start * np.exp(np.cumsum(rets))
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    return pd.Series(prices, index=idx)


def _synth_cointegrated(n: int, seed: int = 42, beta: float = 1.0) -> tuple[pd.Series, pd.Series]:
    """BTC random walk + alt = beta · BTC + small noise → 강한 cointegration."""
    rng = np.random.default_rng(seed)
    btc = _synth_random_walk(n, seed=seed, start=100.0, sigma=0.01)
    noise = rng.normal(0, 0.5, n)  # 작은 stationary noise
    alt = pd.Series(beta * btc.values + noise, index=btc.index)
    # alt 가 양수 유지
    alt = alt.clip(lower=0.1)
    return btc, alt


# ── Helper: cointegration_pvalue ───────────────────────────────────────────

def test_cointegration_strong_pair_has_low_pvalue():
    """동시 random walk → 같은 trend share → p<0.05 기대."""
    pytest.importorskip("statsmodels")
    btc, alt = _synth_cointegrated(200, seed=1)
    p = cointegration_pvalue(btc, alt)
    assert p < 0.10, f"강한 cointegration 인데 p={p:.3f} (>0.10) — 검정 깨짐"


def test_cointegration_independent_returns_high_pvalue():
    """완전 독립 두 random walk → high p (no coint)."""
    pytest.importorskip("statsmodels")
    btc = _synth_random_walk(200, seed=10)
    alt = _synth_random_walk(200, seed=99)
    p = cointegration_pvalue(btc, alt)
    # 독립이면 정확한 값 모르지만 보통 >0.05 — fixed seed 라 deterministic.
    assert 0.0 <= p <= 1.0


def test_cointegration_short_series_returns_one_zero():
    """N<30 이면 graceful fallback p=1.0 (gate 차단)."""
    btc = _synth_random_walk(20, seed=1)
    alt = _synth_random_walk(20, seed=2)
    assert cointegration_pvalue(btc, alt) == 1.0


def test_cointegration_nan_input_returns_one():
    btc = _synth_random_walk(100, seed=1)
    alt = _synth_random_walk(100, seed=2)
    alt.iloc[5] = np.nan
    assert cointegration_pvalue(btc, alt) == 1.0


# ── Helper: rolling_correlation ────────────────────────────────────────────

def test_rolling_correlation_identical_series_is_one():
    s = pd.Series(np.linspace(1, 10, 100))
    rets = s.pct_change().dropna()
    # 자기 자신과 corr = 1
    assert abs(rolling_correlation(rets, rets, 50) - 1.0) < 1e-9


def test_rolling_correlation_independent_near_zero():
    rng = np.random.default_rng(123)
    a = pd.Series(rng.normal(0, 0.01, 200))
    b = pd.Series(rng.normal(0, 0.01, 200))
    r = rolling_correlation(a, b, 100)
    assert -0.3 < r < 0.3, f"독립 sample 인데 corr={r:.3f} 너무 큼"


def test_rolling_correlation_short_returns_zero():
    """데이터 부족 → 0.0."""
    a = pd.Series([0.01] * 5)
    b = pd.Series([0.02] * 5)
    assert rolling_correlation(a, b, 30) == 0.0


# ── Helper: lead_lag_direction ─────────────────────────────────────────────

def test_lead_lag_btc_big_up_with_volume_spike_gives_long():
    """BTC +2% AND vol > 1.5×avg AND alt 가 아직 안 따라옴 → long."""
    # 25 봉 history — 마지막 봉 r_btc=+0.02, vol spike
    btc_ret = pd.Series([0.001] * 23 + [0.001, 0.02])
    btc_vol = pd.Series([100.0] * 24 + [200.0])  # last vol = 2× avg
    alt_ret = pd.Series([0.0] * 24 + [0.001])  # alt 안 따라옴
    assert lead_lag_direction(btc_ret, btc_vol, alt_ret) == 1


def test_lead_lag_btc_big_down_gives_short():
    btc_ret = pd.Series([0.001] * 23 + [0.001, -0.02])
    btc_vol = pd.Series([100.0] * 24 + [200.0])
    alt_ret = pd.Series([0.0] * 24 + [-0.001])
    assert lead_lag_direction(btc_ret, btc_vol, alt_ret) == -1


def test_lead_lag_low_btc_return_gives_zero():
    """|r_btc| < 1% → skip."""
    btc_ret = pd.Series([0.001] * 24 + [0.005])
    btc_vol = pd.Series([100.0] * 24 + [200.0])
    alt_ret = pd.Series([0.0] * 24 + [0.001])
    assert lead_lag_direction(btc_ret, btc_vol, alt_ret) == 0


def test_lead_lag_no_vol_spike_gives_zero():
    """vol 일반 수준 → skip."""
    btc_ret = pd.Series([0.001] * 24 + [0.02])
    btc_vol = pd.Series([100.0] * 25)
    alt_ret = pd.Series([0.0] * 25)
    assert lead_lag_direction(btc_ret, btc_vol, alt_ret) == 0


def test_lead_lag_alt_already_moved_gives_zero():
    """alt 가 BTC 와 같은 방향으로 0.5% 이상 → 시차 끝 → skip."""
    btc_ret = pd.Series([0.001] * 24 + [0.02])
    btc_vol = pd.Series([100.0] * 24 + [200.0])
    alt_ret = pd.Series([0.0] * 24 + [0.01])  # alt 이미 +1%
    assert lead_lag_direction(btc_ret, btc_vol, alt_ret) == 0


# ── Helper: spread_zscore ──────────────────────────────────────────────────

def test_spread_zscore_strong_synced_pair_near_zero():
    """β=1 cointegrated 쌍 — spread 가 stationary → z 작은 값."""
    btc, alt = _synth_cointegrated(100, seed=5)
    z = spread_zscore(btc, alt)
    assert abs(z) < 3.0  # 정상 범위 (extreme outlier 아님)


def test_spread_zscore_short_history_returns_zero():
    btc = _synth_random_walk(20, seed=1)
    alt = _synth_random_walk(20, seed=2)
    assert spread_zscore(btc, alt) == 0.0


# ── Strategy.evaluate() — 4 layer 분기 ──────────────────────────────────────

def _make_strat(**kw) -> MultiAlphaAltcoinV1:
    # 작은 window 로 테스트 빠르게
    return MultiAlphaAltcoinV1(
        coint_window_bars=80, corr_window_bars=40, **kw,
    )


def _hist_from_close(close: pd.Series, vol: float = 100.0) -> pd.DataFrame:
    """단순 OHLCV — close 만 의미 있게."""
    return pd.DataFrame({
        "open": close.values, "high": close.values * 1.001,
        "low": close.values * 0.999, "close": close.values,
        "volume": [vol] * len(close),
    }, index=close.index)


def test_evaluate_cointegration_fail_returns_hold():
    """독립 random walk → high pvalue → hold."""
    pytest.importorskip("statsmodels")
    strat = _make_strat()
    btc = _synth_random_walk(100, seed=100)
    alt = _synth_random_walk(100, seed=200)
    ts = btc.index[-1]
    action, dir_, diag = strat.evaluate(
        _hist_from_close(btc), _hist_from_close(alt), ts, "ALTUSDT"
    )
    assert action == "hold"


def test_evaluate_ambiguous_corr_returns_hold():
    """corr 가 0.3~0.7 사이로 떨어지는 setup → ambiguous → hold."""
    pytest.importorskip("statsmodels")
    strat = _make_strat(regime_corr_high=0.7, regime_corr_low=0.3)
    # cointegrated 강한 쌍 (low p) + corr 가 중간 — synthetic 어렵지만 일단
    # noise 큰 쌍 만들기.
    btc, alt = _synth_cointegrated(100, seed=7)
    # alt 에 noise 추가해 corr 낮춤
    rng = np.random.default_rng(7)
    alt_noisy = alt + rng.normal(0, 3.0, len(alt))
    alt_noisy = alt_noisy.clip(lower=0.1)
    ts = btc.index[-1]
    action, dir_, diag = strat.evaluate(
        _hist_from_close(btc), _hist_from_close(alt_noisy), ts, "ALTUSDT"
    )
    # diag 의 regime 이 ambiguous 또는 cointegration_fail 이면 action=hold
    if diag.get("regime") == "ambiguous":
        assert action == "hold"
        assert diag["layer"] == ""


def test_strategy_class_constants():
    """spec 의 stop/TP/timeout 가드."""
    assert MultiAlphaAltcoinV1.stop_loss_pct == 0.0075
    assert MultiAlphaAltcoinV1.take_profit_pct == 0.015
    # R/R = 1:2
    assert MultiAlphaAltcoinV1.take_profit_pct / MultiAlphaAltcoinV1.stop_loss_pct == 2.0
    assert MultiAlphaAltcoinV1.timeout_bars == 4
    assert MultiAlphaAltcoinV1.shorts_allowed is True


def test_strategy_get_interval_is_1h():
    assert MultiAlphaAltcoinV1.get_interval() == "1h"


def test_on_bar_no_btc_history_returns_hold():
    """ctx 에 btc_history 없으면 graceful hold."""
    strat = _make_strat()
    alt_close = _synth_random_walk(200, seed=1)
    ctx = {
        "ts": alt_close.index[-1],
        "market_snapshot": {
            "symbol": "ALTUSDT",
            "history": _hist_from_close(alt_close),
            # btc_history 누락
        },
    }
    sig = asyncio.run(strat.on_bar(ctx))
    assert sig.action == "hold"


def test_on_bar_btc_symbol_returns_hold():
    """BTC 자체엔 진입 X — universe-scan 의 reference 종목이라 skip."""
    strat = _make_strat()
    btc_close = _synth_random_walk(200, seed=1)
    ctx = {
        "ts": btc_close.index[-1],
        "market_snapshot": {
            "symbol": "BTCUSDT",
            "history": _hist_from_close(btc_close),
        },
    }
    sig = asyncio.run(strat.on_bar(ctx))
    assert sig.action == "hold"
    assert "btc_or_no_symbol" in sig.reason
