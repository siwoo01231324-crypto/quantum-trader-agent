"""Broker ↔ position_store reconciliation 백그라운드 task (2026-05-21).

문제 (NEARUSDT 19:47:47 사례):
  1. Strategy 가 +135 LONG 진입 → broker +135, store +135 (동기화 OK)
  2. 사용자가 Binance UI 에서 수동 close → broker 0, **store 모름** (+135 그대로)
  3. risk_mgr.evaluate → store +135 보고 stop_loss fire → SELL 135
  4. broker (=0) → SELL 135 받아 -135 SHORT 진입 (의도와 정반대)
  5. cooldown 후 strategy 가 다시 BUY → broker 0 으로 복귀
  6. store 는 또 misalign → 사이클 반복 → 출혈 누적

Fix: 주기적으로 (default 60s) broker 의 net position 을 fetch 해 store 와
비교. mismatch 발견 시:
  - WAL 에 ``position_reconciled`` 이벤트 기록 (감사 추적)
  - timeline_broker 로 dashboard 토스트 push (사용자 즉시 알림)
  - **auto-fix**: single-holder 케이스에 한해 store 를 broker 에 맞춰 강제
    sync. multi-holder 또는 phantom broker 포지션은 알림만 — 자동 추정이
    위험.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Protocol

from src.live.position_reconciliation import (
    ReconcileMismatch, reconcile_positions, sum_logical_by_symbol,
)
from src.live.strategy_position_store import StrategyPositionStore
from src.live.types import WALEvent

logger = logging.getLogger(__name__)

EVENT_TYPE = "position_reconciled"


class _BrokerWithNetPositions(Protocol):
    """Broker 가 가져야 할 인터페이스 — `get_net_positions()` 만."""

    async def get_net_positions(self) -> dict[str, Decimal]: ...


@dataclass(frozen=True, slots=True)
class ReconciliationOutcome:
    """단일 reconcile cycle 결과 — 진단/테스트 용."""

    mismatches: tuple[ReconcileMismatch, ...]
    auto_fixed: tuple[tuple[str, str, Decimal, Decimal], ...]  # (sid, symbol, before, after)
    alerted_only: tuple[ReconcileMismatch, ...]


class PositionReconciler:
    """주기적 broker ↔ store reconciliation.

    Wiring:
        reconciler = PositionReconciler(
            position_store=position_store,
            broker=binance_adapter,        # get_net_positions() 메서드 필요
            wal_observer=_wal_observer,    # None 가능
            alert_publisher=lambda payload: timeline_broker.publish(payload),  # None 가능
            tol=Decimal("0.001"),
            interval_sec=60.0,
        )
        task = asyncio.create_task(reconciler.run_loop(stop_event))

    Auto-fix 룰:
      - mismatch symbol 의 logical holder 1명 → 그 holder 의 qty 를 broker_net
        에 맞춰 force_sync (사용자가 broker UI 로 close 한 케이스의 표준 경로)
      - holder 0명 (broker 만 있고 store 비어있음) → phantom 으로 알림만
      - holder ≥ 2명 → multi-strategy attribution 불확실 → 알림만
    """

    def __init__(
        self,
        *,
        position_store: StrategyPositionStore,
        broker: _BrokerWithNetPositions,
        wal_observer: Callable[[WALEvent], None] | None = None,
        alert_publisher: Callable[[dict[str, Any]], None] | None = None,
        on_position_synced: Callable[[str, str, Decimal], None] | None = None,
        on_live_entered_reconcile: Callable[[set], None] | None = None,
        tol: Decimal = Decimal("0.001"),
        interval_sec: float = 60.0,
    ) -> None:
        self._store = position_store
        self._broker = broker
        self._wal_observer = wal_observer
        self._alert_publisher = alert_publisher
        # 2026-05-22: auto-fix 가 store qty 를 바꾼 직후 호출되는 콜백.
        # orchestrator._live_entered 를 store 와 정합시키는 데 쓴다 — store 만
        # 고치고 _live_entered 를 방치하면 청산된 종목이 영구 진입 차단된다.
        self._on_position_synced = on_position_synced
        # 2026-06-17: 매 cycle broker 보유집합으로 orchestrator._live_entered 정합.
        # auto-fix(불일치)에만 의존하던 on_position_synced 의 사각 보완 — 네이티브
        # 청산은 store↔broker 둘 다 flat 이라 불일치가 없어 _live_entered 가 leak
        # → 종목 영구 재진입차단(2026-06-16 SKYAI). 불일치 유무와 무관하게 호출.
        self._on_live_entered_reconcile = on_live_entered_reconcile
        self._tol = tol
        self._interval_sec = interval_sec

    async def reconcile_once(self) -> ReconciliationOutcome:
        """단일 cycle — broker fetch → 비교 → alert + auto-fix.

        Broker fetch 실패 시 빈 outcome 반환 (다음 cycle 에 재시도). 어떤 단계
        든 절대 raise 하지 않음 — background task 가 죽으면 사용자 보호 못함.
        """
        try:
            broker_net = await self._broker.get_net_positions()
        except Exception as err:  # noqa: BLE001 — defensive
            logger.warning("PositionReconciler: broker fetch failed: %s", err)
            return ReconciliationOutcome((), (), ())

        # 2026-06-17 — 매 cycle _live_entered 정합 (mismatch 유무와 무관, broker fetch
        # 성공 후에만). 네이티브 청산으로 닫힌 종목은 store↔broker 일치라 아래 mismatch
        # 가 없어 auto-fix sync 가 안 돌고 _live_entered 가 leak → 영구 재진입차단
        # (2026-06-16 SKYAI). broker 보유집합으로 정합해 broker 에 없는 키를 해제한다.
        if self._on_live_entered_reconcile is not None:
            try:
                held = {str(s).upper() for s, n in broker_net.items() if n != 0}
                self._on_live_entered_reconcile(held)
            except Exception as err:  # noqa: BLE001 — 정합 실패가 loop 죽이면 안 됨
                logger.warning(
                    "PositionReconciler: live_entered reconcile failed: %s", err,
                )

        logical = {sid: dict(bucket) for sid, bucket in self._store._positions.items()}
        mismatches = reconcile_positions(logical, broker_net, tol=self._tol)
        if not mismatches:
            return ReconciliationOutcome((), (), ())

        auto_fixed: list[tuple[str, str, Decimal, Decimal]] = []
        alerted_only: list[ReconcileMismatch] = []
        for m in mismatches:
            holders = self._holders_of(m.symbol)
            self._emit_alert(m, holders=holders)
            if len(holders) == 1:
                sid, before = next(iter(holders.items()))
                self._store.force_sync_position(
                    strategy_id=sid, symbol=m.symbol, qty=m.broker_net,
                )
                auto_fixed.append((sid, m.symbol, before, m.broker_net))
                logger.warning(
                    "PositionReconciler: AUTO-FIX %s %s store=%s → broker=%s (delta=%s)",
                    sid, m.symbol, before, m.broker_net, m.delta,
                )
                # store 를 고쳤으면 orchestrator._live_entered 도 같이 정합한다.
                # 미연결(None) 이면 no-op. 콜백 예외는 흡수 — reconcile loop 가
                # 죽으면 사용자 보호 불가.
                if self._on_position_synced is not None:
                    try:
                        self._on_position_synced(sid, m.symbol, m.broker_net)
                    except Exception as err:  # noqa: BLE001 — defensive
                        logger.warning(
                            "PositionReconciler: on_position_synced failed "
                            "sid=%s sym=%s: %s", sid, m.symbol, err,
                        )
            elif m.broker_net == 0:
                # P2.5 (2026-06-11) — holders>=2 이지만 broker=0 = 전원 phantom.
                # 나눌 실포지션이 없으므로 multi-holder attribution 불확실 우려가
                # 없음 → 전원 0 정합. 안 지우면 유령이 (a) 재진입 차단(SNDK 발화
                # 인데 미진입) (b) risk manager 가 30초마다 닫으려다 22002 폭주.
                for sid, before in holders.items():
                    self._store.force_sync_position(
                        strategy_id=sid, symbol=m.symbol, qty=Decimal("0"),
                    )
                    auto_fixed.append((sid, m.symbol, before, Decimal("0")))
                    if self._on_position_synced is not None:
                        try:
                            self._on_position_synced(sid, m.symbol, Decimal("0"))
                        except Exception as err:  # noqa: BLE001 — defensive
                            logger.warning(
                                "PositionReconciler: on_position_synced failed "
                                "sid=%s sym=%s: %s", sid, m.symbol, err,
                            )
                logger.warning(
                    "PositionReconciler: AUTO-FIX-PHANTOM %s holders=%d → 0 "
                    "(broker 없음 — 전원 유령 정리)",
                    m.symbol, len(holders),
                )
            else:
                # broker 에 실포지션 있고 multi-holder → 자동 추정 위험 → 알림만.
                alerted_only.append(m)
                logger.warning(
                    "PositionReconciler: ALERT-ONLY %s holders=%d store=%s broker=%s delta=%s",
                    m.symbol, len(holders), m.logical_net, m.broker_net, m.delta,
                )
        return ReconciliationOutcome(
            mismatches=tuple(mismatches),
            auto_fixed=tuple(auto_fixed),
            alerted_only=tuple(alerted_only),
        )

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        """주기적 실행 — stop_event 가 set 될 때까지 interval_sec 마다 reconcile.

        예외는 cycle 내부에서 흡수, loop 자체는 절대 죽지 않음.
        """
        logger.info(
            "PositionReconciler: started (interval=%.1fs, tol=%s)",
            self._interval_sec, self._tol,
        )
        while not stop_event.is_set():
            await self.reconcile_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval_sec)
            except asyncio.TimeoutError:
                pass  # next cycle
        logger.info("PositionReconciler: stopped")

    def _holders_of(self, symbol: str) -> dict[str, Decimal]:
        """해당 symbol 을 0 이 아닌 qty 로 들고 있는 (sid → qty) — auto-fix 판정용."""
        out: dict[str, Decimal] = {}
        for sid, bucket in self._store._positions.items():
            qty = bucket.get(symbol, Decimal("0"))
            if qty != 0:
                out[sid] = qty
        return out

    def _emit_alert(
        self, m: ReconcileMismatch, *, holders: dict[str, Decimal],
    ) -> None:
        """WAL + timeline_broker 양쪽으로 alert 출력 (둘 다 옵션, defensive)."""
        payload = {
            "symbol": m.symbol,
            "logical_net": str(m.logical_net),
            "broker_net": str(m.broker_net),
            "delta": str(m.delta),
            "holders": {sid: str(q) for sid, q in holders.items()},
            "action": (
                "auto_fix" if len(holders) == 1
                else "alert_only_multi_holder" if len(holders) >= 2
                else "alert_only_phantom_broker"
            ),
        }
        if self._wal_observer is not None:
            event = WALEvent(
                ts=datetime.now(timezone.utc).isoformat(),
                event_type=EVENT_TYPE,
                payload=payload,
            )
            try:
                self._wal_observer(event)
            except Exception as err:  # noqa: BLE001
                logger.warning("PositionReconciler: WAL emit failed: %s", err)
        if self._alert_publisher is not None:
            try:
                self._alert_publisher({
                    "event_type": EVENT_TYPE,
                    "payload": payload,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as err:  # noqa: BLE001
                logger.warning("PositionReconciler: alert publish failed: %s", err)
