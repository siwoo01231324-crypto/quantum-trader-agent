"""BasketDispatcher (universe-scan auto-orders) — 2026-05-21 fix.

orchestrator 가 universe-scan strategy 의 Signal(symbol="CRYPTO_TOP30_BASKET")
을 silent drop 하던 사고. BasketDispatcher 가 별도 polling 으로 latest_weights
→ 종목별 dispatch_rebalance 호출하는 hook 의 단위 테스트.
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pandas as pd
import pytest

from src.live.cs_basket_dispatcher import BasketDispatcher


# ── Stub strategies / broker / orchestrator ────────────────────────────────

class _StubStrategy:
    """latest_weights attr 만 노출하는 가짜 universe-scan strategy."""
    def __init__(self, weights: pd.Series):
        self.latest_weights = weights


class _StubBroker:
    """place_order 호출 기록만 하는 가짜 broker."""
    def __init__(self):
        self.calls: list = []

    async def place_order(self, req):
        self.calls.append(req)
        return SimpleNamespace(
            client_order_id=req.client_order_id,
            status="ACKED",
            reject_reason=None,
        )


class _StubOrch:
    """orchestrator.strategies dict 만 흉내."""
    def __init__(self, strategies: dict):
        self.strategies = strategies


def _ohlcv(symbols: list[str], last_close: float = 100.0) -> dict[str, pd.DataFrame]:
    idx = pd.date_range("2026-01-01", periods=10, freq="D")
    return {s: pd.DataFrame({"close": [last_close] * 10}, index=idx) for s in symbols}


def _snapshot(*, equity_usdt: float = 0.0, equity_krw: float = 0.0) -> SimpleNamespace:
    return SimpleNamespace(equity_usdt=equity_usdt, equity_krw=equity_krw)


# ── Tests ────────────────────────────────────────────────────────────────────

class TestBasketDispatcher:
    @pytest.mark.asyncio
    async def test_no_weights_skips(self):
        """latest_weights 없는 strategy 는 무시."""
        bd = BasketDispatcher()
        broker = _StubBroker()
        # latest_weights = None
        orch = _StubOrch({"foo": _StubStrategy(None)})  # type: ignore[arg-type]
        reports = await bd.dispatch(
            orchestrator=orch, snapshot=_snapshot(equity_usdt=10000),
            broker=broker, ohlcv_history=None,
        )
        assert reports == []
        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_empty_weights_skips(self):
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({"foo": _StubStrategy(pd.Series([], dtype=float))})
        reports = await bd.dispatch(
            orchestrator=orch, snapshot=_snapshot(equity_usdt=10000),
            broker=broker, ohlcv_history=None,
        )
        assert reports == []
        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_zero_equity_skips_no_orders(self):
        weights = pd.Series({"BTCUSDT": 0.5, "ETHUSDT": 0.5})
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({"cs_tsmom_crypto_daily": _StubStrategy(weights)})
        # equity_usdt=0 → skip
        reports = await bd.dispatch(
            orchestrator=orch, snapshot=_snapshot(equity_usdt=0),
            broker=broker, ohlcv_history=_ohlcv(["BTCUSDT", "ETHUSDT"]),
        )
        assert reports == []
        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_no_prices_skips(self):
        weights = pd.Series({"BTCUSDT": 1.0})
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({"cs_tsmom_crypto_daily": _StubStrategy(weights)})
        # ohlcv_history=None → prices empty → skip
        reports = await bd.dispatch(
            orchestrator=orch, snapshot=_snapshot(equity_usdt=10000),
            broker=broker, ohlcv_history=None,
        )
        assert reports == []
        assert broker.calls == []

    @pytest.mark.asyncio
    async def test_first_dispatch_places_orders(self):
        weights = pd.Series({"BTCUSDT": 0.5, "ETHUSDT": 0.5})
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({"cs_tsmom_crypto_daily": _StubStrategy(weights)})
        reports = await bd.dispatch(
            orchestrator=orch, snapshot=_snapshot(equity_usdt=100000),
            broker=broker,
            ohlcv_history=_ohlcv(["BTCUSDT", "ETHUSDT"], last_close=100.0),
        )
        assert len(reports) == 1
        # 2 symbols × buy each → 2 place_order calls.
        assert len(broker.calls) == 2
        symbols = sorted(c.symbol for c in broker.calls)
        assert symbols == ["BTCUSDT", "ETHUSDT"]
        for c in broker.calls:
            assert str(c.side).endswith("BUY")
            assert Decimal(str(c.qty)) > 0

    @pytest.mark.asyncio
    async def test_duplicate_dispatch_skipped(self):
        """같은 weights 두 번 dispatch 시 두 번째는 cache 가 skip."""
        weights = pd.Series({"BTCUSDT": 0.5, "ETHUSDT": 0.5})
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({"cs_tsmom_crypto_daily": _StubStrategy(weights)})
        snap = _snapshot(equity_usdt=100000)
        ohlcv = _ohlcv(["BTCUSDT", "ETHUSDT"], last_close=100.0)
        r1 = await bd.dispatch(orchestrator=orch, snapshot=snap,
                                broker=broker, ohlcv_history=ohlcv)
        r2 = await bd.dispatch(orchestrator=orch, snapshot=snap,
                                broker=broker, ohlcv_history=ohlcv)
        assert len(r1) == 1
        assert r2 == []   # signature 같으므로 skip
        # 첫 dispatch 만 broker 호출.
        assert len(broker.calls) == 2

    @pytest.mark.asyncio
    async def test_weights_change_dispatches_again(self):
        """weights signature 가 바뀌면 dispatch 다시 발생."""
        bd = BasketDispatcher()
        broker = _StubBroker()
        strat = _StubStrategy(pd.Series({"BTCUSDT": 1.0}))
        orch = _StubOrch({"cs_tsmom_crypto_daily": strat})
        snap = _snapshot(equity_usdt=100000)
        ohlcv = _ohlcv(["BTCUSDT", "ETHUSDT"], last_close=100.0)
        await bd.dispatch(orchestrator=orch, snapshot=snap,
                          broker=broker, ohlcv_history=ohlcv)
        # weights 바꿈 — BTCUSDT → ETHUSDT
        strat.latest_weights = pd.Series({"ETHUSDT": 1.0})
        r2 = await bd.dispatch(orchestrator=orch, snapshot=snap,
                                broker=broker, ohlcv_history=ohlcv)
        assert len(r2) == 1
        # 첫 dispatch: BTCUSDT buy 1건. 두 번째: position_store 미연결이라
        # current={} → 이전 BTCUSDT 청산 안 함, ETHUSDT buy 1건. 총 2 calls.
        # (position_store wiring 은 별도 테스트.)
        assert len(broker.calls) == 2
        # 두 번째 호출은 ETHUSDT buy 여야.
        assert broker.calls[-1].symbol == "ETHUSDT"

    @pytest.mark.asyncio
    async def test_krx_basket_uses_krw_equity(self):
        """심볼이 USDT 가 아니면 equity_krw 참조."""
        weights = pd.Series({"005930": 0.5, "035720": 0.5})
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({"cs_tsmom_kr_daily": _StubStrategy(weights)})
        # equity_usdt 만 있으면 0 으로 잡혀 skip 됐을 텐데, KRX 는 equity_krw.
        reports = await bd.dispatch(
            orchestrator=orch,
            snapshot=_snapshot(equity_usdt=10000, equity_krw=10_000_000),
            broker=broker,
            ohlcv_history=_ohlcv(["005930", "035720"], last_close=70000.0),
        )
        assert len(reports) == 1
        assert len(broker.calls) > 0


class TestAlwaysOnGracefulNoOp:
    """2026-05-21 — env gate 제거 후 항상 활성. broker_mode 가 binance 가
    아니거나 universe-scan strategy 등록 안 됐을 때 graceful no-op 검증."""

    @pytest.mark.asyncio
    async def test_empty_strategies_no_op(self):
        bd = BasketDispatcher()
        broker = _StubBroker()
        orch = _StubOrch({})  # no strategies registered
        reports = await bd.dispatch(
            orchestrator=orch, snapshot=_snapshot(equity_usdt=10000),
            broker=broker, ohlcv_history=None,
        )
        assert reports == []
        assert broker.calls == []  # 발주 0건 — KIS 모드처럼 cs-tsmom 미등록 시
