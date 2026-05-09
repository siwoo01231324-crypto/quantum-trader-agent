"""Tests for universe_dispatch_helper — orchestrator → broker glue (#218)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from brokers.base import OrderAck, OrderRequest
from portfolio.order_intent import OrderIntent
from portfolio.universe_dispatch_helper import (
    UniverseDispatchSummary,
    expand_basket_intents,
    is_basket_intent,
)


class MockBroker:
    def __init__(self):
        self.placed: list[OrderRequest] = []

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self.placed.append(req)
        return OrderAck(
            broker_order_id=f"mock-{len(self.placed)}",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="FILLED",
            ts=datetime.now(timezone.utc),
            qty=req.qty,
            price=Decimal("100"),
        )


def test_is_basket_intent_detects_suffix():
    assert is_basket_intent(OrderIntent(
        strategy_id="cs", symbol="KRX_TOP350_BASKET", side="buy",
        qty=1.0, reason="r"))
    assert is_basket_intent(OrderIntent(
        strategy_id="cs", symbol="CRYPTO_TOP30_BASKET", side="buy",
        qty=1.0, reason="r"))
    assert not is_basket_intent(OrderIntent(
        strategy_id="single", symbol="005930", side="buy",
        qty=1.0, reason="r"))
    assert not is_basket_intent(OrderIntent(
        strategy_id="single", symbol="BTCUSDT", side="buy",
        qty=1.0, reason="r"))


@pytest.mark.asyncio
async def test_expand_basket_passthrough_legacy_intents():
    """Legacy single-ticker intent 는 그대로 passthrough — basket expansion 없음."""
    legacy = OrderIntent(strategy_id="momo_kis_v1", symbol="005930",
                         side="buy", qty=10.0, reason="legacy_signal")
    orch = MagicMock()
    orch._strategies = {}
    broker = MockBroker()
    summary = await expand_basket_intents(
        [legacy], orch, broker,
        prices_provider=lambda sid, syms: pd.Series(),
        positions_provider=lambda sid: {},
        capital_provider=lambda sid: 1_000_000,
    )
    assert summary.passthrough_intents == [legacy]
    assert summary.basket_intents == []
    assert summary.rebal_reports == []
    assert broker.placed == []  # legacy 는 본 helper 가 발주 안 함


@pytest.mark.asyncio
async def test_expand_basket_routes_to_dispatch_rebalance():
    """KRX_*_BASKET intent → strategy.latest_weights 조회 → multi-symbol 발주."""
    basket = OrderIntent(strategy_id="cs_tsmom_kr_daily",
                         symbol="KRX_TOP350_BASKET",
                         side="buy", qty=1.0, reason="rebal_buy")

    # Fake strategy with latest_weights
    strategy = MagicMock()
    strategy.latest_weights = pd.Series({"005930": 0.5, "000660": 0.5})
    orch = MagicMock()
    orch._strategies = {"cs_tsmom_kr_daily": strategy}

    broker = MockBroker()
    prices = pd.Series({"005930": 80_000, "000660": 200_000})
    summary = await expand_basket_intents(
        [basket], orch, broker,
        prices_provider=lambda sid, syms: prices,
        positions_provider=lambda sid: {},
        capital_provider=lambda sid: 10_000_000,
    )
    assert summary.intent_count_in == 1
    assert len(summary.basket_intents) == 1
    assert len(summary.rebal_reports) == 1
    rep = summary.rebal_reports[0]
    assert rep.strategy_id == "cs_tsmom_kr_daily"
    # 두 종목 모두 매수 발주
    assert {r.symbol for r in broker.placed} == {"005930", "000660"}


@pytest.mark.asyncio
async def test_expand_skips_when_no_weights_yet():
    """Warmup 단계 strategy.latest_weights=None → skip (broker 호출 없음)."""
    basket = OrderIntent(strategy_id="cs_test", symbol="KRX_TOP350_BASKET",
                         side="buy", qty=1.0, reason="warmup")
    strategy = MagicMock()
    strategy.latest_weights = None
    orch = MagicMock()
    orch._strategies = {"cs_test": strategy}

    broker = MockBroker()
    summary = await expand_basket_intents(
        [basket], orch, broker,
        prices_provider=lambda sid, syms: pd.Series(),
        positions_provider=lambda sid: {},
        capital_provider=lambda sid: 1_000_000,
    )
    assert len(summary.basket_intents) == 1  # 감지됨
    assert summary.rebal_reports == []  # 그러나 expand 스킵
    assert broker.placed == []


@pytest.mark.asyncio
async def test_expand_skips_unknown_strategy():
    """orch._strategies 에 등록되지 않은 strategy_id → skip + warning."""
    basket = OrderIntent(strategy_id="ghost", symbol="KRX_TOP350_BASKET",
                         side="buy", qty=1.0, reason="r")
    orch = MagicMock()
    orch._strategies = {}
    broker = MockBroker()
    summary = await expand_basket_intents(
        [basket], orch, broker,
        prices_provider=lambda sid, syms: pd.Series(),
        positions_provider=lambda sid: {},
        capital_provider=lambda sid: 1_000_000,
    )
    assert len(summary.basket_intents) == 1
    assert summary.rebal_reports == []
    assert broker.placed == []


@pytest.mark.asyncio
async def test_expand_handles_provider_exception_gracefully():
    """prices_provider 예외 시 해당 strategy 만 skip — 다른 strategy 진행."""
    basket1 = OrderIntent(strategy_id="cs_a", symbol="KRX_TOP350_BASKET",
                          side="buy", qty=1.0, reason="r")
    basket2 = OrderIntent(strategy_id="cs_b", symbol="KRX_TOP350_BASKET",
                          side="buy", qty=1.0, reason="r")

    sa, sb = MagicMock(), MagicMock()
    sa.latest_weights = pd.Series({"005930": 1.0})
    sb.latest_weights = pd.Series({"000660": 1.0})
    orch = MagicMock()
    orch._strategies = {"cs_a": sa, "cs_b": sb}

    broker = MockBroker()

    def flaky_prices(sid, syms):
        if sid == "cs_a":
            raise RuntimeError("simulated provider failure")
        return pd.Series({"000660": 200_000})

    summary = await expand_basket_intents(
        [basket1, basket2], orch, broker,
        prices_provider=flaky_prices,
        positions_provider=lambda sid: {},
        capital_provider=lambda sid: 10_000_000,
    )
    # cs_a 는 provider 실패 → expand skip
    # cs_b 는 정상 → broker 발주
    assert len(summary.rebal_reports) == 1
    assert summary.rebal_reports[0].strategy_id == "cs_b"
    assert all(r.symbol == "000660" for r in broker.placed)


@pytest.mark.asyncio
async def test_lot_spec_inferred_from_symbol_prefix():
    """KRX_*_BASKET → KRX_LOT (1주 단위), CRYPTO_* → BINANCE_DEFAULT_LOT (소수점)."""
    from portfolio.universe_dispatch_helper import _infer_lot_spec
    from portfolio.weights_to_orders import KRX_LOT, BINANCE_DEFAULT_LOT
    assert _infer_lot_spec("KRX_TOP350_BASKET") == KRX_LOT
    assert _infer_lot_spec("CRYPTO_TOP30_BASKET") == BINANCE_DEFAULT_LOT
    assert _infer_lot_spec("UNKNOWN_BASKET") == BINANCE_DEFAULT_LOT
