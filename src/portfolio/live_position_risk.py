"""Per-position stop_loss / take_profit / trailing_stop manager (#227 S2).

Live-scanner strategies do not emit ``sell`` signals — they declare exit
thresholds (``stop_loss_pct`` / ``take_profit_pct`` / ``trailing_stop_pct``)
as ``LiveScannerMixin`` class attributes. ``LivePositionRiskManager`` reads
the entry price from ``PnLAggregator`` (``_cost_basis``) and the held qty
from ``StrategyPositionStore``, then on every tick checks each
``(strategy_id, symbol)`` pair and emits a market-sell ``OrderIntent`` when
any threshold is breached.

The live loop (#227 S3) wires this in alongside ``execute_intents``: after
each strategy dispatch it calls ``risk_mgr.evaluate(tick.symbol, last_price,
ts)`` and routes the returned intents to the broker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable, ClassVar

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.live.types import WALEvent
from src.portfolio.order_intent import OrderIntent

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StopTpPolicy:
    """Per-strategy exit thresholds. Mirrors LiveScannerMixin class attrs."""
    stop_loss_pct: float
    take_profit_pct: float
    trailing_stop_pct: float | None = None

    def __post_init__(self) -> None:
        if not 0 < self.stop_loss_pct < 1:
            raise ValueError(f"stop_loss_pct must be in (0, 1), got {self.stop_loss_pct}")
        if not 0 < self.take_profit_pct < 1:
            raise ValueError(f"take_profit_pct must be in (0, 1), got {self.take_profit_pct}")
        if self.trailing_stop_pct is not None and not 0 < self.trailing_stop_pct < 1:
            raise ValueError(
                f"trailing_stop_pct must be None or in (0, 1), got {self.trailing_stop_pct}"
            )


class LivePositionRiskManager:
    """Stateful per-(strategy_id, symbol) stop/TP/trailing-stop monitor.

    Stateless across restarts is achieved by replaying the WAL — the manager's
    only mutable state is the trailing-stop high-water mark, which is
    re-derived from the entry price on first ``evaluate`` after restart (the
    actual peak between entry and restart is lost — acceptable for an MVP).

    All numeric arithmetic uses ``Decimal`` to match ``PnLAggregator``.
    """

    EVENT_TYPE = "position_stop_triggered"

    # 2026-05-24 — in-flight exit guard 의 self-heal timeout. SELL intent 가
    # broker 로 발사된 후 이 시간 안에 fill 이 도착해 store 가 0 (또는 dust)
    # 으로 갱신되지 않으면 _pending_exit 를 자동 해제 → 다음 tick 에 재평가.
    # broker rejection / partial fill 누락 등으로 영구 stuck 되던 버그 fix.
    # 실측: 2026-05-23 16:00:17 BTCUSDT trailing_stop SELL 0.024 발사 후
    # order_filled 가 WAL 에 없어 (broker fill 실패) _pending_exit 에 갇혀
    # 17h+ 동안 추가 평가 0건 → ROI +18% 도달했어도 TP fire 못 함.
    PENDING_EXIT_TIMEOUT_SEC: ClassVar[float] = 30.0

    def __init__(
        self,
        *,
        position_store: StrategyPositionStore,
        pnl_aggregator: PnLAggregator,
        wal_observer: Callable[[WALEvent], None] | None = None,
        on_exit: Callable[[str, str], None] | None = None,
        pending_exit_timeout_sec: float | None = None,
        native_tpsl_check: Callable[[str], bool] | None = None,
        max_hold_sec: float | None = None,
    ) -> None:
        self._position_store = position_store
        self._pnl = pnl_aggregator
        self._wal_observer = wal_observer
        # 2026-06-10 데이터안정화 P2 — symbol 에 거래소 네이티브 preset TP/SL 이
        # 활성이면 True 반환하는 콜백(broker adapter.has_native_tpsl). True 인
        # 종목은 evaluate 에서 synthetic SL/TP 를 *발동 안 함* — 거래소가 라인에서
        # 청산을 담당하고 synthetic 은 백업(preset 실패/naked·청산분)만. 노이즈성
        # 조기청산(+0.83% 등, 거래소 TP/SL 라인 도달 전 self-exit) 차단.
        self._native_tpsl_check = native_tpsl_check
        # #238 — 청산 시 (strategy_id, symbol) 콜백. orchestrator.release_live_position
        # 에 연결되어 live-scanner 재진입을 허용 (미연결 시 1회 진입 fail-safe 유지).
        self._on_exit = on_exit
        self._policies: dict[str, StopTpPolicy] = {}
        # high-water mark for trailing stop, keyed by (strategy_id, symbol)
        self._high_water: dict[tuple[str, str], Decimal] = {}
        # 2026-05-21 — per-(sid, symbol) dynamic policy override. ATR 기반
        # 동적 stop 등 strategy 가 진입 시점에 변동성 보고 계산한 값으로
        # 정적 % policy 를 덮어쓰기. orchestrator 의 _on_entry 콜백을 통해
        # `register_entry_override` 로 등록되고, evaluate 시 정적 _policies
        # 보다 우선 적용. stop/TP fire 시 자동 cleanup → 다음 진입은 새로
        # 계산된 override 로 다시 등록 (없으면 정적 fallback).
        self._dynamic_policies: dict[tuple[str, str], StopTpPolicy] = {}
        # 2026-05-21 — in-flight exit guard. stop/TP 발사 후 broker fill 도착
        # 전까진 같은 (sid, symbol) 에 대해 추가 SELL emit 차단. 미가드 시
        # 1초 간격 mark-price tick 마다 evaluate 가 돌면서 store 가 아직 갱신
        # 안 됐을 때 (fill 도착 < 1초 지연) 같은 SELL 을 또 fire → broker 가
        # 이미 청산된 포지션에 또 SELL 받음 → 예상치 못한 SHORT 진입 (실측
        # 2026-05-21 19:52:34 NEARUSDT -135 short). held=0 (= fill 반영됨)
        # 감지 시 자동 cleanup.
        #
        # 2026-05-24 — set → dict (key → emit_ts). broker fill 이
        # PENDING_EXIT_TIMEOUT_SEC 안에 안 도착하면 자동 해제 → 다음 tick 에
        # 재평가. broker rejection 으로 영구 stuck 되던 BTC/NEAR dust 사고 fix.
        self._pending_exit: dict[tuple[str, str], datetime] = {}
        self._pending_exit_timeout_sec: float = (
            pending_exit_timeout_sec
            if pending_exit_timeout_sec is not None
            else self.PENDING_EXIT_TIMEOUT_SEC
        )
        # 2026-06-15 시간기반 청산 — 진입 후 max_hold_sec 경과 + TP/SL 미도달이면
        # 시장가 청산. 거래소 네이티브 TP/SL 이 있어도 동작(거래소는 시간청산 안
        # 함) → 상승장 숏이 무한정 깔리는 것 차단. sim 의 4봉/1h hold 와 동일
        # 개념(대시보드 PF 가 이 timeout 포함 성적). None 이면 비활성(기존 동작).
        # 진입시각은 lazy 추적 — held!=0 첫 evaluate 시 stamp(재시작 시 소실 →
        # 재스탬프, trailing high_water 와 동일 한계).
        self._max_hold_sec: float | None = (
            float(max_hold_sec) if max_hold_sec else None
        )
        # 2026-06-18 per-strategy max_hold 오버라이드. 비어있으면 모든 전략이
        # global ``_max_hold_sec`` 사용(기존 동작 byte-identical). 전략이
        # `max_hold_sec` ClassVar 를 선언한 경우에만 `_register_exit_policies`
        # 가 여기 등록 → 그 전략만 별도(또는 None=time-stop 면제). MA크로스 같은
        # 추세추종(보유 수일~30일)이 airborne 의 global 1h time-stop 에 강제청산
        # 되는 충돌 차단용. airborne 은 ClassVar 미선언 → map 미등록 → 영향 0.
        self._max_hold_by_sid: dict[str, float | None] = {}
        self._entry_ts: dict[tuple[str, str], datetime] = {}

    def set_native_tpsl_check(self, check: Callable[[str], bool] | None) -> None:
        """P2 (2026-06-10) — broker adapter.has_native_tpsl 를 생성 *후* 주입.

        adapter 가 risk_mgr 보다 늦게 생성되므로 loop wiring 시점에 연결. True
        반환 종목은 evaluate 가 synthetic SL/TP 를 발동 안 함(거래소 preset 담당).
        """
        self._native_tpsl_check = check

    def register_strategy_policy(
        self,
        strategy_id: str,
        *,
        stop_loss_pct: float,
        take_profit_pct: float,
        trailing_stop_pct: float | None = None,
    ) -> None:
        self._policies[strategy_id] = StopTpPolicy(
            stop_loss_pct=stop_loss_pct,
            take_profit_pct=take_profit_pct,
            trailing_stop_pct=trailing_stop_pct,
        )

    def set_strategy_max_hold(
        self, strategy_id: str, max_hold_sec: float | None,
    ) -> None:
        """전략별 time-stop(max_hold) 오버라이드. ``None``/0 = 그 전략 time-stop
        면제(예: 추세추종 MA크로스). 양수 = 그 전략만 별도 보유한도(초).

        미호출 전략은 global ``_max_hold_sec`` 사용(기존 동작 불변). airborne 등
        ``max_hold_sec`` ClassVar 미선언 전략은 여기 등록 안 됨 → 영향 0.
        """
        self._max_hold_by_sid[strategy_id] = (
            float(max_hold_sec) if max_hold_sec else None
        )

    def _effective_max_hold(self, strategy_id: str) -> float | None:
        """해당 전략의 유효 max_hold. 오버라이드 있으면(None 포함) 그 값,
        없으면 global. ``dict.get`` 이라 sid→None 등록 시 None(면제) 반환."""
        return self._max_hold_by_sid.get(strategy_id, self._max_hold_sec)

    def effective_policy(self, strategy_id: str) -> tuple[float, float] | None:
        """거래소 네이티브 TP/SL 코디네이터용 — (stop_loss_pct, take_profit_pct).

        정적 정책 기준 *가격* pct (ROI 아님). 미등록 전략(cs-tsmom 등)은 None
        → 코디네이터가 거래소 보호 대상에서 제외.
        """
        pol = self._policies.get(strategy_id)
        if pol is None:
            return None
        return (pol.stop_loss_pct, pol.take_profit_pct)

    def register_entry_override(
        self,
        strategy_id: str,
        symbol: str,
        *,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
    ) -> None:
        """진입 시 dynamic policy override 등록 (ATR 기반 동적 stop 용).

        None 인 필드는 strategy 의 정적 _policies 값으로 fallback. 모든 필드가
        None 이면 등록 자체를 skip (정적 policy 그대로 사용). 다음 stop/TP 시
        자동 cleanup → 같은 (sid, symbol) 의 새 진입은 다시 호출되어야 함.
        """
        if (stop_loss_pct is None
                and take_profit_pct is None
                and trailing_stop_pct is None):
            return
        base = self._policies.get(strategy_id)
        if base is None:
            logger.debug(
                "register_entry_override: no static policy for sid=%s — override skipped",
                strategy_id,
            )
            return
        self._dynamic_policies[(strategy_id, symbol)] = StopTpPolicy(
            stop_loss_pct=stop_loss_pct if stop_loss_pct is not None else base.stop_loss_pct,
            take_profit_pct=take_profit_pct if take_profit_pct is not None else base.take_profit_pct,
            trailing_stop_pct=(
                trailing_stop_pct if trailing_stop_pct is not None else base.trailing_stop_pct
            ),
        )

    def evaluate(
        self,
        symbol: str,
        last_price: Decimal,
        ts: datetime,
    ) -> list[OrderIntent]:
        """Return SELL intents for any (strategy_id, symbol) that breached a threshold.

        Walks every registered strategy that holds *symbol*. For each:
          1. Look up entry (avg cost) from PnLAggregator._cost_basis.
          2. Update trailing-stop high-water mark.
          3. Check stop_loss / take_profit / trailing_stop in that order.
          4. On breach: emit OrderIntent(side='sell', qty=held), append WAL.
        """
        if not isinstance(last_price, Decimal):
            last_price = Decimal(str(last_price))

        intents: list[OrderIntent] = []
        for strategy_id, static_policy in self._policies.items():
            held, avg_cost = self._lookup_position(strategy_id, symbol)
            if held == 0 or avg_cost <= 0:
                # No position — clear any stale high-water tracking.
                #
                # 2026-05-21 race fix: 이전엔 여기서도 _dynamic_policies 를
                # POP 했었는데, 그게 진짜 race condition 의 원인이었음.
                # orchestrator._on_entry 는 BUY intent dispatch 시점에 호출되어
                # _dynamic_policies 에 새 override 가 들어가는데, broker fill
                # 도착 전엔 held=0 상태. 그 짧은 윈도우 동안 evaluate() 가 한 번
                # 이라도 돌면 방금 등록한 override 가 즉시 cleanup 으로 날아감
                # → 정적 policy (예: trailing 0.5%) 로 fallback → NEAR 같은
                # 변동성 큰 종목이 진입 직후 노이즈로 trailing fire (실측
                # 0.28% 손실).
                #
                # Legitimate cleanup 은 stop fire 직후 (아래쪽 _dynamic_policies
                # .pop) 에 이미 있음 — 그게 정상 청산 경로. 외부 청산 (manual
                # close, 강제 청산) 으로 인한 잔여 override 는 다음
                # register_entry_override 호출이 정확히 overwrite → stale 폐해
                # 없음.
                #
                # 2026-05-21 — in-flight exit guard 의 자연 cleanup. held=0 이
                # 되었다는 건 sell fill 이 도착해서 store 가 갱신된 것 → pending
                # 마크 해제. 다음 진입은 처음부터 가드 없이 정상 평가.
                self._high_water.pop((strategy_id, symbol), None)
                self._pending_exit.pop((strategy_id, symbol), None)
                self._entry_ts.pop((strategy_id, symbol), None)
                continue

            key = (strategy_id, symbol)
            # 진입 시각 lazy 기록 — held!=0 첫 평가 시 stamp (시간기반 청산용).
            self._entry_ts.setdefault(key, ts)

            # 2026-05-21 in-flight exit guard — 이전 evaluate 에서 stop/TP/timeout
            # 가 발사돼 SELL intent 가 broker 로 전송 중인 (sid, symbol) 은
            # broker fill 이 도착해 store 가 0 으로 갱신될 때까지 추가 평가 skip.
            # (2026-06-15 — timeout 도 이 가드를 존중하도록 native 체크보다 앞으로
            # 이동. native 활성이어도 timeout 은 평가돼야 하므로 순서 변경.)
            # 2026-05-24 self-heal — fill 이 _pending_exit_timeout_sec 안에 안
            # 도착하면 guard 자동 해제. broker rejection 등 영구 stuck 사고 fix.
            pending_ts = self._pending_exit.get(key)
            if pending_ts is not None:
                elapsed = (ts - pending_ts).total_seconds()
                if elapsed < self._pending_exit_timeout_sec:
                    continue
                logger.warning(
                    "live_position_risk.pending_exit_timeout sid=%s sym=%s "
                    "elapsed=%.1fs — guard 해제, 재평가",
                    strategy_id, symbol, elapsed,
                )
                self._pending_exit.pop(key, None)

            # 2026-05-21 — dynamic override 가 등록되어 있으면 그것을, 없으면
            # strategy 의 정적 policy 사용 (기존 동작). ATR 기반 동적 stop 활용.
            policy = self._dynamic_policies.get(key, static_policy)
            is_short = held < 0

            # 2026-06-15 시간기반 청산 — 진입 후 max_hold 경과 + TP/SL 미도달이면
            # 시장가 청산. **거래소 네이티브 TP/SL 이 있어도 동작** (거래소는 가격
            # 임계만, 시간청산은 안 함) → 상승장 숏이 무한정 깔리는 것 차단. sim 의
            # 4봉/1h hold 와 동일(대시보드 PF 가 이 timeout 포함 성적).
            triggered_reason: str | None = None
            entry_ts = self._entry_ts.get(key)
            _mh = self._effective_max_hold(strategy_id)
            if (
                _mh is not None and entry_ts is not None
                and (ts - entry_ts).total_seconds() >= _mh
            ):
                triggered_reason = "time_exit"

            if triggered_reason is None:
                # 2026-06-10 P2 — 네이티브 preset TP/SL 활성 종목은 가격 임계 청산을
                # 거래소가 담당 → synthetic 가격 청산 skip (timeout 은 위에서 이미
                # 평가됨 — 거래소가 못 하는 시간청산만 synthetic 이 책임).
                if self._native_tpsl_check is not None and self._native_tpsl_check(symbol):
                    continue
                # Trailing-stop water mark. LONG=high-water, SHORT=low-water.
                if policy.trailing_stop_pct is not None:
                    prev_mark = self._high_water.get(key, avg_cost)
                    if is_short:
                        if last_price < prev_mark:
                            prev_mark = last_price
                    else:
                        if last_price > prev_mark:
                            prev_mark = last_price
                    self._high_water[key] = prev_mark
                else:
                    prev_mark = avg_cost  # unused when trailing disabled
                triggered_reason = self._check_thresholds(
                    policy=policy,
                    avg_cost=avg_cost,
                    last_price=last_price,
                    water_mark=prev_mark,
                    is_short=is_short,
                )
                if triggered_reason is None:
                    continue
            else:
                # timeout 경로 — emission 로깅용 prev_mark.
                prev_mark = self._high_water.get(key, avg_cost)

            pct_change = float((last_price - avg_cost) / avg_cost)
            reason = (
                f"live_{triggered_reason}:"
                f"entry={avg_cost},last={last_price},pct={pct_change:+.4f}"
            )
            # LONG exit = SELL the held qty; SHORT exit = BUY (cover) abs(qty).
            # 2026-06-05: reduce_only=True 강제. 모든 청산 발주는 정의상 보유
            # 축소만 허용 — 어떤 이유로든 store qty 가 broker 실 qty 보다
            # 크게 박혔을 때 (예: 옛 disabled 전략 store 잔량 살아남 사고
            # 2026-06-05 BEATUSDT) broker 가 자동으로 over qty reject. PR
            # #362 의 cross-run filter 가 root cause 차단이고, 본 reduce_only
            # 강제는 안전망 2중. airborne 같은 bidir 전략의 short *진입* sell
            # 은 orchestrator 에서 만들고 본 함수와 무관 (PR #342 shorts_allowed
            # 가드 그대로 작동).
            intents.append(OrderIntent(
                strategy_id=strategy_id,
                symbol=symbol,
                side="buy" if is_short else "sell",
                qty=float(abs(held)),
                reason=reason,
                reduce_only=True,
            ))
            # 2026-06-14 진단 로그 (동작 변화 없음 — observability 전용).
            # "갓 진입한 재진입 포지션을 synthetic 이 즉시 phantom-청산" (AVAX/BTC/DOGE
            # 01:01 사고) 의 stale 필드를 사실로 못박기 위함. water_mark<avg_cost(숏)
            # 또는 dynamic policy 잔존이면 이전 사이클 stale 상태 상속을 의심.
            _is_dyn = (strategy_id, symbol) in self._dynamic_policies
            _stale_wm = (prev_mark < avg_cost) if is_short else (prev_mark > avg_cost)
            logger.info(
                "live_risk STOP-FIRE sid=%s %s %s side=%s held=%s entry=%s last=%s "
                "water_mark=%s stale_wm=%s dyn_policy=%s sl_pct=%s tp_pct=%s trail_pct=%s",
                strategy_id, symbol, triggered_reason,
                "buy" if is_short else "sell", held, avg_cost, last_price,
                prev_mark, _stale_wm, _is_dyn,
                policy.stop_loss_pct, policy.take_profit_pct, policy.trailing_stop_pct,
            )
            self._emit_stop_event(
                strategy_id=strategy_id,
                symbol=symbol,
                trigger=triggered_reason,
                avg_cost=avg_cost,
                last_price=last_price,
                qty=abs(held),
                pct_change=pct_change,
                ts=ts,
            )
            # Reset water mark + dynamic policy override for the next entry
            # into this (sid, symbol). 다음 진입은 새 ATR override 로 다시
            # register 되거나 (override 미설정 시) 정적 policy 로 fallback.
            self._high_water.pop((strategy_id, symbol), None)
            self._dynamic_policies.pop((strategy_id, symbol), None)
            # 2026-05-21 — in-flight exit guard. broker fill 이 도착해 store
            # 가 held=0 으로 갱신되기 전까지 같은 (sid, symbol) 추가 stop
            # 평가 차단. held=0 진입 시 위쪽 분기에서 자동 cleanup.
            # 2026-05-24 — emit_ts 기록 → fill 안 오면 timeout 으로 self-heal.
            self._pending_exit[(strategy_id, symbol)] = ts

        return intents

    def sweep_timeouts(
        self,
        now: datetime,
        price_lookup: Callable[[str], Decimal | None] | None = None,
    ) -> list[OrderIntent]:
        """Feed-독립 벽시계 timeout sweep — 보유 포지션 전체를 주기적으로 청산 점검.

        틱 구동 ``evaluate()`` 는 mark-price tick 이 *도착한* 종목만 평가한다
        (loop: ``evaluate(tick.symbol, ...)``). 토큰화 주식(NVDA/SPYUSDT)처럼
        ticker push 가 멈춘 종목은 evaluate 가 영영 안 불려 max_hold 가 지나도
        timeout 청산이 안 됐다 — 가격이 안 움직여 TP/SL 에도 안 걸리고 timeout 도
        못 도는 catch-22 (2026-06-16 NVDA/SPY 무한보유 사고. 같은 16:0x 진입분 중
        틱 도는 BTC 만 정시 timeout, 틱 멈춘 NVDA/SPY 만 잔존).

        본 sweep 은 inbound tick 과 무관하게 호출돼(loop 의 주기 task) 보유분을
        순회하며 max_hold 경과분을 시장가 reduce_only 로 청산한다. **timeout 전용** —
        가격 SL/TP/trailing 은 평가하지 않는다 (stale price 오발동 방지; 가격청산은
        틱 경로가 담당). ``max_hold_sec`` 비활성(None)이면 즉시 빈 리스트.

        entry_ts 는 틱 경로가 이미 정확히 stamp 했으면 그 값으로 정시 청산. 한 번도
        틱을 못 받아 미stamp 면 *이번 sweep 에 stamp 하고 skip* — 방금 진입분을
        즉시 죽이지 않도록 baseline 부터 카운트(최악 max_hold+sweep_interval 보유).
        """
        if self._max_hold_sec is None and not any(self._max_hold_by_sid.values()):
            return []
        intents: list[OrderIntent] = []
        for strategy_id in list(self._policies):
            _mh = self._effective_max_hold(strategy_id)
            if _mh is None:
                continue  # 이 전략은 time-stop 면제 (예: 추세추종 MA크로스).
            for symbol, _qty in self._position_store.get_positions(strategy_id):
                held, avg_cost = self._lookup_position(strategy_id, symbol)
                if held == 0 or avg_cost <= 0:
                    continue
                key = (strategy_id, symbol)
                entry_ts = self._entry_ts.get(key)
                if entry_ts is None:
                    # 틱 한 번도 못 받음 — 지금부터 baseline (다음 sweep 부터 카운트).
                    self._entry_ts[key] = now
                    continue
                if (now - entry_ts).total_seconds() < _mh:
                    continue
                # in-flight exit guard — evaluate() 와 동일 self-heal.
                pending_ts = self._pending_exit.get(key)
                if pending_ts is not None:
                    if (now - pending_ts).total_seconds() < self._pending_exit_timeout_sec:
                        continue
                    self._pending_exit.pop(key, None)
                is_short = held < 0
                last_price: Decimal | None = None
                if price_lookup is not None:
                    try:
                        lp = price_lookup(symbol)
                        last_price = lp if lp is None or isinstance(lp, Decimal) else Decimal(str(lp))
                    except Exception:  # noqa: BLE001 — 가격조회 실패가 청산을 막으면 안 됨
                        last_price = None
                if last_price is None or last_price <= 0:
                    last_price = avg_cost  # 시장가 reduce_only — 참조가, stale 허용.
                pct_change = float((last_price - avg_cost) / avg_cost)
                intents.append(OrderIntent(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    side="buy" if is_short else "sell",
                    qty=float(abs(held)),
                    reason=(
                        f"live_time_exit:entry={avg_cost},last={last_price},"
                        f"pct={pct_change:+.4f},src=sweep"
                    ),
                    reduce_only=True,
                ))
                logger.info(
                    "live_risk SWEEP-TIMEOUT sid=%s %s held=%s entry=%s last=%s "
                    "held_sec=%.0f max_hold=%.0f",
                    strategy_id, symbol, held, avg_cost, last_price,
                    (now - entry_ts).total_seconds(), _mh,
                )
                self._emit_stop_event(
                    strategy_id=strategy_id,
                    symbol=symbol,
                    trigger="time_exit",
                    avg_cost=avg_cost,
                    last_price=last_price,
                    qty=abs(held),
                    pct_change=pct_change,
                    ts=now,
                )
                self._high_water.pop(key, None)
                self._dynamic_policies.pop(key, None)
                self._pending_exit[key] = now
        return intents

    def policies(self) -> dict[str, StopTpPolicy]:
        """Read-only view for tests and dashboard /strategies/{id}/policy."""
        return dict(self._policies)

    # -- internals ----------------------------------------------------------

    def _lookup_position(
        self,
        strategy_id: str,
        symbol: str,
    ) -> tuple[Decimal, Decimal]:
        """Return (held_qty, avg_cost) for *(strategy_id, symbol)*; (0, 0) if flat.

        ``held`` is SIGNED — positive for a long, negative for a short
        (#238). Position store is the source of truth for qty (matches what
        the broker has filled); PnL aggregator's ``_cost_basis`` carries the
        entry price. We cross-check qty consistency but trust the store.
        """
        bucket = self._position_store._positions.get(strategy_id, {})
        held = bucket.get(symbol, Decimal("0"))
        if held == 0:
            return Decimal("0"), Decimal("0")
        cb_held, avg_cost = self._pnl._cost_basis.get(
            (strategy_id, symbol), (Decimal("0"), Decimal("0"))
        )
        # 2026-06-14 — cost basis 가 store qty 와 *부호*까지 어긋나면 avg_cost 가
        # 반대방향(stale) 진입가라 stop_loss 가 즉시 오발동한다. 정체: reconciler
        # 가 phantom-clear 할 때 force_sync_position 은 qty 만 0 으로 만들고
        # _cost_basis 는 stale 롱 진입가로 잔존 → 같은 (sid,symbol) 재진입(숏)이
        # held=-x 인데 avg_cost 는 옛 롱 진입가(예 6.572) → 숏 SL=6.572×1.005=6.605
        # < mark 6.758 → 진입 즉시 synthetic 이 reduce_only 로 청산(AVAX/BTC/DOGE
        # 01:01 "진입가에 −0.8% flat 청산" 사고의 정체). 부호 불일치 = cost basis
        # 무효 → avg_cost=0 반환해 호출부(held==0 or avg_cost<=0)가 평가 skip.
        # 거래소 native TP/SL(protective_coordinator)가 그 사이 보호를 커버하고,
        # 다음 실체결의 record_fill 이 cost basis 를 새 방향으로 갱신하면 재개된다.
        if cb_held != 0 and (cb_held > 0) != (held > 0):
            logger.warning(
                "live_position_risk.cost_basis_sign_mismatch sid=%s sym=%s "
                "store=%s pnl=%s — stale cost basis, synthetic 평가 skip "
                "(거래소 TP/SL 커버)",
                strategy_id, symbol, held, cb_held,
            )
            return held, Decimal("0")
        # 부호는 맞지만 수량만 다른 경우는 soft warning — store 가 트리거 기준.
        if cb_held != held:
            logger.debug(
                "live_position_risk.qty_mismatch sid=%s sym=%s store=%s pnl=%s",
                strategy_id, symbol, held, cb_held,
            )
        return held, avg_cost

    @staticmethod
    def _check_thresholds(
        *,
        policy: StopTpPolicy,
        avg_cost: Decimal,
        last_price: Decimal,
        water_mark: Decimal,
        is_short: bool = False,
    ) -> str | None:
        """Return the trigger name if any threshold breached, else None.

        Order matters: stop_loss first (worst case), then take_profit, then
        trailing_stop. Stop and TP are absolute distances from entry;
        trailing is a distance from the running water mark since entry.

        LONG (default): stop fires on price DOWN, take_profit on price UP,
        trailing tracks the HIGH-water and fires on a drop from it.

        SHORT (#238, ``is_short=True``): everything inverts — stop fires on
        price UP ``stop_loss_pct`` above entry, take_profit on price DOWN
        ``take_profit_pct`` below entry, trailing tracks the LOW-water and
        fires on a rise ``trailing_stop_pct`` above it (only once price has
        moved below entry, mirroring the long break-above gate).
        """
        sl_pct = Decimal(str(policy.stop_loss_pct))
        tp_pct = Decimal(str(policy.take_profit_pct))
        if is_short:
            sl = avg_cost * (Decimal("1") + sl_pct)
            if last_price >= sl:
                return "stop_loss"
            tp = avg_cost * (Decimal("1") - tp_pct)
            if last_price <= tp:
                return "take_profit"
            if policy.trailing_stop_pct is not None:
                trail = water_mark * (
                    Decimal("1") + Decimal(str(policy.trailing_stop_pct))
                )
                if last_price >= trail and water_mark < avg_cost:
                    return "trailing_stop"
            return None

        sl = avg_cost * (Decimal("1") - sl_pct)
        if last_price <= sl:
            return "stop_loss"
        tp = avg_cost * (Decimal("1") + tp_pct)
        if last_price >= tp:
            return "take_profit"
        if policy.trailing_stop_pct is not None:
            trail = water_mark * (Decimal("1") - Decimal(str(policy.trailing_stop_pct)))
            if last_price <= trail and water_mark > avg_cost:
                # Only fire trailing once price has moved above entry — avoids
                # double-counting against the stop_loss check above.
                return "trailing_stop"
        return None

    def _emit_stop_event(
        self,
        *,
        strategy_id: str,
        symbol: str,
        trigger: str,
        avg_cost: Decimal,
        last_price: Decimal,
        qty: Decimal,
        pct_change: float,
        ts: datetime,
    ) -> None:
        if self._wal_observer is None:
            return
        event = WALEvent(
            ts=(ts or datetime.now(timezone.utc)).isoformat(),
            event_type=self.EVENT_TYPE,
            payload={
                "strategy_id": strategy_id,
                "symbol": symbol,
                "trigger": trigger,
                "avg_cost": str(avg_cost),
                "last_price": str(last_price),
                "qty": str(qty),
                "pct_change": pct_change,
            },
        )
        try:
            self._wal_observer(event)
        except Exception as err:
            logger.warning(
                "live_position_risk.wal_observer_error sid=%s sym=%s trigger=%s error=%s",
                strategy_id, symbol, trigger, err,
            )
        # #238 — 청산 완료 → orchestrator 진입 기록 해제 (재진입 허용).
        if self._on_exit is not None:
            try:
                self._on_exit(strategy_id, symbol)
            except Exception as err:
                logger.warning(
                    "live_position_risk.on_exit_error sid=%s sym=%s error=%s",
                    strategy_id, symbol, err,
                )
