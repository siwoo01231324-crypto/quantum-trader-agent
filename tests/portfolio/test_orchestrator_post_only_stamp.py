"""2026-05-22 post-only Maker 진입 — orchestrator 가 entry_order_type / ref_price
를 OrderIntent 에 stamp 하는지 회귀 (post-only-maker-entry.draft.md, gap A).

run_bar 가 strategy 의 ``entry_order_type`` 속성을 읽어 **BUY 진입** intent 에만
stamp 한다. ref_price 는 per-symbol ``order_price`` — executor 가
market_state.tick.last(단일 심볼만 정확) 대신 이 값으로 limit 가격을 산출하므로
멀티심볼 universe-scan 배치에서도 심볼별로 정확하다.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


def _orch(**kw) -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"), **kw)


def _ohlcv(n: int = 30, last_close: float | None = None) -> pd.DataFrame:
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    if last_close is not None:
        close[-1] = last_close
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": np.full(n, 1000.0)}, index=idx,
    )


_SNAP = {
    "equity_krw": 1_000_000.0,
    "equity_usdt": 1_000_000.0,
    "ohlcv_history": {"BTCUSDT": _ohlcv(last_close=77000.0)},
}


class _PostOnlyScanner(LiveScannerMixin):
    """entry_order_type 를 명시적으로 post_only 로 선언."""

    entry_order_type: ClassVar[str] = "post_only"

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


class _MarketScanner(LiveScannerMixin):
    """entry_order_type 미선언 → default "market" (PR1 은 mixin 에 미추가)."""

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


class _BadScanner(LiveScannerMixin):
    """잘못된 값 — orchestrator 가 market 으로 안전 강등해야 함."""

    entry_order_type: ClassVar[str] = "garbage"

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


def _run(orch: AsyncStrategyOrchestrator, ts: str = "2026-01-01") -> list:
    return asyncio.run(orch.run_bar(pd.Timestamp(ts), _SNAP))


def test_post_only_strategy_stamps_intent():
    """entry_order_type="post_only" → OrderIntent.entry_order_type + ref_price."""
    orch = _orch()
    orch.register_strategy("scan", _PostOnlyScanner())
    intents = _run(orch)
    assert len(intents) == 1
    assert intents[0].entry_order_type == "post_only"
    # ref_price = per-symbol order_price = 마지막 close (멀티심볼 정확성, gap A).
    assert intents[0].ref_price == pytest.approx(77000.0)


def test_market_strategy_default_unstamped():
    """entry_order_type 미선언 → "market", ref_price None — legacy 동작 보존."""
    orch = _orch()
    orch.register_strategy("scan", _MarketScanner())
    intents = _run(orch)
    assert len(intents) == 1
    assert intents[0].entry_order_type == "market"
    assert intents[0].ref_price is None


def test_invalid_entry_order_type_falls_back_to_market():
    """알 수 없는 값 → market 강등 (방어적)."""
    orch = _orch()
    orch.register_strategy("scan", _BadScanner())
    intents = _run(orch)
    assert len(intents) == 1
    assert intents[0].entry_order_type == "market"
    assert intents[0].ref_price is None


# ── 2026-06-29 숏 진입 post_only 확장 (_build_entry_intent 직접 검증) ──────────
# 숏 *진입*(sell & shorts_allowed) 은 post_only stamp 허용. 청산(reduce_only)은
# 항상 market — 손절 즉시성(naked-short 안전 핵심부 reduce_only 회귀 박제).

class _ShortPostOnly:
    """shorts_allowed 숏 진입 전략 — post_only 선언."""
    entry_order_type = "post_only"
    shorts_allowed = True
    stop_loss_pct = 0.01
    take_profit_pct = 0.02


class _LongOnlyPostOnly:
    """long-only 전략 (shorts_allowed 미선언) — SELL 은 청산이므로 market 강제."""
    entry_order_type = "post_only"
    stop_loss_pct = 0.01
    take_profit_pct = 0.02


def test_short_entry_post_only_stamped():
    """SELL & shorts_allowed(= 숏 진입) → post_only + ref_price stamp."""
    orch = _orch()
    intent = orch._build_entry_intent(
        strategy_id="sw", strategy=_ShortPostOnly(), symbol="SOLUSDT",
        action="sell", qty=1.0, price=100.0, reason="fire_consume:short",
    )
    assert intent.entry_order_type == "post_only"
    assert intent.ref_price == pytest.approx(100.0)
    assert intent.reduce_only is False  # 숏 진입은 reduceOnly 아님


def test_short_entry_market_when_declared_market():
    """shorts_allowed 라도 entry_order_type 미선언/market → market."""
    orch = _orch()

    class _ShortMarket:
        shorts_allowed = True
        stop_loss_pct = 0.01
        take_profit_pct = 0.02

    intent = orch._build_entry_intent(
        strategy_id="sw", strategy=_ShortMarket(), symbol="SOLUSDT",
        action="sell", qty=1.0, price=100.0, reason="r",
    )
    assert intent.entry_order_type == "market"
    assert intent.ref_price is None
    assert intent.reduce_only is False


def test_liquidation_sell_always_market_even_if_post_only():
    """⚠️ 안전 핵심부 회귀: long-only 전략의 SELL(=청산, reduce_only) 은
    entry_order_type=post_only 선언해도 절대 market — 손절 즉시성 우선."""
    orch = _orch()
    intent = orch._build_entry_intent(
        strategy_id="lo", strategy=_LongOnlyPostOnly(), symbol="SOLUSDT",
        action="sell", qty=1.0, price=100.0, reason="liquidate",
    )
    assert intent.reduce_only is True       # 청산 = reduceOnly (naked-short 차단)
    assert intent.entry_order_type == "market"  # maker 금지 (지연 시 손절 확대)
    assert intent.ref_price is None


def test_buy_entry_post_only_unchanged():
    """롱 진입 post_only 는 기존대로 (회귀)."""
    orch = _orch()

    class _LongPostOnly:
        entry_order_type = "post_only"
        stop_loss_pct = 0.01
        take_profit_pct = 0.02

    intent = orch._build_entry_intent(
        strategy_id="lo", strategy=_LongPostOnly(), symbol="SOLUSDT",
        action="buy", qty=1.0, price=100.0, reason="r",
    )
    assert intent.entry_order_type == "post_only"
    assert intent.ref_price == pytest.approx(100.0)
    assert intent.reduce_only is False
