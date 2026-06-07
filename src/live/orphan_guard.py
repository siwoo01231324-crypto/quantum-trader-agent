"""OrphanGuard — broker-truth 기반 포지션 정합 + orphan 보호 (2026-06-08).

WS 가 체결 이벤트를 흘려 store 가 broker 와 어긋나는 두 방향을 REST 로 복구:
  - **phantom** (store 엔 있는데 broker 0): 청산 체결 유실 → store 를 0 으로
    정합. synthetic 이 없는 포지션을 닫으려다 ``22002 No position to close``
    무한루프 나던 것 멈춤 (2026-06-08 SNDKUSDT).
  - **orphan** (broker 엔 있는데 store 모름): 진입 체결 유실 → synthetic 이
    모르는 무보호 포지션. broker 의 entry/mark 로 ROE 평가해 TP/SL 넘으면
    직접 reduce-only 청산 (2026-06-08 BEATUSDT +17% 방치 사고).

WS 와 완전 독립 — REST 로 거래소 실포지션을 보므로 체결 유실·WS 끊김과 무관.

⚠️ **안전장치**: orphan 청산은 *봇이 주문한 종목*(``store.bot_ordered_symbols``)
만. 사용자 수동 포지션(ORDIUSDT 등 — order-context 없음)은 절대 안 건드린다.

⚠️ ROE = 가격변동% × leverage. ``take_profit_roi``/``stop_loss_roi`` 는 ROI
기준 (예 0.10 / 0.05 @10x = 가격 ±1%/0.5%). 코인가격 pct 아님.
"""
from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

from src.brokers import client_id as client_id_mod
from src.brokers.base import OrderRequest, OrderType, Side, TimeInForce

logger = logging.getLogger(__name__)


class OrphanGuard:
    def __init__(
        self,
        *,
        adapter: Any,
        position_store: Any,
        take_profit_roi: float = 0.10,
        stop_loss_roi: float = 0.05,
        default_leverage: float = 10.0,
        interval_sec: float = 20.0,
        dry_run: bool = False,
    ) -> None:
        self._adapter = adapter
        self._store = position_store
        self._tp_roe = Decimal(str(take_profit_roi))
        self._sl_roe = Decimal(str(stop_loss_roi))
        self._default_lev = Decimal(str(default_leverage))
        self._interval = interval_sec
        self._dry_run = dry_run

    async def run_loop(self, stop_event: asyncio.Event) -> None:
        logger.info(
            "OrphanGuard started (interval=%.0fs tp_roe=%s sl_roe=%s dry_run=%s)",
            self._interval, self._tp_roe, self._sl_roe, self._dry_run,
        )
        while not stop_event.is_set():
            try:
                await self.check_once()
            except Exception as exc:  # noqa: BLE001 — never kill the guard
                logger.warning("OrphanGuard: cycle error: %s", exc)
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except asyncio.TimeoutError:
                pass
        logger.info("OrphanGuard stopped")

    async def check_once(self) -> None:
        positions = await self._adapter.get_protective_positions()
        broker = {p["symbol"]: p for p in positions}

        # store 가 보유로 아는 symbol → [(sid, qty)]
        store_held: dict[str, list[tuple[str, Decimal]]] = {}
        for sid, poslist in self._store.all_positions().items():
            for sym, qty in poslist:
                if qty:
                    store_held.setdefault(sym, []).append((sid, Decimal(str(qty))))

        # 1) PHANTOM — store 엔 있는데 broker 엔 없음 → store 0 정합
        for sym, holders in list(store_held.items()):
            if sym not in broker:
                for sid, _q in holders:
                    self._store.force_sync_position(
                        strategy_id=sid, symbol=sym, qty=Decimal("0"),
                    )
                logger.warning(
                    "OrphanGuard: CLEAR-PHANTOM %s holders=%d → 0 (broker 없음)",
                    sym, len(holders),
                )

        # 2) ORPHAN — broker 엔 있는데 store 모름 → *봇 주문분만* 보호
        bot_syms = self._store.bot_ordered_symbols()
        for sym, p in broker.items():
            if sym in store_held:
                continue  # 귀속됨 → synthetic 담당
            if sym not in bot_syms:
                continue  # 사용자 수동분 (ORDI 등) → 절대 안 건드림
            await self._protect_orphan(sym, p)

    async def _protect_orphan(self, sym: str, p: dict) -> None:
        try:
            entry = Decimal(str(p["entry"]))
            mark = Decimal(str(p["mark"]))
            qty = Decimal(str(p["qty"]))  # signed
        except Exception:  # noqa: BLE001
            return
        lev = Decimal(str(p.get("leverage") or 0)) or self._default_lev
        if entry <= 0 or mark <= 0 or qty == 0:
            return
        if qty > 0:  # long: 이익 when mark>entry
            roe = (mark - entry) / entry * lev
        else:        # short: 이익 when mark<entry
            roe = (entry - mark) / entry * lev
        if not (roe >= self._tp_roe or roe <= -self._sl_roe):
            return
        kind = "TP" if roe >= self._tp_roe else "SL"
        logger.warning(
            "OrphanGuard: ORPHAN %s ROE=%.1f%% %s 돌파 → 청산 (qty=%s entry=%s mark=%s)",
            sym, float(roe) * 100, kind, qty, entry, mark,
        )
        if self._dry_run:
            return
        await self._close(sym, qty)

    async def _close(self, sym: str, signed_qty: Decimal) -> None:
        side = Side.BUY if signed_qty < 0 else Side.SELL
        cid = client_id_mod.generate(
            strategy="orphanguard", symbol=sym, side=str(side.value),
            ts_ms=int(time.time() * 1000),
        )
        req = OrderRequest(
            client_order_id=cid,
            symbol=sym,
            side=side,
            qty=abs(signed_qty),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
            reduce_only=True,
        )
        try:
            await self._adapter.place_order(req)
            logger.warning(
                "OrphanGuard: CLOSED orphan %s qty=%s side=%s",
                sym, abs(signed_qty), side.value,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("OrphanGuard: close failed %s: %s", sym, exc)
