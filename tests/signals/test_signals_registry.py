"""Tests for src/signals/registry.py — factor registry + compute dispatch."""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    """Snapshot and restore FACTOR_REGISTRY for each test to avoid cross-test pollution."""
    from signals import registry as reg_mod

    snapshot = dict(reg_mod.FACTOR_REGISTRY)
    yield
    reg_mod.FACTOR_REGISTRY.clear()
    reg_mod.FACTOR_REGISTRY.update(snapshot)


def test_register_and_lookup():
    from signals.registry import FACTOR_REGISTRY, register

    @register("dummy_a", inputs=["close"], window=5)
    def _dummy(close: pd.Series, window: int = 5) -> pd.Series:
        return close.rolling(window).mean()

    assert "dummy_a" in FACTOR_REGISTRY
    spec = FACTOR_REGISTRY["dummy_a"]
    assert spec.name == "dummy_a"
    assert spec.inputs == ["close"]
    assert spec.default_params == {"window": 5}
    assert callable(spec.func)


def test_register_returns_original_callable():
    """Decorator must return a callable equivalent to the original function."""
    from signals.registry import register

    @register("dummy_ret", inputs=["close"])
    def _fn(close: pd.Series) -> pd.Series:
        return close * 2

    s = pd.Series([1.0, 2.0, 3.0])
    out = _fn(s)
    assert list(out) == [2.0, 4.0, 6.0]


def test_duplicate_rejection():
    from signals.registry import register

    @register("dup_name", inputs=["close"])
    def _first(close: pd.Series) -> pd.Series:
        return close

    with pytest.raises(ValueError, match="dup_name"):
        @register("dup_name", inputs=["close"])
        def _second(close: pd.Series) -> pd.Series:
            return close


def test_unknown_name_error():
    from signals.registry import compute

    with pytest.raises(KeyError, match="nonexistent"):
        compute("nonexistent", close=pd.Series([1.0, 2.0]))


def test_compute_delegates_to_function():
    from signals.registry import compute, register

    @register("dummy_del", inputs=["close"], multiplier=3)
    def _fn(close: pd.Series, multiplier: int = 3) -> pd.Series:
        return close * multiplier

    result = compute("dummy_del", close=pd.Series([1.0, 2.0, 3.0]))
    assert list(result) == [3.0, 6.0, 9.0]


def test_list_registered_factors():
    from signals.registry import list_factors, register

    @register("dummy_list_a", inputs=["close"])
    def _a(close: pd.Series) -> pd.Series:
        return close

    @register("dummy_list_b", inputs=["close"])
    def _b(close: pd.Series) -> pd.Series:
        return close

    names = list_factors()
    assert "dummy_list_a" in names
    assert "dummy_list_b" in names
    assert names == sorted(names), "list_factors() must return sorted names"


def test_compute_forwards_only_declared_inputs():
    """Architect edit 5: compute() must drop kwargs not in the function signature.

    A factor declared with inputs=["close"] must not receive extra "high", "low", etc.
    Without this filter, a factor like compute_sma(close, window=20) would TypeError
    when the engine blindly passes all OHLCV columns.
    """
    from signals.registry import compute, register

    @register("fake_close_only", inputs=["close"])
    def _fn(close: pd.Series) -> pd.Series:
        return close

    close = pd.Series([1.0, 2.0, 3.0])
    high = pd.Series([1.1, 2.1, 3.1])

    # Must NOT raise TypeError — 'high' has to be filtered out before calling _fn.
    result = compute("fake_close_only", close=close, high=high)
    assert list(result) == [1.0, 2.0, 3.0]


def test_default_factor_set_constant():
    from signals.registry import DEFAULT_FACTOR_SET

    assert DEFAULT_FACTOR_SET == "v1"


def test_register_validates_inputs_match_signature():
    """@register must reject when declared `inputs` don't match the function signature."""
    from signals.registry import register

    # Declaring inputs=["close", "volume"] but function only accepts `close` must fail
    with pytest.raises(ValueError, match="volume"):
        @register("sig_mismatch", inputs=["close", "volume"])
        def _fn(close: pd.Series) -> pd.Series:
            return close


def test_rsi_registered_after_import():
    """Importing src.signals triggers RSI registration."""
    import signals  # noqa: F401 — triggers package __init__ that imports rsi
    from signals.registry import FACTOR_REGISTRY

    assert "rsi" in FACTOR_REGISTRY
    spec = FACTOR_REGISTRY["rsi"]
    assert spec.inputs == ["close"]
    assert spec.default_params.get("period") == 14 or spec.default_params.get("window") == 14
