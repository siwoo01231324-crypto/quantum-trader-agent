"""Phase 3 — orchestrator.run_bar 가 strategy.get_universe() 안의 symbol 만 dispatch.

회귀 안전: get_universe 미선언 (legacy) 전략은 전체 universe 그대로.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio._async_orchestrator import AsyncStrategyOrchestrator


class _RecordingStrategy(LiveScannerMixin):
    """매 on_bar 호출의 symbol 기록."""
    is_live_scanner: ClassVar[bool] = True

    def __init__(self, universe: list[str] | None = None):
        self._universe = universe
        self.seen_symbols: list[str] = []

    @classmethod
    def get_universe(cls):
        raise NotImplementedError

    async def on_bar(self, ctx):
        sym = ctx["market_snapshot"]["symbol"]
        self.seen_symbols.append(sym)
        return Signal(action="hold", size=0.0, reason="ok")


def _make_strategy(universe: list[str] | None):
    """클래스 생성 — get_universe 가 instance 의 _universe 반환.

    test isolation: 서브클래스를 동적으로 만들어 부모 LiveScannerMixin 의
    classmethod 가 오염되지 않게.
    """
    strat = _RecordingStrategy(universe=universe)
    if universe is not None:
        sub = type(
            f"_Recording_{id(universe)}",
            (_RecordingStrategy,),
            {"get_universe": classmethod(lambda cls: universe)},
        )
        strat.__class__ = sub
    return strat


def _hist():
    """더미 OHLCV — 봉 1개."""
    return pd.DataFrame({
        "open": [100], "high": [101], "low": [99],
        "close": [100], "volume": [1000],
    })


@pytest.mark.asyncio
async def test_filter_applied_when_get_universe_declared():
    """get_universe=[A, B] → universe={A,B,C,D} 중 A,B 만 dispatch."""
    universe_ohlcv = {sym: _hist() for sym in ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]}
    snapshot = {"ohlcv_history": universe_ohlcv}

    strat = _make_strategy(["BTCUSDT", "ETHUSDT"])

    from risk.dsl import Policy
    orch = AsyncStrategyOrchestrator(policy=Policy(policy_version=1, name="t"))
    orch.register_strategy("test", strat)

    await orch.run_bar(ts=None, market_snapshot=snapshot)
    assert sorted(strat.seen_symbols) == ["BTCUSDT", "ETHUSDT"]


@pytest.mark.asyncio
async def test_no_filter_when_get_universe_not_defined():
    """기존 LiveScannerMixin default get_universe = BINANCE_USDT_TOP30.

    universe 의 모든 symbol 이 TOP30 안에 있으면 그대로 dispatch.
    """
    from src.portfolio.binance_universe import BINANCE_USDT_TOP30

    # TOP30 안의 처음 3 개만 fake universe 로
    sample = list(BINANCE_USDT_TOP30)[:3]
    universe_ohlcv = {s: _hist() for s in sample}
    snapshot = {"ohlcv_history": universe_ohlcv}

    strat = _RecordingStrategy()  # universe override 없음 → default TOP30 사용

    from risk.dsl import Policy
    orch = AsyncStrategyOrchestrator(policy=Policy(policy_version=1, name="t"))
    orch.register_strategy("test", strat)

    await orch.run_bar(ts=None, market_snapshot=snapshot)
    assert sorted(strat.seen_symbols) == sorted(sample)


@pytest.mark.asyncio
async def test_universe_outside_filter_excluded():
    """universe 에 있지만 get_universe() 에 없는 symbol = skip."""
    universe_ohlcv = {
        "BTCUSDT": _hist(),
        "XAGUSDT": _hist(),  # daemon top-100 에 있을 수 있지만 strategy universe 에 없음
        "FILUSDT": _hist(),
    }
    snapshot = {"ohlcv_history": universe_ohlcv}

    strat = _make_strategy(["BTCUSDT"])

    from risk.dsl import Policy
    orch = AsyncStrategyOrchestrator(policy=Policy(policy_version=1, name="t"))
    orch.register_strategy("test", strat)

    await orch.run_bar(ts=None, market_snapshot=snapshot)
    assert strat.seen_symbols == ["BTCUSDT"]
