"""Protective order manager — auto-register stop-loss/take-profit on entry (#127).

Phase 3 (실자금) 진입 전 안전망. PC 다운/네트워크 단절 시 보유 포지션을 거래소
측에 사전 등록한 보호 주문이 자동 청산해줌.

Flow:
1. 전략이 entry 주문 체결 → ProtectiveOrderManager.register_protection() 호출
2. 매니저는 브로커별 `place_protective_order(kind="STOP_MARKET", ...)` 호출
   → Binance: STOP_MARKET + TAKE_PROFIT_MARKET (reduceOnly=True)
   → KIS:    예약매도 (조건부 주문) — 후속 PR 에서 통합
3. 청산 시 `cancel_protection()` → 양 보호 주문 취소
4. PC 재기동 시 `sync_from_broker()` → 거래소 측 open orders 조회로 상태 복원

WAL 감사 로그 (선택):
- protective_registered  : 보호 주문 등록 성공
- protective_cancelled   : 명시적 취소 (entry 청산 시)
- protective_orphaned    : sync 시 발견된 알 수 없는 보호 주문

CLAUDE.md 불변식 6: LLM 위임 금지. 본 매니저는 결정 로직 0 — 가격 계산은
순수 산술, broker 호출은 단순 forwarding.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

ProtectiveKind = Literal["STOP_MARKET", "TAKE_PROFIT_MARKET"]
PositionSide = Literal["BUY", "SELL"]  # BUY = long entry, SELL = short entry


@dataclass(frozen=True)
class ProtectiveOrderConfig:
    """보호 주문 설정 — entry 시 strategy 가 매니저에게 넘김."""

    stop_loss_pct: Decimal       # 진입가 대비 손절폭 (예: 0.02 = 2%)
    take_profit_pct: Decimal     # 진입가 대비 익절폭 (예: 0.04 = 4%)

    def __post_init__(self) -> None:
        if self.stop_loss_pct <= Decimal("0"):
            raise ValueError(f"stop_loss_pct must be > 0, got {self.stop_loss_pct}")
        if self.take_profit_pct <= Decimal("0"):
            raise ValueError(f"take_profit_pct must be > 0, got {self.take_profit_pct}")


@dataclass(frozen=True)
class ProtectivePair:
    """등록된 보호 주문 한 쌍 (stop-loss + take-profit) 의 broker_order_id."""

    symbol: str
    sl_order_id: str
    tp_order_id: str
    entry_side: PositionSide
    stop_price: Decimal
    take_profit_price: Decimal
    qty: Decimal
    registered_at: str  # UTC ISO


class ProtectiveBrokerProtocol(Protocol):
    """브로커가 매니저와 통신하기 위해 구현해야 하는 최소 인터페이스.

    BinanceFuturesAdapter / KISAdapter 가 `place_protective_order` /
    `cancel_protective_order` 를 별도로 추가한다 (place_order 와 분리 — 보호
    주문은 reduceOnly + 가격 트리거라 일반 주문 schema 와 의미가 다름).
    """

    name: str

    def place_protective_order(
        self,
        *,
        symbol: str,
        side: PositionSide,
        qty: Decimal,
        stop_price: Decimal,
        kind: ProtectiveKind,
    ) -> str:
        """브로커에 보호 주문 제출. broker_order_id 반환."""
        ...

    def cancel_protective_order(
        self,
        *,
        symbol: str,
        broker_order_id: str,
    ) -> None:
        """등록된 보호 주문 취소."""
        ...

    def list_open_protective_orders(
        self,
        *,
        symbol: str | None = None,
    ) -> list[dict]:
        """현재 거래소 측에 살아있는 보호 주문 목록 (PC 재기동 동기화용)."""
        ...


class WALLikeProtocol(Protocol):
    def write(self, event: object) -> None: ...


class ProtectiveOrderManager:
    """진입 시 stop-loss + take-profit 자동 등록, 청산 시 자동 취소.

    Single-broker scope: 인스턴스 1개 = 브로커 1개. 멀티 브로커는 매니저 여러개
    인스턴스화. (#143 Binance + #133 KIS 가 분리되어 있어 자연스러움)
    """

    def __init__(
        self,
        broker: ProtectiveBrokerProtocol,
        wal: WALLikeProtocol | None = None,
    ) -> None:
        self._broker = broker
        self._wal = wal
        # symbol → ProtectivePair
        self._registered: dict[str, ProtectivePair] = {}

    # ── public API ──────────────────────────────────────────────────────────

    def register_protection(
        self,
        *,
        symbol: str,
        entry_side: PositionSide,
        qty: Decimal,
        entry_price: Decimal,
        config: ProtectiveOrderConfig,
    ) -> ProtectivePair:
        """진입 후 호출 — 거래소에 보호 주문 한 쌍 제출.

        멱등성: 같은 symbol 에 이미 등록된 보호 주문이 있으면 ValueError.
        호출자가 cancel_protection 후 재등록해야 함 (의도적 — 의도치 않은
        중복 등록 방지).
        """
        if symbol in self._registered:
            raise ValueError(
                f"protection already registered for {symbol}; cancel first"
            )

        sl_price, tp_price, close_side = self._compute_protection_prices(
            entry_side=entry_side,
            entry_price=entry_price,
            config=config,
        )

        sl_id = self._broker.place_protective_order(
            symbol=symbol,
            side=close_side,
            qty=qty,
            stop_price=sl_price,
            kind="STOP_MARKET",
        )
        tp_id = self._broker.place_protective_order(
            symbol=symbol,
            side=close_side,
            qty=qty,
            stop_price=tp_price,
            kind="TAKE_PROFIT_MARKET",
        )

        pair = ProtectivePair(
            symbol=symbol,
            sl_order_id=sl_id,
            tp_order_id=tp_id,
            entry_side=entry_side,
            stop_price=sl_price,
            take_profit_price=tp_price,
            qty=qty,
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
        self._registered[symbol] = pair
        self._wal_emit(
            "protective_registered",
            {
                "symbol": symbol,
                "sl_order_id": sl_id,
                "tp_order_id": tp_id,
                "entry_side": entry_side,
                "stop_price": str(sl_price),
                "take_profit_price": str(tp_price),
                "qty": str(qty),
                "broker": self._broker.name,
            },
        )
        logger.info(
            "protective_registered symbol=%s sl=%s tp=%s qty=%s",
            symbol, sl_price, tp_price, qty,
        )
        return pair

    def cancel_protection(self, *, symbol: str) -> ProtectivePair | None:
        """청산 시 호출 — 등록된 보호 주문 한 쌍 취소.

        Returns: 취소된 ProtectivePair 또는 None (등록 안 돼있던 경우).
        """
        pair = self._registered.pop(symbol, None)
        if pair is None:
            logger.debug("cancel_protection: %s not registered, noop", symbol)
            return None
        for order_id in (pair.sl_order_id, pair.tp_order_id):
            try:
                self._broker.cancel_protective_order(
                    symbol=symbol, broker_order_id=order_id,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "cancel_protective_order failed symbol=%s order_id=%s: %s",
                    symbol, order_id, exc,
                )
        self._wal_emit(
            "protective_cancelled",
            {
                "symbol": symbol,
                "sl_order_id": pair.sl_order_id,
                "tp_order_id": pair.tp_order_id,
                "broker": self._broker.name,
            },
        )
        return pair

    def sync_from_broker(self, *, symbol: str | None = None) -> dict[str, list[dict]]:
        """PC 재기동 시 호출 — 거래소 측 살아있는 보호 주문을 매니저 내부 상태와 비교.

        Returns:
            {"orphaned": [...]}  — 거래소엔 있지만 매니저 등록 안 된 보호 주문.
                호출자가 일괄 취소하거나 register 와 다시 매핑.
        """
        live_orders = self._broker.list_open_protective_orders(symbol=symbol)
        registered_ids = {
            oid
            for pair in self._registered.values()
            for oid in (pair.sl_order_id, pair.tp_order_id)
        }
        orphaned = [
            o for o in live_orders
            if str(o.get("broker_order_id") or o.get("orderId") or "") not in registered_ids
        ]
        for o in orphaned:
            self._wal_emit(
                "protective_orphaned",
                {
                    "broker": self._broker.name,
                    "broker_order_id": o.get("broker_order_id") or o.get("orderId"),
                    "symbol": o.get("symbol"),
                    "kind": o.get("type") or o.get("kind"),
                },
            )
        logger.info(
            "sync_from_broker symbol=%s live=%d registered=%d orphaned=%d",
            symbol, len(live_orders), len(registered_ids), len(orphaned),
        )
        return {"orphaned": orphaned}

    def get_registered(self, symbol: str) -> ProtectivePair | None:
        return self._registered.get(symbol)

    def list_registered(self) -> list[ProtectivePair]:
        return list(self._registered.values())

    # ── internal ────────────────────────────────────────────────────────────

    @staticmethod
    def _compute_protection_prices(
        *,
        entry_side: PositionSide,
        entry_price: Decimal,
        config: ProtectiveOrderConfig,
    ) -> tuple[Decimal, Decimal, PositionSide]:
        """LONG entry → close_side=SELL, SL = entry × (1-pct), TP = entry × (1+pct).
        SHORT entry → close_side=BUY,  SL = entry × (1+pct), TP = entry × (1-pct)."""
        if entry_side == "BUY":
            sl = entry_price * (Decimal("1") - config.stop_loss_pct)
            tp = entry_price * (Decimal("1") + config.take_profit_pct)
            return sl, tp, "SELL"
        else:  # SELL (short entry)
            sl = entry_price * (Decimal("1") + config.stop_loss_pct)
            tp = entry_price * (Decimal("1") - config.take_profit_pct)
            return sl, tp, "BUY"

    def _wal_emit(self, event_type: str, payload: dict) -> None:
        if self._wal is None:
            return
        try:
            from src.live.types import WALEvent  # noqa: PLC0415
            ev = WALEvent(
                ts=datetime.now(timezone.utc).isoformat(),
                event_type=event_type,
                payload=payload,
            )
            self._wal.write(ev)
        except Exception as exc:  # noqa: BLE001
            logger.warning("wal_emit failed event=%s: %s", event_type, exc)
