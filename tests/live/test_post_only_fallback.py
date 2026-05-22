"""post-only Maker 진입 미체결 fallback 회귀 (post-only-maker-entry.draft.md 3단계).

박제 대상 (실측 근거 — 라이브에서만 터지는 경로):
  - NEW → cancel → 전량 시장가 재발주
  - PARTIALLY_FILLED → cancel → **잔량만** 시장가 재발주 (부분 체결 처리)
  - EXPIRED → 즉시 시장가 재발주 (대기 없음)
  - cancel-race (gap D) — cancel 실패 → re-GET FILLED → 재발주 금지 (중복 차단)
  - cancel 실패 + 주문 아직 활성 → 재발주 포기 (dup-order guard)
  - 완전 미체결 + 시장가도 REJECTED → on_entry_unfilled 콜백 (_live_entered 해제)
  - 부분 체결 + 시장가 REJECTED → release 안 함 (포지션 보유)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from prometheus_client import CollectorRegistry

import src.live.post_only_fallback as pof
from src.brokers.base import OrderAck, OrderType
from src.brokers.errors import BrokerError
from src.execution.base import TimeInForce
from src.live.conversion import intent_to_order_request
from src.live.executor import _build_order_request, execute_intents
from src.live.post_only_fallback import (
    cancel_pending_fallbacks,
    is_post_only,
    resubmit_post_only_as_market,
    run_post_only_fallback,
)
from src.live.wal import WAL, replay
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio.order_intent import OrderIntent


# ---------------------------------------------------------------------------
# Fixtures + test double
# ---------------------------------------------------------------------------

@pytest.fixture
def wal(tmp_path):
    return WAL(tmp_path / "wal.jsonl")


@pytest.fixture
def ks():
    return KillSwitch()


@pytest.fixture
def metrics():
    return Metrics(registry=CollectorRegistry())


@pytest.fixture(autouse=True)
async def _drain_background_tasks():
    """테스트가 남긴 fallback background task 정리 — 테스트 간 격리."""
    yield
    tasks = list(pof._BACKGROUND_TASKS)
    for t in tasks:
        if not t.done():
            t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def _now():
    return datetime.now(timezone.utc)


class FakeBroker:
    """설정 가능한 AsyncBrokerAdapter 테스트 더블.

    - place_order: LIMIT 은 ``limit_status``, MARKET 은 ``market_status`` 반환.
    - get_order: ``get_queue`` 의 (status, filled_qty) 를 호출 순서대로 소비
      (소진 시 마지막 값 반복) — cancel-race 시뮬레이션용 멀티 응답.
    - cancel_order: ``cancel_raises`` 면 BrokerError (이미 체결/소멸 시뮬레이션).
    """

    name = "fake"
    paper = False

    def __init__(self) -> None:
        self.place_calls: list = []
        self.cancel_calls: list = []
        self.get_calls: list = []
        self.limit_status = "NEW"
        self.market_status = "FILLED"
        self.get_queue: list[tuple[str, Decimal]] = [("NEW", Decimal("0"))]
        self._get_idx = 0
        self.cancel_raises = False
        self.orig_qty = Decimal("0.001")

    async def place_order(self, req):
        self.place_calls.append(req)
        is_limit = req.order_type == OrderType.LIMIT
        status = self.limit_status if is_limit else self.market_status
        return OrderAck(
            broker_order_id=f"bo{len(self.place_calls)}",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status=status,
            ts=_now(),
            qty=req.qty,
        )

    async def get_order(self, *, symbol, client_order_id=None, broker_order_id=None):
        self.get_calls.append(client_order_id)
        idx = min(self._get_idx, len(self.get_queue) - 1)
        self._get_idx += 1
        status, filled = self.get_queue[idx]
        return OrderAck(
            broker_order_id="bo",
            client_order_id=client_order_id or "",
            symbol=symbol,
            status=status,
            ts=_now(),
            qty=self.orig_qty,
            filled_qty=filled,
        )

    async def cancel_order(self, *, symbol, client_order_id=None, broker_order_id=None):
        self.cancel_calls.append(client_order_id)
        if self.cancel_raises:
            raise BrokerError("order already filled / unknown")


def _intent(symbol: str = "BTCUSDT", qty: float = 0.001) -> OrderIntent:
    return OrderIntent(
        strategy_id="s1", symbol=symbol, side="buy", qty=qty,
        reason="test", entry_order_type="post_only", ref_price=77000.0,
    )


def _post_only_req(coid: str = "coid1"):
    return intent_to_order_request(
        _intent(), idempotency_key=coid,
        order_type=OrderType.LIMIT, price=Decimal("76961.5"), tif=TimeInForce.GTX,
    )


def _market_places(broker: FakeBroker) -> list:
    return [r for r in broker.place_calls if r.order_type == OrderType.MARKET]


def _fallback_outcomes(wal: WAL) -> list[str]:
    events, _ = replay(wal.path)
    return [
        e.payload.get("outcome")
        for e in events
        if e.event_type == "post_only_fallback"
    ]


async def _run_fallback(broker, ks, wal, metrics, **kw):
    await run_post_only_fallback(
        _intent(), _post_only_req(),
        broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
        market_state=None, position_store=None, fallback_sec=0.0, **kw,
    )


# ---------------------------------------------------------------------------
# _build_order_request / is_post_only
# ---------------------------------------------------------------------------

def test_build_order_request_post_only_produces_gtx_limit():
    req = _build_order_request(_intent(), "coidX")
    assert req.order_type == OrderType.LIMIT
    assert req.tif == TimeInForce.GTX
    # buy → ref × (1 - 0.0005) = 77000 × 0.9995 = 76961.5
    assert req.price == Decimal("76961.5")


def test_build_order_request_market_default():
    intent = OrderIntent(strategy_id="s", symbol="BTCUSDT", side="buy",
                         qty=0.001, reason="r")
    req = _build_order_request(intent, "c")
    assert req.order_type == OrderType.MARKET
    assert req.price is None


def test_build_order_request_post_only_without_ref_price_degrades_to_market():
    """ref_price None (orchestrator 산출 실패) → 안전하게 MARKET 강등."""
    intent = OrderIntent(strategy_id="s", symbol="BTCUSDT", side="buy", qty=0.001,
                         reason="r", entry_order_type="post_only", ref_price=None)
    req = _build_order_request(intent, "c")
    assert req.order_type == OrderType.MARKET
    assert req.price is None


def test_is_post_only():
    assert is_post_only(_post_only_req()) is True
    market = intent_to_order_request(
        OrderIntent(strategy_id="s", symbol="BTCUSDT", side="buy", qty=0.001,
                    reason="r"),
        idempotency_key="c",
    )
    assert is_post_only(market) is False


# ---------------------------------------------------------------------------
# run_post_only_fallback — NEW / PARTIALLY_FILLED / EXPIRED-after-NEW
# ---------------------------------------------------------------------------

async def test_new_unfilled_cancel_then_full_market(wal, ks, metrics):
    """NEW 미체결 → cancel → 전량 시장가 재발주."""
    broker = FakeBroker()
    broker.get_queue = [("NEW", Decimal("0")), ("CANCELED", Decimal("0"))]
    await _run_fallback(broker, ks, wal, metrics)

    assert len(broker.cancel_calls) == 1
    market = _market_places(broker)
    assert len(market) == 1
    assert market[0].qty == Decimal("0.001")  # 전량
    assert _fallback_outcomes(wal) == ["resubmitted_market"]


async def test_partial_fill_resubmits_remaining_only(wal, ks, metrics):
    """PARTIALLY_FILLED → cancel → **잔량만** 시장가 재발주 (partial fill)."""
    broker = FakeBroker()
    broker.orig_qty = Decimal("0.005")
    broker.get_queue = [
        ("PARTIALLY_FILLED", Decimal("0.002")),
        ("CANCELED", Decimal("0.002")),
    ]
    await _run_fallback(broker, ks, wal, metrics)

    assert len(broker.cancel_calls) == 1
    market = _market_places(broker)
    assert len(market) == 1
    # 잔량 = origQty 0.005 − executedQty 0.002 = 0.003
    assert market[0].qty == Decimal("0.003")
    assert _fallback_outcomes(wal) == ["resubmitted_market"]


async def test_filled_as_maker_no_resubmit(wal, ks, metrics):
    """fallback 시점 이미 FILLED (maker 체결) → cancel/재발주 없음."""
    broker = FakeBroker()
    broker.get_queue = [("FILLED", Decimal("0.001"))]
    await _run_fallback(broker, ks, wal, metrics)

    assert broker.cancel_calls == []
    assert _market_places(broker) == []
    assert _fallback_outcomes(wal) == ["filled_maker"]


async def test_expired_after_new_resubmits_market(wal, ks, metrics):
    """NEW 였다가 거래소측 EXPIRED → 미체결분 시장가."""
    broker = FakeBroker()
    broker.get_queue = [("EXPIRED", Decimal("0"))]
    await _run_fallback(broker, ks, wal, metrics)

    assert broker.cancel_calls == []  # 이미 종결 — cancel 불필요
    assert len(_market_places(broker)) == 1


# ---------------------------------------------------------------------------
# cancel-race (gap D) + dup-order guard
# ---------------------------------------------------------------------------

async def test_cancel_race_filled_during_cancel_no_resubmit(wal, ks, metrics):
    """cancel 실패 → re-GET FILLED → 재발주 금지 (중복 포지션 차단)."""
    broker = FakeBroker()
    broker.get_queue = [("NEW", Decimal("0")), ("FILLED", Decimal("0.001"))]
    broker.cancel_raises = True
    await _run_fallback(broker, ks, wal, metrics)

    assert len(broker.cancel_calls) == 1  # cancel 시도는 함
    assert _market_places(broker) == []   # 그러나 재발주 안 함
    assert _fallback_outcomes(wal) == ["filled_during_cancel"]


async def test_cancel_fails_order_still_live_aborts(wal, ks, metrics):
    """cancel 실패 + 주문 아직 NEW → 재발주 포기 (dup-order guard)."""
    broker = FakeBroker()
    broker.get_queue = [("NEW", Decimal("0")), ("NEW", Decimal("0"))]
    broker.cancel_raises = True
    await _run_fallback(broker, ks, wal, metrics)

    assert len(broker.cancel_calls) == 1
    assert _market_places(broker) == []  # 살아있는 주문 — 재발주하면 중복
    assert _fallback_outcomes(wal) == ["cancel_failed_abort"]


async def test_get_order_unavailable_aborts(wal, ks, metrics):
    """fallback 시점 get_order 자체가 실패 → 보수적으로 포기."""
    broker = FakeBroker()

    async def _boom(**kw):
        raise BrokerError("network")

    broker.get_order = _boom
    await _run_fallback(broker, ks, wal, metrics)

    assert broker.cancel_calls == []
    assert _market_places(broker) == []


# ---------------------------------------------------------------------------
# _live_entered 해제 (4단계)
# ---------------------------------------------------------------------------

async def test_total_miss_releases_live_entered(wal, ks, metrics):
    """완전 미체결 + 시장가 재발주도 REJECTED → on_entry_unfilled 호출."""
    broker = FakeBroker()
    broker.get_queue = [("NEW", Decimal("0")), ("CANCELED", Decimal("0"))]
    ks.trip(reason="test", source="manual")  # 시장가 재발주 → REJECTED
    released: list = []
    await _run_fallback(
        broker, ks, wal, metrics,
        on_entry_unfilled=lambda sid, sym: released.append((sid, sym)),
    )

    assert released == [("s1", "BTCUSDT")]
    assert _fallback_outcomes(wal) == ["total_miss"]


async def test_partial_then_market_reject_keeps_live_entered(wal, ks, metrics):
    """부분 체결 보유 시 시장가 잔량 REJECTED 여도 release 안 함 (포지션 존재)."""
    broker = FakeBroker()
    broker.orig_qty = Decimal("0.005")
    broker.get_queue = [
        ("PARTIALLY_FILLED", Decimal("0.002")),
        ("CANCELED", Decimal("0.002")),
    ]
    ks.trip(reason="test", source="manual")
    released: list = []
    await _run_fallback(
        broker, ks, wal, metrics,
        on_entry_unfilled=lambda sid, sym: released.append(1),
    )

    assert released == []  # already_filled > 0 → 진입 기록 유지
    assert _fallback_outcomes(wal) == ["resubmitted_market"]


async def test_resubmit_zero_remaining_is_noop(wal, ks, metrics):
    """잔량 0 (전량 maker 체결) → 시장가 재발주 no-op."""
    broker = FakeBroker()
    acks = await resubmit_post_only_as_market(
        _intent(), qty=Decimal("0"), already_filled=Decimal("0.001"),
        broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
        market_state=None,
    )
    assert acks == []
    assert _market_places(broker) == []
    assert _fallback_outcomes(wal) == ["filled_maker"]


# ---------------------------------------------------------------------------
# execute_intents 통합 — EXPIRED 즉시 / NEW 백그라운드 예약
# ---------------------------------------------------------------------------

async def test_executor_expired_post_only_immediate_market(wal, ks, metrics):
    """post-only 발주 → EXPIRED → 같은 tick 안에서 즉시 시장가 재발주."""
    broker = FakeBroker()
    broker.limit_status = "EXPIRED"
    broker.market_status = "FILLED"
    acks = await execute_intents(
        [_intent()], broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
    )

    assert acks[0].status == "EXPIRED"
    assert len(broker.place_calls) == 2
    assert broker.place_calls[0].order_type == OrderType.LIMIT
    assert broker.place_calls[0].tif == TimeInForce.GTX
    assert broker.place_calls[1].order_type == OrderType.MARKET


async def test_executor_new_post_only_schedules_background_fallback(
    monkeypatch, wal, ks, metrics,
):
    """post-only 발주 → NEW → tick loop 블록 없이 백그라운드 fallback 예약."""
    monkeypatch.setattr(pof, "POST_ONLY_FALLBACK_SEC", 0.0)
    broker = FakeBroker()
    broker.limit_status = "NEW"
    broker.market_status = "FILLED"
    broker.get_queue = [("NEW", Decimal("0")), ("CANCELED", Decimal("0"))]

    acks = await execute_intents(
        [_intent()], broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
    )
    # execute_intents 자체는 NEW 즉시 반환 — 블로킹 없음.
    assert acks[0].status == "NEW"
    assert _market_places(broker) == []  # 아직 fallback task 미실행

    # 예약된 백그라운드 task 실행 완료까지 대기.
    tasks = list(pof._BACKGROUND_TASKS)
    assert len(tasks) == 1
    await asyncio.gather(*tasks)

    # fallback 완료 후: cancel + 시장가 재발주.
    assert len(broker.cancel_calls) == 1
    assert len(_market_places(broker)) == 1


async def test_executor_market_intent_unaffected(wal, ks, metrics):
    """entry_order_type 기본값(market) intent → post-only 경로 안 탐 (legacy)."""
    broker = FakeBroker()
    broker.market_status = "FILLED"
    intent = OrderIntent(strategy_id="s", symbol="BTCUSDT", side="buy",
                         qty=0.001, reason="r")  # entry_order_type 기본 "market"
    acks = await execute_intents(
        [intent], broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
    )
    assert acks[0].status == "FILLED"
    assert len(broker.place_calls) == 1
    assert broker.place_calls[0].order_type == OrderType.MARKET
    assert _fallback_outcomes(wal) == []


async def test_cancel_pending_fallbacks_cancels_inflight(monkeypatch, wal, ks, metrics):
    """cancel_pending_fallbacks 가 대기 중인 fallback task 를 취소한다."""
    monkeypatch.setattr(pof, "POST_ONLY_FALLBACK_SEC", 100.0)  # 길게 — 대기 상태 유지
    broker = FakeBroker()
    broker.limit_status = "NEW"
    await execute_intents(
        [_intent()], broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
    )
    assert len(pof._BACKGROUND_TASKS) == 1

    await cancel_pending_fallbacks()
    assert len(pof._BACKGROUND_TASKS) == 0
    assert _market_places(broker) == []  # 취소돼서 시장가 발사 안 됨
