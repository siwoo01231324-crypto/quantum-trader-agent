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
import os
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

# 종목 단위 force-sync 제외 — reconciler 가 절대 0 정합/유령정리 하면 안 되는 심볼.
# 2026-06-22: ORDIUSDT 는 사용자가 수동 보유·관리하는 포지션(봇이 주문 안 함,
# store holders=0). 만에 하나 store holder 가 생겨도 reconciler 가 건드리면 사용자
# 물량이 차감/뒤집힘 → 어떤 branch 도 force-sync 금지(알림만).
#
# 정본(canonical): src/portfolio/bitget_top_dynamic.py::_EXCLUDE_SYMBOLS (universe
# 진입 차단용). 여기서는 그 패키지의 무거운 __init__ import 체인(orchestrator/risk)
# 을 끌어오지 않도록 동일한 env 규약(`BITGET_UNIVERSE_EXCLUDE`)을 mirror 한다 —
# reconciler 는 live_run 초기에 import 되므로 import surface 를 최소로 유지.
_EXCLUDE_SYMBOLS: frozenset[str] = frozenset(
    {"ORDIUSDT"}
    | {
        s.strip().upper()
        for s in os.environ.get("BITGET_UNIVERSE_EXCLUDE", "").split(",")
        if s.strip()
    }
)


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
      - holder ≥ 2명 & broker=0 → 전원 phantom → 전원 0 정합 (P2.5)
      - holder ≥ 2명 & broker≠0 → **Bitget one-way 불변식**: broker net 방향과
        반대인 holder 는 증명상 유령 → 0 정합. 반대유령 제거 후 같은방향 holder
        가 정확히 1명이면 broker_net 에 정합. 그래도 같은방향 ≥2명이면 귀속
        추정 불가 → 알림만 (CL 2026-06-19 -13.6% 무방비 사고 fix).
      - **제외 종목**(`bitget_top_dynamic._EXCLUDE_SYMBOLS`, 예: ORDIUSDT 사용자
        수동분)은 어떤 branch 도 force-sync 안 함 — 알림만 (방어심층).

    참고 — STORE-ONLY: force_sync_position 은 in-memory store 만 바꾸고 거래소
    주문을 절대 내지 않는다. 그래서 반대유령 0 정합이 안전하다 (네팅 위험 없음).
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
            # ── 제외종목 가드(최상단, 모든 branch 우선) ──────────────────────
            # ORDIUSDT 등 사용자 수동분 / `BITGET_UNIVERSE_EXCLUDE` 는 single-holder
            # ·all-phantom·multi-holder 어떤 경로로도 force-sync 금지(알림만). 봇이
            # ORDI 를 거래 안 해 holders=0 이지만, 만에 하나 store holder 가 생겨도
            # 절대 안 건드린다 — 사용자 수동물량 차감/뒤집힘 방지(방어심층).
            if m.symbol in _EXCLUDE_SYMBOLS:
                alerted_only.append(m)
                logger.warning(
                    "PositionReconciler: ALERT-ONLY(EXCLUDED) %s holders=%d "
                    "store=%s broker=%s delta=%s (제외종목 — force-sync 금지)",
                    m.symbol, len(holders), m.logical_net, m.broker_net, m.delta,
                )
                continue
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
                # broker 에 실포지션 있고 multi-holder (>=2) — 과거엔 무조건 알림만
                # 이었으나, Bitget one-way(posMode=one_way_mode)에서는 종목당 net
                # 방향이 정확히 1개라 **broker 방향과 반대인 holder 는 증명상 유령**
                # (CL 2026-06-19: bb-reversal long +0.56 진짜 + short-whitelist
                # short -1.52 유령 vs broker net +1.66 → 유령이 양 보호계층(네이티브
                # SL 재배치 43023 / STOP-FIRE close 22002) 오염 → -13.6% 무방비).
                #
                # (제외종목 가드는 루프 최상단에서 이미 처리 — 여기 도달하면 비제외.)
                broker_sign = 1 if m.broker_net > 0 else -1
                opposite = {
                    sid: q for sid, q in holders.items()
                    if (1 if q > 0 else -1) != broker_sign
                }
                same_dir = {
                    sid: q for sid, q in holders.items()
                    if (1 if q > 0 else -1) == broker_sign
                }

                # 1) 반대방향 holder 전원 0 정합 (one-way 모드라 증명상 유령).
                for sid, before in opposite.items():
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
                        "PositionReconciler: AUTO-FIX-PHANTOM-OPPOSITE %s sid=%s "
                        "qty=%s → 0 (broker 방향과 반대 = 유령)",
                        m.symbol, sid, before,
                    )

                # 2) 반대유령 제거 후 같은방향 holder 가 정확히 1명이면 broker 에 정합.
                if len(same_dir) == 1:
                    sid, before = next(iter(same_dir.items()))
                    self._store.force_sync_position(
                        strategy_id=sid, symbol=m.symbol, qty=m.broker_net,
                    )
                    auto_fixed.append((sid, m.symbol, before, m.broker_net))
                    if self._on_position_synced is not None:
                        try:
                            self._on_position_synced(sid, m.symbol, m.broker_net)
                        except Exception as err:  # noqa: BLE001 — defensive
                            logger.warning(
                                "PositionReconciler: on_position_synced failed "
                                "sid=%s sym=%s: %s", sid, m.symbol, err,
                            )
                    logger.warning(
                        "PositionReconciler: AUTO-FIX-MULTI→SINGLE %s sid=%s → "
                        "broker=%s (반대유령 제거 후 단일 holder)",
                        m.symbol, sid, m.broker_net,
                    )
                else:
                    # 같은방향 holder 가 2명 이상(진짜 모호) 또는 0명(holders 전원
                    # 반대유령이었던 broker-only 잔여) → 자동 추정 위험 → 알림만.
                    alerted_only.append(m)
                    logger.warning(
                        "PositionReconciler: ALERT-ONLY %s holders=%d store=%s broker=%s delta=%s",
                        m.symbol, len(holders), m.logical_net, m.broker_net, m.delta,
                    )
        # TODO(Part B): unprotected-position alert. CL 2026-06-19 -13.6% 는
        # 유령(Part A 가 해소)이 보호계층을 죽인 것이 1차 원인이지만, 아무도
        # 모니터링 안 한 것이 2차 원인. 봇 보유 포지션 중 거래소 native SL/TP
        # plan order 가 없는(=무방비) 종목을 텔레그램으로 능동 알림하는 패스가
        # 필요하다. **ALERT-ONLY — 자동청산/SL가격계산 금지(너무 위험)**.
        # 미구현 사유: 본 reconciler 의 broker 의존 Protocol 은 의도적으로 최소
        # (`get_net_positions()` 단 하나)라, plan-order 조회(거래소별 get_open_
        # plan_orders) + 텔레그램 notify 채널 주입을 끼우면 침습적. 보호상태
        # 점검은 이미 native TP/SL 을 다루는 `protective_coordinator.py` 가 더
        # 적합한 위치 — 거기서 알림 훅을 거는 후속 PR 로 분리.
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
