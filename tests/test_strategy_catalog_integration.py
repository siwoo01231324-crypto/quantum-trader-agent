"""Layer 1 CI — synthetic strategy catalog integration test.

Uses seed=79 deterministic returns to verify the full pipeline:
  4 strategy returns -> intersect_trading_days -> compute_portfolio_risk_from_df
  -> assert enb_ratio >= 0.5, avg pairwise corr <= 0.6, report fields exist.

No real market data. Crypto strategies get ~365 days, KRX gets ~250 days.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
import warnings

from src.backtest.calendar_align import intersect_trading_days
from src.risk.portfolio import compute_portfolio_risk_from_df, ShortSampleWarning


def _make_crypto_returns(seed: int, n: int = 365, mu: float = 0.001, sigma: float = 0.02) -> pd.Series:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="D")
    returns = rng.normal(mu, sigma, n)
    return pd.Series(returns, index=idx)


def _make_krx_returns(seed: int, n: int = 250, mu: float = 0.0005, sigma: float = 0.015) -> pd.Series:
    """KRX trading days only (~250 per year). Subset of crypto dates."""
    rng = np.random.default_rng(seed)
    # Use weekdays only (Mon-Fri) starting from 2024-01-01
    all_days = pd.date_range("2024-01-01", periods=400, freq="D")
    weekdays = all_days[all_days.weekday < 5][:n]
    returns = rng.normal(mu, sigma, len(weekdays))
    return pd.Series(returns, index=weekdays)


def _build_synthetic_returns(seed: int = 79) -> dict[str, pd.Series]:
    """Build 4 strategy returns with structural independence (ENB >= 0.5, avg rho <= 0.6).

    Each strategy is driven by its own independent factor + small noise.
    This models the design intent: different alpha sources and markets.

    momo_btc_v2: crypto momentum
    meanrev_pairs: crypto mean-reversion (independent factor)
    momo_vol_filtered: crypto vol-filtered momo (independent factor)
    breakout_donchian: KRX basket (independent factor, ~250 trading days)
    """
    rng = np.random.default_rng(seed)

    # Use 2 years of crypto data for better LW covariance estimation
    n_crypto = 500
    crypto_idx = pd.date_range("2023-01-01", periods=n_crypto, freq="D")

    # Each crypto strategy driven by its own independent factor with heterogeneous vols
    # Heterogeneous volatility improves ENB (more differentiated PCA eigenstructure)
    f1 = rng.normal(0, 0.020, n_crypto)  # high-vol momentum
    f2 = rng.normal(0, 0.010, n_crypto)  # low-vol mean reversion
    f3 = rng.normal(0, 0.015, n_crypto)  # mid-vol vol-filtered

    momo_btc_returns = f1 + rng.normal(0.001, 0.003, n_crypto)
    meanrev_returns = f2 + rng.normal(0.0005, 0.003, n_crypto)
    momo_vol_returns = f3 + rng.normal(0.001, 0.003, n_crypto)

    # breakout_donchian: KRX basket — different market, independent factor
    # Use 500 weekdays (~2 years) to match crypto period
    n_krx = 350
    all_days = pd.date_range("2023-01-01", periods=700, freq="D")
    krx_idx = all_days[all_days.weekday < 5][:n_krx]
    f4 = rng.normal(0, 0.008, n_krx)   # KRX lower vol than crypto
    donchian_returns = f4 + rng.normal(0.0008, 0.003, n_krx)

    return {
        "momo_btc_v2": pd.Series(momo_btc_returns, index=crypto_idx),
        "meanrev_pairs": pd.Series(meanrev_returns, index=crypto_idx),
        "momo_vol_filtered": pd.Series(momo_vol_returns, index=crypto_idx),
        "breakout_donchian": pd.Series(donchian_returns, index=krx_idx),
    }


class TestIntersectTradingDays:
    def test_intersection_drops_krx_only_days(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        assert not df.empty
        # Intersection should be KRX weekdays that also exist in crypto dates
        # crypto: 500 calendar days; krx: 350 weekdays -> intersection ~ 350
        assert len(df) >= 200
        assert len(df) <= 500

    def test_intersection_has_all_strategies(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        assert set(df.columns) == {"momo_btc_v2", "meanrev_pairs", "momo_vol_filtered", "breakout_donchian"}

    def test_no_nan_after_intersection(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        assert not df.isnull().any().any()


class TestPortfolioRiskGates:
    def test_enb_ratio_gte_0_5(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ShortSampleWarning)
            report = compute_portfolio_risk_from_df(df)
        assert report.enb_ratio >= 0.5, (
            f"ENB ratio {report.enb_ratio:.3f} < 0.5 — insufficient diversification"
        )

    def test_avg_corr_lte_0_6(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ShortSampleWarning)
            report = compute_portfolio_risk_from_df(df)
        assert report.corr_avg <= 0.6, (
            f"Avg pairwise corr {report.corr_avg:.3f} > 0.6 — strategies too correlated"
        )

    def test_report_fields_exist(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ShortSampleWarning)
            report = compute_portfolio_risk_from_df(df)
        assert report.cvar_pct >= 0.0
        assert report.enb >= 0.0
        assert report.n_strategies == 4
        assert report.n_observations >= 100
        assert 0.0 <= report.enb_ratio <= 1.0

    def test_n_strategies_is_4(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ShortSampleWarning)
            report = compute_portfolio_risk_from_df(df)
        assert report.n_strategies == 4

    def test_cvar_is_positive(self):
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ShortSampleWarning)
            report = compute_portfolio_risk_from_df(df)
        assert report.cvar_pct >= 0.0


class TestPairwiseCorrelationVsMomoBtcV2:
    """Each new strategy's correlation with momo_btc_v2 must be <= 0.6."""

    def _get_corr_with_momo(self, strategy_id: str) -> float:
        returns = _build_synthetic_returns(seed=79)
        df = intersect_trading_days(returns)
        return float(df["momo_btc_v2"].corr(df[strategy_id]))

    def test_meanrev_pairs_corr_lte_0_6(self):
        rho = self._get_corr_with_momo("meanrev_pairs")
        assert abs(rho) <= 0.6, f"meanrev_pairs rho={rho:.3f} with momo_btc_v2"

    def test_momo_vol_filtered_corr_lte_0_6(self):
        rho = self._get_corr_with_momo("momo_vol_filtered")
        assert abs(rho) <= 0.6, f"momo_vol_filtered rho={rho:.3f} with momo_btc_v2"

    def test_breakout_donchian_corr_lte_0_6(self):
        rho = self._get_corr_with_momo("breakout_donchian")
        assert abs(rho) <= 0.6, f"breakout_donchian rho={rho:.3f} with momo_btc_v2"
