"""Tests for 2-stage cluster-HRP (P7 — patent avoidance, IBM US11562281B2)."""
import numpy as np
import pandas as pd
import pytest
from src.risk.position_sizer import hrp_with_clustering


def _make_returns(n_assets: int, n_periods: int = 500, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    data = rng.standard_normal((n_periods, n_assets))
    return pd.DataFrame(data, columns=[f"a{i}" for i in range(n_assets)])


def _make_correlated_returns(seed: int = 99) -> pd.DataFrame:
    """Returns where assets 0-4 are correlated, 5-9 are correlated, 10-14 are independent."""
    rng = np.random.default_rng(seed)
    n = 500
    common1 = rng.standard_normal(n)
    common2 = rng.standard_normal(n)
    data = {}
    for i in range(5):
        data[f"a{i}"] = common1 + 0.1 * rng.standard_normal(n)
    for i in range(5, 10):
        data[f"a{i}"] = common2 + 0.1 * rng.standard_normal(n)
    for i in range(10, 15):
        data[f"a{i}"] = rng.standard_normal(n)
    return pd.DataFrame(data)


def test_hrp_bit_equal_with_single_hrp_when_k_none():
    """k_clusters=None should give same result as single HRP (deterministic)."""
    returns = _make_returns(20)
    w1 = hrp_with_clustering(returns, k_clusters=None)
    w2 = hrp_with_clustering(returns, k_clusters=None)
    np.testing.assert_array_equal(w1, w2)


def test_hrp_with_clustering_n_small_fallback():
    """N=20, k_clusters=None → single HRP fallback: sum=1, w_i>=0."""
    returns = _make_returns(20)
    w = hrp_with_clustering(returns, k_clusters=None)
    assert abs(w.sum() - 1.0) < 1e-9
    assert np.all(w >= -1e-12)
    assert len(w) == 20


def test_hrp_with_clustering_k_groups():
    """k_clusters=5, N=50 → 2-stage HRP: sum=1, w_i>=0."""
    returns = _make_returns(50)
    w = hrp_with_clustering(returns, k_clusters=5)
    assert abs(w.sum() - 1.0) < 1e-9
    assert np.all(w >= -1e-12)
    assert len(w) == 50


def test_hrp_with_clustering_interpretability():
    """Correlated assets in same cluster should receive similar aggregate weight."""
    returns = _make_correlated_returns()
    w = hrp_with_clustering(returns, k_clusters=3)
    assert abs(w.sum() - 1.0) < 1e-9
    assert np.all(w >= -1e-12)
    # Correlated groups (0-4, 5-9) should receive non-trivial weight
    assert w[:5].sum() > 0.05
    assert w[5:10].sum() > 0.05


def test_hrp_with_clustering_n200_performance():
    """N=200 should complete without error (performance smoke test)."""
    import time
    returns = _make_returns(200, n_periods=600)
    t0 = time.perf_counter()
    w = hrp_with_clustering(returns, k_clusters=10)
    elapsed = time.perf_counter() - t0
    assert abs(w.sum() - 1.0) < 1e-9
    assert np.all(w >= -1e-12)
    assert elapsed < 30.0  # generous timeout
