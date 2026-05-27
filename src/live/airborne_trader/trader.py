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
    """본 PR scope 밖. 실제 Binance Futures client 는 후속 PR.

    DummyBroker 로 단위 테스트 가능하게 추상화만 명시.
    """

    async def place_market_order(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult: ...

    async def get_mark_price(self, symbol: str) -> float: ...

    async def close_position(
        self, *, symbol: str, side: str, qty: float,
    ) -> OrderResult: ...


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
