"""Unit tests for Smoke1mRoundtrip — verifies env gate + alternating signal."""
from __future__ import annotations

import os

import pandas as pd
import pytest

from src.backtest.protocol import Bar
from src.backtest.strategies.smoke_1m_roundtrip import Smoke1mRoundtrip


def _bar(ts: str = "2026-05-15T09:00:00+09:00", close: float = 100.0) -> Bar:
    return Bar(
        ts=pd.Timestamp(ts),
        open=close, high=close, low=close, close=close, volume=1000.0,
    )


@pytest.fixture
def history() -> pd.DataFrame:
    # 30 bars of flat history — strategy doesn't read it.
    return pd.DataFrame({
        "open": [100.0] * 30, "high": [100.0] * 30,
        "low": [100.0] * 30, "close": [100.0] * 30, "volume": [1000.0] * 30,
    })


def test_disabled_by_default_returns_hold(monkeypatch, history):
    monkeypatch.delenv("SMOKE_TEST_ENABLED", raising=False)
    strat = Smoke1mRoundtrip()
    strat.on_init({})
    sig = strat.on_bar(_bar(), history, {"symbol": "005930"})
    assert sig.action == "hold"
    assert sig.reason == "smoke_disabled"


def test_enabled_alternates_buy_sell(monkeypatch, history):
    monkeypatch.setenv("SMOKE_TEST_ENABLED", "1")
    strat = Smoke1mRoundtrip(interval_sec=0)  # disable throttle for unit test
    strat.on_init({})
    sigs = [strat.on_bar(_bar(), history, {"symbol": "005930"}) for _ in range(4)]
    assert [s.action for s in sigs] == ["buy", "sell", "buy", "sell"]
    assert all(s.size > 0 for s in sigs)


def test_per_symbol_independent_state(monkeypatch, history):
    monkeypatch.setenv("SMOKE_TEST_ENABLED", "1")
    strat = Smoke1mRoundtrip(interval_sec=0)
    strat.on_init({})
    a = strat.on_bar(_bar(), history, {"symbol": "005930"})
    b = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    # Both first bars → buy independently.
    assert a.action == "buy"
    assert b.action == "buy"
    a2 = strat.on_bar(_bar(), history, {"symbol": "005930"})
    b2 = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    assert a2.action == "sell"
    assert b2.action == "sell"


def test_interval_throttles_rapid_ticks(monkeypatch, history):
    """#238 사용자 보고 — Binance aggTrade tick 빈도로 매번 발사 차단."""
    monkeypatch.setenv("SMOKE_TEST_ENABLED", "1")
    strat = Smoke1mRoundtrip()  # default 60s interval
    strat.on_init({})
    first = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    second = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    third = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    assert first.action == "buy"
    assert second.action == "hold"
    assert second.reason == "smoke_interval"
    assert third.action == "hold"


def test_interval_env_override(monkeypatch, history):
    monkeypatch.setenv("SMOKE_TEST_ENABLED", "1")
    monkeypatch.setenv("SMOKE_INTERVAL_SEC", "0")
    strat = Smoke1mRoundtrip()  # env-only override
    strat.on_init({})
    a = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    b = strat.on_bar(_bar(), history, {"symbol": "BTCUSDT"})
    assert a.action == "buy"
    assert b.action == "sell"


def test_size_fraction_env_override(monkeypatch, history):
    monkeypatch.setenv("SMOKE_TEST_ENABLED", "1")
    monkeypatch.setenv("SMOKE_SIZE_FRACTION", "0.05")
    strat = Smoke1mRoundtrip()
    strat.on_init({})
    sig = strat.on_bar(_bar(), history, {"symbol": "005930"})
    assert sig.size == pytest.approx(0.05)


def test_size_fraction_constructor_override_beats_env(monkeypatch, history):
    monkeypatch.setenv("SMOKE_TEST_ENABLED", "1")
    monkeypatch.setenv("SMOKE_SIZE_FRACTION", "0.05")
    strat = Smoke1mRoundtrip(size_fraction=0.02)
    strat.on_init({})
    sig = strat.on_bar(_bar(), history, {"symbol": "005930"})
    assert sig.size == pytest.approx(0.02)
