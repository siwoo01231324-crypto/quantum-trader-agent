"""Extended tests for signals.registry — FactorSpec new fields + validation (issue #76)."""
from __future__ import annotations

import pandas as pd
import pytest


@pytest.fixture(autouse=True)
def _isolate_registry(monkeypatch):
    from signals import registry as reg_mod
    snapshot = dict(reg_mod.FACTOR_REGISTRY)
    yield
    reg_mod.FACTOR_REGISTRY.clear()
    reg_mod.FACTOR_REGISTRY.update(snapshot)


def test_alpha_horizon_bars_stored_in_factorspec():
    """alpha_horizon_bars is stored on FactorSpec after registration."""
    from signals.registry import FACTOR_REGISTRY, register

    @register("test_alpha_hz", inputs=["close"], alpha_horizon_bars=5, bar_interval="15m", signal_type="momentum")
    def _fn(close: pd.Series) -> pd.Series:
        return close

    spec = FACTOR_REGISTRY["test_alpha_hz"]
    assert spec.alpha_horizon_bars == 5
    assert spec.bar_interval == "15m"
    assert spec.signal_type == "momentum"


def test_bar_interval_default_1d():
    """Default bar_interval is '1d'."""
    from signals.registry import FACTOR_REGISTRY, register

    @register("test_bar_default", inputs=["close"])
    def _fn(close: pd.Series) -> pd.Series:
        return close

    assert FACTOR_REGISTRY["test_bar_default"].bar_interval == "1d"


def test_bar_interval_literal_reject():
    """Unknown bar_interval raises ValueError at registration time."""
    from signals.registry import register

    with pytest.raises(ValueError, match="bar_interval"):
        @register("bad_interval", inputs=["close"], bar_interval="3m")
        def _fn(close: pd.Series) -> pd.Series:
            return close


def test_signal_type_literal_reject():
    """Unknown signal_type raises ValueError at registration time."""
    from signals.registry import register

    with pytest.raises(ValueError, match="signal_type"):
        @register("bad_signal_type", inputs=["close"], signal_type="random_noise")
        def _fn(close: pd.Series) -> pd.Series:
            return close


def test_register_rejects_unknown_bar_interval():
    """Explicit test name per task spec: bar_interval='99m' must raise ValueError."""
    from signals.registry import register

    with pytest.raises(ValueError, match="bar_interval"):
        @register("reject_99m", inputs=["close"], bar_interval="99m")
        def _fn(close: pd.Series) -> pd.Series:
            return close


def test_valid_bar_intervals_all_accepted():
    """All valid bar_interval values must register without error."""
    from signals.registry import register

    valid_intervals = ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]
    for interval in valid_intervals:
        name = f"test_valid_{interval.replace('m', 'min').replace('h', 'hr').replace('d', 'day').replace('w', 'wk')}"

        @register(name, inputs=["close"], bar_interval=interval)
        def _fn(close: pd.Series) -> pd.Series:
            return close


def test_valid_signal_types_all_accepted():
    """All valid signal_type values register without error."""
    from signals.registry import register

    valid_types = ["momentum", "mean_reversion", "volatility", "trend",
                   "breakout", "event", "value", "vol", "unknown"]
    for i, stype in enumerate(valid_types):
        name = f"test_stype_{i}"

        @register(name, inputs=["close"], signal_type=stype)
        def _fn(close: pd.Series) -> pd.Series:
            return close


def test_factorspec_default_alpha_horizon_bars_is_1():
    """Default alpha_horizon_bars is 1."""
    from signals.registry import FACTOR_REGISTRY, register

    @register("test_default_ahb", inputs=["close"])
    def _fn(close: pd.Series) -> pd.Series:
        return close

    assert FACTOR_REGISTRY["test_default_ahb"].alpha_horizon_bars == 1
