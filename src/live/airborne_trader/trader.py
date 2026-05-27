"""AirborneTrader — main async loop.

Cycle (every ``config.poll_interval_seconds``):
  1. listener.poll_new() → new FIRE records
  2. for each fire:
     - dedup via state.is_fire_processed
     - risk.evaluate(fire) → ok / reject
     - if ok: broker.place_market_order + state.open_position
     - record_fire_decision (audit)
  3. for each open position:
     - broker.get_mark_price(symbol) → check stop / TP
     - if hit: broker.close_position + state.close_position + record realized_pnl

Broker:
  Protocol — actual Binance integration in subsequent PR. 본 PR 에서는 dry_run
  + DummyBroker (no-op) 로 path 검증만.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Protocol

from live.airborne_fire_listener import AirborneFireListener, FireRecord

from .config import AirborneTraderConfig
from .risk import AirborneTraderRisk
from .state import AirborneTraderState, FireDecision, PositionRecord

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrderResult:
    """Broker 발주 결과. ``filled_qty`` 0 = 실패 / dry-run."""
    symbol: str
    side: str        # 'BUY' | 'SELL'
    filled_qty: float
    avg_price: float
    raw_response: dict


class BrokerInterface(Protocol):
    """Broker abstraction — DummyBroker (dry-run/tests) 와 BinanceFuturesBroker
    (실거래) 모두 같은 API 로 동작.
    """

    async def place_market_order(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult: ...

    async def get_mark_price(self, symbol: str) -> float: ...

    async def close_position(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult: ...

    async def get_open_position_qty(self, symbol: str) -> float:
        """Reconciler 용 — broker 측 실 포지션 수량 (NET long+, short−, flat 0).

        DummyBroker 같은 sim broker 는 0.0 반환 (= broker flat = SQLite state 만 신뢰).
        BinanceFuturesBroker 는 /fapi/v2/positionRisk 호출.
        """
        ...


class DummyBroker:
    """Logging-only broker — dry-run / 단위 테스트 용. 발주 X.

    behavior: place_market_order 항상 ``filled_qty=qty``, avg_price=requested_price
    return. get_mark_price 는 ``self.mark_prices`` dict 참조 (테스트에서 set).
    """

    def __init__(self) -> None:
        self.mark_prices: dict[str, float] = {}
        self.orders_log: list[dict] = []

    async def place_market_order(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult:
        price = self.mark_prices.get(symbol, 0.0)
        record = {
            "symbol": symbol, "side": side, "qty": qty,
            "avg_price": price, "dry_run": True,
        }
        self.orders_log.append(record)
        return OrderResult(
            symbol=symbol, side=side, filled_qty=qty,
            avg_price=price, raw_response=record,
        )

    async def get_mark_price(self, symbol: str) -> float:
        return self.mark_prices.get(symbol, 0.0)

    async def close_position(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult:
        opposite = "SELL" if side == "BUY" else "BUY"
        return await self.place_market_order(
            symbol=symbol, side=opposite, qty=qty,
        )

    async def get_open_position_qty(self, symbol: str) -> float:
        """Dry-run broker — broker 측 실 포지션 없다고 가정 (state 만 신뢰)."""
        return 0.0


class AirborneTrader:
    """Main async loop. ``stop_event`` 으로 graceful shutdown."""

    def __init__(
        self,
        *,
        config: AirborneTraderConfig,
        state: AirborneTraderState,
        risk: AirborneTraderRisk,
        listener: AirborneFireListener,
        broker: BrokerInterface,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self.state = state
        self.risk = risk
        self.listener = listener
        self.broker = broker
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self.stop_event = asyncio.Event()

    # ── Position size ──────────────────────────────────────────────────────
    def _compute_qty(self, fire_close: float) -> float:
        """USDT 노출 / price = base 통화 수량 (예: 0.001 BTC)."""
        if fire_close <= 0:
            raise ValueError(f"fire_close > 0 required, got {fire_close}")
        return self.config.position_usd / fire_close

    # ── Single cycle handlers ──────────────────────────────────────────────
    async def handle_fire(self, fire: FireRecord) -> None:
        """단일 FIRE 처리 — risk gate → place order → record state."""
        key = ":".join(fire.key())
        if self.state.is_fire_processed(key):
            return

        now = self._now()
        decision = self.risk.evaluate(fire, now_utc=now)
        if not decision.ok:
            # Daily loss limit 도달 첫 reject 시 kill switch 자동 트리거.
            # 이후 모든 fire 는 risk.evaluate 의 gate 0 (kill_switch_active) 에서
            # 차단됨 — manual unlock (--unlock-daily-kill) 까지 유지.
            if (
                decision.reason.startswith("daily_loss_limit")
                and not self.state.is_kill_switch_active()
            ):
                self.state.trigger_kill_switch(reason=decision.reason)
                logger.warning(
                    "[airborne_trader] KILL SWITCH triggered — %s. "
                    "manual unlock 까지 모든 신규 진입 차단.",
                    decision.reason,
                )
            self.state.record_fire_decision(
                fire_key=key, ts_iso=fire.ts.isoformat(),
                symbol=fire.symbol, side=fire.side,
                decision=FireDecision.SKIPPED, reason=decision.reason,
            )
            logger.info(
                "[airborne_trader] SKIP %s %s @ %s — %s",
                fire.symbol, fire.side, fire.ts.isoformat(), decision.reason,
            )
            return

        qty = self._compute_qty(fire.fire_close)
        broker_side = "BUY" if fire.side == "long" else "SELL"

        if self.config.dry_run:
            avg_price = fire.fire_close
            logger.info(
                "[airborne_trader] DRY_RUN PLACE %s %s qty=%.6f @ %.6f (fire.close)",
                fire.symbol, broker_side, qty, avg_price,
            )
        else:
            try:
                result = await self.broker.place_market_order(
                    symbol=fire.symbol, side=broker_side, qty=qty,
                )
                avg_price = float(result.avg_price) or fire.fire_close
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    "[airborne_trader] place_market_order failed %s %s: %s",
                    fire.symbol, broker_side, err,
                )
                self.state.record_fire_decision(
                    fire_key=key, ts_iso=fire.ts.isoformat(),
                    symbol=fire.symbol, side=fire.side,
                    decision=FireDecision.SKIPPED,
                    reason=f"broker_error: {type(err).__name__}: {err}",
                )
                return

        # Stop / TP 가격 계산
        if fire.side == "long":
            stop_px = avg_price * (1 - self.config.stop_loss_pct)
            tp_px = avg_price * (1 + self.config.take_profit_pct)
        else:
            stop_px = avg_price * (1 + self.config.stop_loss_pct)
            tp_px = avg_price * (1 - self.config.take_profit_pct)

        self.state.open_position(
            symbol=fire.symbol, side=fire.side,
            entry_ts_iso=now.isoformat(), entry_px=avg_price,
            qty=qty, stop_px=stop_px, tp_px=tp_px, fire_key=key,
        )
        self.state.record_fire_decision(
            fire_key=key, ts_iso=fire.ts.isoformat(),
            symbol=fire.symbol, side=fire.side,
            decision=FireDecision.PLACED,
            reason=f"placed qty={qty:.6f} entry={avg_price:.6f} "
                   f"stop={stop_px:.6f} tp={tp_px:.6f}",
        )

    async def monitor_positions(self) -> None:
        """모든 open position 의 mark_price 확인 → stop/TP 도달 시 청산."""
        positions = self.state.list_open_positions()
        for pos in positions:
            try:
                mark = await self.broker.get_mark_price(pos.symbol)
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    "[airborne_trader] get_mark_price failed %s: %s",
                    pos.symbol, err,
                )
                continue
            if mark <= 0:
                continue
            await self._maybe_close(pos, mark)

    async def _maybe_close(self, pos: PositionRecord, mark: float) -> None:
        """단일 포지션의 stop/TP 도달 시 청산."""
        if pos.side == "long":
            hit_stop = mark <= pos.stop_px
            hit_tp = mark >= pos.tp_px
        else:
            hit_stop = mark >= pos.stop_px
            hit_tp = mark <= pos.tp_px

        if not (hit_stop or hit_tp):
            return

        # stop 과 TP 동시 도달 시 stop 우선 (보수적)
        if hit_stop:
            status = "closed_sl"
            exit_px = pos.stop_px
        else:
            status = "closed_tp"
            exit_px = pos.tp_px

        if self.config.dry_run:
            logger.info(
                "[airborne_trader] DRY_RUN CLOSE pos=%d %s %s @ %.6f (%s)",
                pos.id, pos.symbol, pos.side, exit_px, status,
            )
        else:
            try:
                broker_side = "BUY" if pos.side == "long" else "SELL"
                await self.broker.close_position(
                    symbol=pos.symbol, side=broker_side, qty=pos.qty,
                )
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    "[airborne_trader] close_position failed %s: %s",
                    pos.symbol, err,
                )
                return

        # 실현손익 계산
        if pos.side == "long":
            pnl_usd = (exit_px - pos.entry_px) * pos.qty
        else:
            pnl_usd = (pos.entry_px - exit_px) * pos.qty

        self.state.close_position(
            position_id=pos.id,
            exit_ts_iso=self._now().isoformat(),
            exit_px=exit_px, status=status,
            realized_pnl_usd=pnl_usd,
        )
        logger.info(
            "[airborne_trader] CLOSE pos=%d %s %s entry=%.6f exit=%.6f "
            "pnl=%.2f USDT status=%s",
            pos.id, pos.symbol, pos.side, pos.entry_px, exit_px,
            pnl_usd, status,
        )

    # ── Startup reconciler ────────────────────────────────────────────────
    async def reconcile_on_startup(self) -> dict[str, int]:
        """Process 시작 시 broker 잔고 ↔ SQLite state 동기화.

        SQLite 의 open positions 각각:
          - broker 측 net_qty == 0 (flat) → state 의 position 을 closed_manual
            로 mark (broker UI 또는 다른 process 가 청산한 경우).
          - broker 측 net_qty != 0 → 정상, 그대로 monitor 계속.
          - broker get_open_position_qty 가 raise → skip (계속 보유로 가정).

        broker 에는 있는데 SQLite 에 없는 포지션은 warning 로그만 (이미 cs-tsmom
        등 다른 entity 가 보유 중일 수 있음 — airborne_trader 가 청산할 권한 X).

        Returns:
          {"reconciled_closed": N, "still_open": M, "errors": E}
        """
        open_positions = self.state.list_open_positions()
        if not open_positions:
            logger.info("[airborne_trader] reconciler — no open positions")
            return {"reconciled_closed": 0, "still_open": 0, "errors": 0}

        closed = 0
        still_open = 0
        errors = 0
        for pos in open_positions:
            try:
                broker_qty = await self.broker.get_open_position_qty(pos.symbol)
            except Exception as err:  # noqa: BLE001
                logger.warning(
                    "[airborne_trader] reconciler get_open_position_qty %s failed: %s",
                    pos.symbol, err,
                )
                errors += 1
                continue

            # Flat tolerance: |broker_qty| < 1e-9 = flat (수량 step 차이 안전 마진).
            if abs(broker_qty) < 1e-9:
                # Broker 측 flat — state mismatch. closed_manual 로 표시.
                # exit_px 는 알 수 없으니 entry_px 사용 (보수적, pnl=0).
                self.state.close_position(
                    position_id=pos.id,
                    exit_ts_iso=self._now().isoformat(),
                    exit_px=pos.entry_px,
                    status="closed_manual",
                    realized_pnl_usd=0.0,
                )
                closed += 1
                logger.warning(
                    "[airborne_trader] reconciler — pos %d %s %s closed (broker flat). "
                    "exit_px=entry_px (pnl=0 보수). 실 broker UI 청산이면 income 원장에서 확인.",
                    pos.id, pos.symbol, pos.side,
                )
            else:
                still_open += 1
                logger.info(
                    "[airborne_trader] reconciler — pos %d %s broker_qty=%.6f OK",
                    pos.id, pos.symbol, broker_qty,
                )

        logger.info(
            "[airborne_trader] reconciler done — closed=%d still_open=%d errors=%d",
            closed, still_open, errors,
        )
        return {
            "reconciled_closed": closed,
            "still_open": still_open,
            "errors": errors,
        }

    # ── Main loop ──────────────────────────────────────────────────────────
    async def run_one_cycle(self) -> None:
        """단일 cycle: poll listener → handle fires → monitor positions."""
        fires = self.listener.poll_new()
        for fire in fires:
            await self.handle_fire(fire)
        await self.monitor_positions()

    async def run(self) -> None:
        """주기적 실행. ``stop_event`` 으로 graceful shutdown."""
        if not self.listener.started:
            self.listener.start_at(self._now())
        logger.info(
            "[airborne_trader] starting — dry_run=%s position_usd=%.0f "
            "max_concurrent=%d kst_hours=%s",
            self.config.dry_run, self.config.position_usd,
            self.config.max_concurrent_positions,
            sorted(self.config.kst_entry_hours),
        )
        # Startup reconciler — process restart 시 broker 잔고 vs SQLite mismatch 정리.
        try:
            await self.reconcile_on_startup()
        except Exception as err:  # noqa: BLE001
            logger.exception("[airborne_trader] reconciler failed: %s", err)
        while not self.stop_event.is_set():
            try:
                await self.run_one_cycle()
            except Exception as err:  # noqa: BLE001
                logger.exception("[airborne_trader] cycle failed: %s", err)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(),
                    timeout=self.config.poll_interval_seconds,
                )
            except asyncio.TimeoutError:
                pass
        logger.info("[airborne_trader] stopped")
