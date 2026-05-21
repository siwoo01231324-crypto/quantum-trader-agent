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
from typing import Callable

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

    def __init__(
        self,
        *,
        position_store: StrategyPositionStore,
        pnl_aggregator: PnLAggregator,
        wal_observer: Callable[[WALEvent], None] | None = None,
        on_exit: Callable[[str, str], None] | None = None,
    ) -> None:
        self._position_store = position_store
        self._pnl = pnl_aggregator
        self._wal_observer = wal_observer
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
                self._high_water.pop((strategy_id, symbol), None)
                continue

            # 2026-05-21 — dynamic override 가 등록되어 있으면 그것을, 없으면
            # strategy 의 정적 policy 사용 (기존 동작). ATR 기반 동적 stop 활용.
            policy = self._dynamic_policies.get((strategy_id, symbol), static_policy)

            is_short = held < 0

            # Trailing-stop water mark. LONG tracks the HIGH-water (peak
            # since entry); SHORT tracks the LOW-water (trough since entry).
            if policy.trailing_stop_pct is not None:
                key = (strategy_id, symbol)
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

            pct_change = float((last_price - avg_cost) / avg_cost)
            reason = (
                f"live_{triggered_reason}:"
                f"entry={avg_cost},last={last_price},pct={pct_change:+.4f}"
            )
            # LONG exit = SELL the held qty; SHORT exit = BUY (cover) abs(qty).
            intents.append(OrderIntent(
                strategy_id=strategy_id,
                symbol=symbol,
                side="buy" if is_short else "sell",
                qty=float(abs(held)),
                reason=reason,
            ))
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
        # Mismatch is a soft warning — store is authoritative for triggering.
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
