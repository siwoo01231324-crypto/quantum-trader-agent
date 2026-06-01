"""Regression — bidir 전략의 SELL 시그널은 reduce_only=False 로 stamp.

#238 Item 7 가 ``reduce_only=(signal.action == "sell")`` 로 stamp 하던 게
airborne v1.2 (long + short bidir) 의 short 진입 시그널까지 reduce_only=True
로 보내 Binance Futures 가 "reduceOnly with no position" (-2022) 거부 →
2026-05-28 ~ 06-01 사이 13K+ sell 시그널 전량 silent REJECTED → 자동매매 0건.

Fix: strategy 에 ``shorts_allowed: ClassVar[bool] = True`` 선언 → orchestrator
가 그 case 만 reduce_only=False 로 stamp. 미선언 (default) 전략은
``not getattr(strat, "shorts_allowed", False) == True`` 라 기존 동작
byte-identical (long-only 전략 sell = exit, reduce_only=True 유지).
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pytest

from backtest.protocol import Signal
from portfolio._async_orchestrator import AsyncStrategyOrchestrator
from risk.dsl import Policy


class _LongOnlyStub:
    """기존 long-only 전략 모사 — shorts_allowed 선언 안 함."""

    is_live_scanner: ClassVar[bool] = False

    def __init__(self, action: str):
        self._action = action

    async def on_bar(self, ctx):
        return Signal(action=self._action, size=0.05, reason="stub")


class _BidirStub(_LongOnlyStub):
    """bidir 전략 모사 — shorts_allowed=True 선언."""

    shorts_allowed: ClassVar[bool] = True


def _build_orch(strategy):
    orch = AsyncStrategyOrchestrator(policy=Policy(policy_version=1, name="t"))
    orch.register_strategy("test", strategy)
    return orch


def _market_snapshot():
    """단일 종목 단일 가격 — orchestrator 가 OrderIntent 만들기 위한 최소 입력."""
    return {
        "symbol": "BTCUSDT",
        "price": 100.0,
        "equity_usdt": 1000.0,
        "equity_krw": 0.0,
        "factors": {},
    }


@pytest.mark.asyncio
async def test_long_only_sell_keeps_reduce_only_true():
    """기존 long-only 전략의 SELL → reduce_only=True (byte-identical)."""
    strat = _LongOnlyStub(action="sell")
    orch = _build_orch(strat)

    intents = await orch.run_bar(ts=None, market_snapshot=_market_snapshot())
    assert intents, "long-only sell — intent 생성 자체는 일어나야 (exit path)"
    intent = intents[0]
    assert intent.side == "sell"
    assert intent.reduce_only is True, (
        f"long-only 전략의 SELL 은 exit 이므로 reduce_only=True 유지해야 한다 "
        f"(naked short 방지 가드). got reduce_only={intent.reduce_only!r}"
    )


@pytest.mark.asyncio
async def test_bidir_sell_uses_reduce_only_false():
    """shorts_allowed=True 선언한 bidir 전략의 SELL → reduce_only=False.

    이게 안 되면 Binance Futures 가 -2022 로 short 진입을 거부 → silent
    REJECTED → WAL order_acked 0건 → 자동매매 0건 회귀 (2026-05-28 ~ 06-01).
    """
    strat = _BidirStub(action="sell")
    orch = _build_orch(strat)

    intents = await orch.run_bar(ts=None, market_snapshot=_market_snapshot())
    assert intents
    intent = intents[0]
    assert intent.side == "sell"
    assert intent.reduce_only is False, (
        f"shorts_allowed=True 전략의 SELL 은 short 진입 가능해야 — reduce_only "
        f"=False 로 stamp 되어야 한다. got reduce_only={intent.reduce_only!r}. "
        f"reduce_only=True 면 Binance -2022 거부 → airborne 자동매매 0건 회귀."
    )


@pytest.mark.asyncio
async def test_bidir_buy_reduce_only_remains_false():
    """BUY 는 어차피 reduce_only=False — bidir 든 long-only 든 동일 (양쪽 확인)."""
    for strat_cls in (_LongOnlyStub, _BidirStub):
        strat = strat_cls(action="buy")
        orch = _build_orch(strat)
        intents = await orch.run_bar(ts=None, market_snapshot=_market_snapshot())
        assert intents
        assert intents[0].side == "buy"
        assert intents[0].reduce_only is False, (
            f"{strat_cls.__name__}: BUY 는 reduce_only=False (long 진입)"
        )


def test_airborne_strategy_declares_shorts_allowed():
    """LiveAirborneBbReversalKstMorning + 자식 KstHours 가 shorts_allowed=True
    명시해야 함 — 자식이 부모 ClassVar 상속받는지도 검증."""
    from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
        LiveAirborneBbReversalKstMorning,
    )
    from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
        LiveAirborneBbReversalKstHours,
    )

    assert LiveAirborneBbReversalKstMorning.shorts_allowed is True
    assert LiveAirborneBbReversalKstHours.shorts_allowed is True
