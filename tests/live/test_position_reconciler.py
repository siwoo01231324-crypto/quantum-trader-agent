"""PositionReconciler 회귀 (2026-05-21).

NEARUSDT 19:47:47 사례 fix: 사용자가 broker UI 에서 수동 close → store 모름
→ store 에 phantom long → stop fire → broker SHORT 진입.

본 reconciler 가 주기적으로 broker.get_net_positions() 와 store 를 비교해서
mismatch 발견 시 WAL/timeline alert + single-holder 케이스 auto-fix.
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from live.position_reconciler import PositionReconciler, EVENT_TYPE
from live.strategy_position_store import StrategyPositionStore


class _FakeBroker:
    def __init__(self, net: dict[str, Decimal] | None = None) -> None:
        self.net = net or {}
        self.calls = 0
        self.raise_next = False

    async def get_net_positions(self) -> dict[str, Decimal]:
        self.calls += 1
        if self.raise_next:
            self.raise_next = False
            raise RuntimeError("simulated broker error")
        return dict(self.net)


def _make_reconciler(broker, store):
    wal_events: list = []
    timeline: list = []
    rec = PositionReconciler(
        position_store=store,
        broker=broker,
        wal_observer=lambda ev: wal_events.append(ev),
        alert_publisher=lambda p: timeline.append(p),
        tol=Decimal("0.001"),
    )
    return rec, wal_events, timeline


@pytest.mark.asyncio
async def test_no_mismatch_no_alert_no_fix():
    """store 와 broker 가 일치 → alert/fix 둘 다 없음."""
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({"NEARUSDT": Decimal("135")})
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert outcome.mismatches == ()
    assert outcome.auto_fixed == ()
    assert outcome.alerted_only == ()
    assert wal_events == []
    assert timeline == []


@pytest.mark.asyncio
async def test_manual_close_on_binance_ui_triggers_auto_fix():
    """★ 실제 사고 시나리오: store=+135 long, broker=0 (사용자 수동 close).
    Single holder → auto-fix 로 store 를 0 으로 강제 동기화.
    """
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({})  # broker 에 NEAR 0
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert len(outcome.mismatches) == 1
    m = outcome.mismatches[0]
    assert m.symbol == "NEARUSDT"
    assert m.logical_net == Decimal("135")
    assert m.broker_net == Decimal("0")
    assert m.delta == Decimal("135")

    # Auto-fix: store NEAR 가 broker (0) 에 맞춰짐 → bucket 에서 제거.
    assert outcome.auto_fixed == (("scan", "NEARUSDT", Decimal("135"), Decimal("0")),)
    assert store.get_positions("scan") == []  # NEAR removed (qty=0)

    # WAL + timeline 둘 다 alert.
    assert len(wal_events) == 1
    assert wal_events[0].event_type == EVENT_TYPE
    assert wal_events[0].payload["action"] == "auto_fix"
    assert len(timeline) == 1
    assert timeline[0]["event_type"] == EVENT_TYPE


@pytest.mark.asyncio
async def test_phantom_broker_position_alerts_only():
    """broker 에는 있는데 store 엔 holder 없음 → phantom → 알림만, fix X."""
    store = StrategyPositionStore()  # 비어있음
    broker = _FakeBroker({"NEARUSDT": Decimal("135")})
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert len(outcome.mismatches) == 1
    assert outcome.auto_fixed == ()
    assert len(outcome.alerted_only) == 1
    assert wal_events[0].payload["action"] == "alert_only_phantom_broker"
    # Store 는 변경 X — 어느 strategy 에 attribute 할지 불명확.
    assert store.all_positions() == {}


@pytest.mark.asyncio
async def test_multi_holder_real_position_alerts_only_no_fix():
    """multi-holder + broker 에 *실포지션 존재* → attribution 불확실 → 알림만.
    (broker=0 phantom 케이스는 P2.5 에서 auto-fix — 아래 별도 테스트.)"""
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="a", symbol="NEARUSDT", qty=Decimal("100"))
    store.force_sync_position(strategy_id="b", symbol="NEARUSDT", qty=Decimal("50"))
    broker = _FakeBroker({"NEARUSDT": Decimal("200")})  # 실포지션 — 나눌 수 없음
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert len(outcome.mismatches) == 1
    assert outcome.auto_fixed == ()
    assert len(outcome.alerted_only) == 1
    assert wal_events[0].payload["action"] == "alert_only_multi_holder"
    # Store 는 변경 X — 사용자가 dashboard 보고 직접 결정해야.
    assert store.all_positions() == {
        "a": [("NEARUSDT", 100.0)], "b": [("NEARUSDT", 50.0)],
    }


@pytest.mark.asyncio
async def test_multi_holder_phantom_broker_zero_auto_fixes_all():
    """★ P2.5 (2026-06-11) — multi-holder 인데 broker=0 = 전원 phantom.
    나눌 실포지션이 없으므로 전원 0 정합 (유령이 재진입 차단·22002 폭주 유발했음).
    """
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="a", symbol="SNDKUSDT", qty=Decimal("-0.226"))
    store.force_sync_position(strategy_id="b", symbol="SNDKUSDT", qty=Decimal("-0.226"))
    broker = _FakeBroker({"SNDKUSDT": Decimal("0")})  # 거래소엔 없음 = 전원 유령
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert len(outcome.auto_fixed) == 2          # a, b 둘 다 정리
    assert outcome.alerted_only == ()
    assert store.all_positions() == {}           # 전원 0 → 보유 없음 (재진입 허용)


@pytest.mark.asyncio
async def test_broker_short_logical_long_auto_fix_to_short():
    """store +135 LONG, broker -135 SHORT — 가장 위험한 케이스 (사고 시나리오).
    Auto-fix 가 store 를 -135 (broker truth) 로 동기화 → 다음 evaluate 가 short
    포지션으로 인식해 long-stop 발사 안 함.
    """
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({"NEARUSDT": Decimal("-135")})
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert outcome.auto_fixed == (("scan", "NEARUSDT", Decimal("135"), Decimal("-135")),)
    # Store 가 short 으로 동기화됨.
    assert store.get_positions("scan") == [("NEARUSDT", -135.0)]


@pytest.mark.asyncio
async def test_broker_fetch_failure_returns_empty_outcome():
    """Broker 조회 실패 시 절대 raise 안 함 → 다음 cycle 에 재시도."""
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({"NEARUSDT": Decimal("0")})
    broker.raise_next = True
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()  # 1st call raises → empty outcome
    assert outcome.mismatches == ()
    assert outcome.auto_fixed == ()
    assert wal_events == []

    # 다음 호출은 정상 → mismatch 감지 + fix.
    outcome2 = await rec.reconcile_once()
    assert len(outcome2.mismatches) == 1
    assert outcome2.auto_fixed != ()


@pytest.mark.asyncio
async def test_tolerance_small_diff_ignored():
    """tol 이하의 부동소수점 잔차는 무시 (broker 가 137.0001 같은 dust 줄 때)."""
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({"NEARUSDT": Decimal("135.0005")})  # < tol=0.001
    rec, wal_events, timeline = _make_reconciler(broker, store)

    outcome = await rec.reconcile_once()
    assert outcome.mismatches == ()


@pytest.mark.asyncio
async def test_run_loop_stops_on_event():
    """stop_event set 되면 loop 종료."""
    store = StrategyPositionStore()
    broker = _FakeBroker({})
    rec = PositionReconciler(
        position_store=store, broker=broker,
        tol=Decimal("0.001"), interval_sec=0.05,
    )
    stop_event = asyncio.Event()

    async def stop_soon():
        await asyncio.sleep(0.15)
        stop_event.set()

    await asyncio.gather(rec.run_loop(stop_event), stop_soon())
    # broker.calls 가 최소 1번 이상이어야 함 (loop 가 1 cycle 이상 돔).
    assert broker.calls >= 1


@pytest.mark.asyncio
async def test_auto_fix_invokes_on_position_synced_callback():
    """★ 2026-05-22 회귀: auto-fix 가 store qty 를 바꾸면 on_position_synced
    콜백이 (sid, symbol, broker_net) 으로 호출된다.

    이 콜백이 orchestrator._live_entered 정합의 진입점 — 미호출 시 reconciler
    가 청산한 종목이 _live_entered 에 영구히 남아 진입 차단된다 (재시작 후
    11시간 매수 0 의 원인).
    """
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({})  # broker flat (사용자가 UI 로 수동 close)
    synced: list = []
    rec = PositionReconciler(
        position_store=store, broker=broker,
        on_position_synced=lambda sid, sym, qty: synced.append((sid, sym, qty)),
        tol=Decimal("0.001"),
    )
    outcome = await rec.reconcile_once()
    assert outcome.auto_fixed != ()
    assert synced == [("scan", "NEARUSDT", Decimal("0"))]


@pytest.mark.asyncio
async def test_live_entered_reconcile_called_every_cycle_no_mismatch():
    """★ 2026-06-16 SKYAI 회귀: store↔broker 가 일치(둘 다 보유 or 둘 다 flat)해
    mismatch 가 없어도 on_live_entered_reconcile 가 broker 보유집합으로 호출된다.

    네이티브 청산은 store·broker 가 함께 flat 이 돼 mismatch 가 없으므로, auto-fix
    경로(on_position_synced)로는 _live_entered 해제가 안 됐다 → 종목 영구 재진입차단.
    본 콜백은 mismatch 유무와 무관하게 매 cycle broker 보유집합을 넘겨야 한다.
    """
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="BTCUSDT", qty=Decimal("1"))
    broker = _FakeBroker({"BTCUSDT": Decimal("1")})  # 완전 일치 → mismatch 없음
    held_seen: list = []
    rec = PositionReconciler(
        position_store=store, broker=broker,
        on_live_entered_reconcile=lambda held: held_seen.append(held),
        tol=Decimal("0.001"),
    )
    outcome = await rec.reconcile_once()
    assert outcome.mismatches == ()          # 일치 → mismatch 없음
    assert held_seen == [{"BTCUSDT"}]         # 그래도 broker 보유집합으로 호출됨


@pytest.mark.asyncio
async def test_live_entered_reconcile_empty_when_broker_flat():
    """broker 가 전부 flat 이면 빈 집합 전달 → orchestrator 가 전 키 해제(재진입 허용)."""
    store = StrategyPositionStore()
    broker = _FakeBroker({})  # broker 아무것도 안 들고 있음
    held_seen: list = []
    rec = PositionReconciler(
        position_store=store, broker=broker,
        on_live_entered_reconcile=lambda held: held_seen.append(held),
        tol=Decimal("0.001"),
    )
    await rec.reconcile_once()
    assert held_seen == [set()]


@pytest.mark.asyncio
async def test_live_entered_reconcile_skipped_on_broker_fetch_failure():
    """broker fetch 실패 cycle 엔 콜백 미호출 (빈집합으로 전체 오해제 방지)."""
    store = StrategyPositionStore()
    broker = _FakeBroker({"BTCUSDT": Decimal("1")})
    broker.raise_next = True
    held_seen: list = []
    rec = PositionReconciler(
        position_store=store, broker=broker,
        on_live_entered_reconcile=lambda held: held_seen.append(held),
        tol=Decimal("0.001"),
    )
    await rec.reconcile_once()  # fetch raises → early return
    assert held_seen == []


@pytest.mark.asyncio
async def test_on_position_synced_skipped_for_phantom_and_multi_holder():
    """auto-fix 안 하는 케이스 (phantom holder 0 / multi-holder ≥2) 는 콜백도
    호출 안 한다 — store 를 안 고쳤으니 _live_entered 도 건드리면 안 된다."""
    # phantom: broker 에 있고 store holder 0
    store = StrategyPositionStore()
    broker = _FakeBroker({"NEARUSDT": Decimal("135")})
    synced: list = []
    rec = PositionReconciler(
        position_store=store, broker=broker,
        on_position_synced=lambda *a: synced.append(a), tol=Decimal("0.001"),
    )
    await rec.reconcile_once()
    assert synced == []

    # multi-holder + broker 실포지션: attribution 불확실 → ALERT-ONLY → 콜백 skip.
    # (broker=0 phantom 은 P2.5 에서 auto-fix 되어 콜백 호출됨 — 별도 테스트.)
    store2 = StrategyPositionStore()
    store2.force_sync_position(strategy_id="a", symbol="NEARUSDT", qty=Decimal("100"))
    store2.force_sync_position(strategy_id="b", symbol="NEARUSDT", qty=Decimal("50"))
    broker2 = _FakeBroker({"NEARUSDT": Decimal("200")})
    synced2: list = []
    rec2 = PositionReconciler(
        position_store=store2, broker=broker2,
        on_position_synced=lambda *a: synced2.append(a), tol=Decimal("0.001"),
    )
    await rec2.reconcile_once()
    assert synced2 == []


@pytest.mark.asyncio
async def test_on_position_synced_exception_does_not_break_reconcile():
    """콜백이 raise 해도 reconcile 은 안 죽고 auto-fix 자체는 완료된다."""
    store = StrategyPositionStore()
    store.force_sync_position(strategy_id="scan", symbol="NEARUSDT", qty=Decimal("135"))
    broker = _FakeBroker({})

    def _boom(sid, sym, qty):
        raise RuntimeError("callback boom")

    rec = PositionReconciler(
        position_store=store, broker=broker,
        on_position_synced=_boom, tol=Decimal("0.001"),
    )
    outcome = await rec.reconcile_once()  # 안 raise
    assert outcome.auto_fixed != ()
    assert store.get_positions("scan") == []  # auto-fix 자체는 완료
