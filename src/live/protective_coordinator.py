"""거래소 네이티브 TP/SL 코디네이터 (2026-06-08).

진입 체결 시 거래소에 익절/손절 plan order 를 등록하고, 청산(포지션 net=0)
시 취소한다. fill consumer 가 매 fill 후 ``on_fill`` 을 호출 → 이 코디네이터가
position_store 의 net 을 보고 등록/취소를 결정한다.

synthetic ``LivePositionRiskManager`` 는 *백업*으로 유지 — 거래소 미등록/지연
구간(주문 실패, 재기동 윈도우)을 커버한다. 거래소 보호주문이 먼저 청산하면
synthetic 의 reduce-only close 는 no-op 이라 무해하다.

⚠️ 진입가 = ``fill.price`` (실제 체결가). trigger = entry × (1 ± *가격*pct),
side-aware (``ProtectiveOrderManager._compute_protection_prices`` 재사용).
가격pct 는 전략의 ``take_profit_pct``/``stop_loss_pct`` (= 이미 가격 기준,
ROI 아님 — ROI = 가격변동% × leverage).
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Callable

from src.brokers.protective_orders import (
    ProtectiveOrderConfig,
    ProtectiveOrderManager,
)

logger = logging.getLogger(__name__)

PolicyLookup = Callable[[str], "tuple[float, float] | None"]


class ProtectiveOrderCoordinator:
    """진입 체결 → 거래소 TP/SL 등록, 청산 → 취소. 단일 브로커(async) scope."""

    def __init__(
        self,
        *,
        adapter: Any,            # async place/cancel/list_open_protective_order
        position_store: Any,     # get_positions(sid) -> list[(symbol, qty)]
        policy_lookup: PolicyLookup,   # sid -> (stop_loss_pct, take_profit_pct) | None
    ) -> None:
        self._adapter = adapter
        self._store = position_store
        self._policy_lookup = policy_lookup
        # (sid, symbol) -> {"sl": order_id, "tp": order_id}
        self._registered: dict[tuple[str, str], dict[str, str]] = {}

    # ── public ────────────────────────────────────────────────────────────────

    async def on_fill(
        self,
        *,
        symbol: str,
        side: str,
        strategy_id: str | None,
        fill: Any,
    ) -> None:
        """fill consumer 가 매 체결 후 호출. net 을 보고 등록/취소 결정.

        절대 예외를 밖으로 던지지 않는다 — 보호주문 실패가 fill 처리(WAL/
        store/pnl)를 깨면 안 됨. 실패는 로그만, synthetic 백업이 커버.
        """
        try:
            await self._on_fill_impl(symbol=symbol, strategy_id=strategy_id, fill=fill)
        except Exception as exc:  # noqa: BLE001 — fill 경로를 절대 안 깬다
            logger.error(
                "protective_coordinator: on_fill error sid=%s %s: %s "
                "(synthetic 백업이 커버)",
                strategy_id, symbol, exc,
            )

    # ── internal ──────────────────────────────────────────────────────────────

    async def _on_fill_impl(
        self, *, symbol: str, strategy_id: str | None, fill: Any,
    ) -> None:
        if not strategy_id or not symbol:
            return  # orphan — TP/SL 설정 모름. synthetic/reconciler 가 커버.
        key = (strategy_id, symbol)
        net = self._net_qty(strategy_id, symbol)

        if net == 0:
            await self._cancel(key, symbol)   # 청산 완료 → 보호주문 취소
            return

        if key in self._registered:
            return  # 이미 등록됨 (add/partial 은 v1 에서 재등록 안 함)

        policy = None
        try:
            policy = self._policy_lookup(strategy_id)
        except Exception:  # noqa: BLE001
            policy = None
        if policy is None:
            return  # TP/SL 정책 없는 전략 (cs-tsmom 등) — 거래소 보호 대상 아님
        sl_pct, tp_pct = policy

        try:
            entry_price = Decimal(str(fill.price))
        except Exception:  # noqa: BLE001
            return
        if entry_price <= 0:
            return

        entry_side = "BUY" if net > 0 else "SELL"   # net 부호로 포지션 방향 도출
        qty = abs(net)
        cfg = ProtectiveOrderConfig(
            stop_loss_pct=Decimal(str(sl_pct)),
            take_profit_pct=Decimal(str(tp_pct)),
        )
        sl_price, tp_price, close_side = (
            ProtectiveOrderManager._compute_protection_prices(
                entry_side=entry_side, entry_price=entry_price, config=cfg,
            )
        )
        sl_id = await self._adapter.place_protective_order(
            symbol=symbol, side=close_side, qty=qty,
            stop_price=sl_price, kind="STOP_MARKET",
        )
        tp_id = await self._adapter.place_protective_order(
            symbol=symbol, side=close_side, qty=qty,
            stop_price=tp_price, kind="TAKE_PROFIT_MARKET",
        )
        self._registered[key] = {"sl": sl_id, "tp": tp_id}
        logger.info(
            "protective_coordinator: registered sid=%s %s side=%s qty=%s "
            "entry=%s SL=%s TP=%s",
            strategy_id, symbol, entry_side, qty, entry_price, sl_price, tp_price,
        )

    async def _cancel(self, key: tuple[str, str], symbol: str) -> None:
        ids = self._registered.pop(key, None)
        if not ids:
            return
        for oid in (ids.get("sl"), ids.get("tp")):
            if not oid:
                continue
            try:
                await self._adapter.cancel_protective_order(
                    symbol=symbol, broker_order_id=oid,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "protective_coordinator: cancel failed %s oid=%s: %s",
                    symbol, oid, exc,
                )
        logger.info("protective_coordinator: cancelled sid=%s %s", key[0], symbol)

    def _net_qty(self, sid: str, symbol: str) -> Decimal:
        try:
            for sym, qty in self._store.get_positions(sid):
                if sym == symbol:
                    return Decimal(str(qty))
        except Exception:  # noqa: BLE001
            pass
        return Decimal("0")
