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
        volatility_provider: "Callable[[str], float | None] | None" = None,
        sl_slip_factor: float = 0.0,
        sl_slip_cap_pct: float = 0.003,
    ) -> None:
        self._adapter = adapter
        self._store = position_store
        self._policy_lookup = policy_lookup
        # symbol -> {"sl": order_id, "tp": order_id} — whole-position TPSL 종목당 1세트.
        self._registered: dict[str, dict[str, str]] = {}
        # 2026-06-13 변동성 보정 손절 — thin/변동성 큰 종목은 빠른 무빙에 시장가
        # 손절이 라인보다 밀려 *실현손실*이 −0.5% → −0.8% 로 커진다(손익비 파괴,
        # LWLG −8.75% 등). 슬리피지 ∝ 변동성이므로, 변동성을 대리지표로 손절선을
        # 그만큼 *당겨* 밀려도 목표(−0.5%)에 안착시킨다. buffer = vol×factor (cap 적용).
        # factor=0 이면 비활성(기존 동작 byte-identical). 측정 막혀(청산 fill 미귀속)
        # factor 는 라이브 관찰로 튜닝 — env AIRBORNE_SL_SLIP_FACTOR.
        self._vol_provider = volatility_provider
        self._sl_slip_factor = float(sl_slip_factor)
        self._sl_slip_cap_pct = float(sl_slip_cap_pct)

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
        net = self._net_qty(strategy_id, symbol)

        if net == 0:
            # 이 전략 net 0 — 단, whole-position TPSL 은 *종목 net 전체*를 보호하므로
            # 같은 종목을 다른 전략이 아직 보유 중이면 취소하면 안 된다(이중진입).
            # broker net 이 실제 0 일 때만 취소(불확실/조회실패 시 유지 — 보호 우선).
            if await self._symbol_broker_net_zero(symbol):
                await self._cancel(symbol)
            return

        # 2026-06-12 — 종목당 whole-position TPSL **1세트만** 등록(이중등록 제거).
        # 단, _registered 메모리 플래그는 포지션이 청산돼도(특히 청산 fill 이
        # "cannot resolve" orphan 이거나 reconciler phantom 정리로 닫힌 경우) 안
        # 지워질 수 있다 → 재진입 시 "이미 등록됨"으로 skip → **naked 회귀**
        # (PAXG/XAUT 2026-06-12). 따라서 메모리 플래그가 있어도 **거래소에 실제
        # active TP/SL 이 있을 때만** skip, 없으면 stale 로 보고 정리 후 재등록.
        if symbol in self._registered:
            if await self._has_active_tpsl(symbol):
                return                              # 진짜 보호 중 → skip
            self._registered.pop(symbol, None)      # stale(청산됨) → 재등록 진행

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
        # 변동성 보정 — 손절선을 buffer 만큼 *당겨* 슬리피지 후 실현손실이 목표에
        # 안착. TP 는 resting limit 이라 슬립 없음 → 안 건드림.
        sl_pct_eff = self._slip_adjusted_sl_pct(symbol, sl_pct)
        cfg = ProtectiveOrderConfig(
            stop_loss_pct=Decimal(str(sl_pct_eff)),
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
        self._registered[symbol] = {"sl": sl_id, "tp": tp_id}
        logger.info(
            "protective_coordinator: registered(whole-position) sid=%s %s side=%s "
            "entry=%s SL=%s TP=%s",
            strategy_id, symbol, entry_side, entry_price, sl_price, tp_price,
        )

    async def _cancel(self, symbol: str) -> None:
        ids = self._registered.pop(symbol, None)
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
        logger.info("protective_coordinator: cancelled %s", symbol)

    async def _symbol_broker_net_zero(self, symbol: str) -> bool:
        """broker 의 *실제* net 포지션이 0 인가 — whole-position TPSL 취소 판정.

        조회 실패/예외 시 False(취소 안 함) — 열린 포지션의 보호를 실수로 제거하는
        것보다 stale 주문(트리거 시 22002 무해)이 안전하다. adapter 에
        get_net_positions 없으면(테스트 fake 등) 보수적으로 False.
        """
        getter = getattr(self._adapter, "get_net_positions", None)
        if getter is None:
            return False
        try:
            nets = await getter()
            q = nets.get(symbol, 0) if isinstance(nets, dict) else 0
            return Decimal(str(q)) == 0
        except Exception:  # noqa: BLE001
            return False

    async def _has_active_tpsl(self, symbol: str) -> bool:
        """거래소에 이 종목의 active TP/SL plan order 가 실제로 있나 (stale 판정).

        Bitget pos-TPSL 은 포지션 청산 시 자동 취소되므로, 청산된 종목은 빈 리스트
        → ``_registered`` 가 stale 임을 의미. 조회 실패/미지원 시 **False**(= stale 로
        간주해 재등록) — naked 보다 중복등록(benign: 먼저 발동한 게 청산, 나머지
        auto-cancel)이 안전하다.
        """
        lister = getattr(self._adapter, "list_open_protective_orders", None)
        if lister is None:
            return False
        try:
            rows = await lister(symbol=symbol)
            return bool(rows)
        except Exception:  # noqa: BLE001
            return False

    def _slip_adjusted_sl_pct(self, symbol: str, sl_pct: float) -> float:
        """변동성 기반 슬리피지 보정 — 손절선을 buffer 만큼 *당긴* sl_pct 반환.

        buffer = vol(symbol) × factor, cap 적용. factor=0 또는 vol 미가용/예외면
        원래 sl_pct (기존 동작 byte-identical). 과당김 방지로 sl_pct 의 절반까지만.
        예: sl_pct=0.005, vol=0.02(2% range), factor=0.15 → buffer=0.003 → eff
        sl_pct=0.002 (손절선 −0.2%, 0.3% 밀려도 실현 ≈ −0.5%).
        """
        if self._sl_slip_factor <= 0 or self._vol_provider is None:
            return sl_pct
        try:
            vol = self._vol_provider(symbol)
        except Exception:  # noqa: BLE001
            return sl_pct
        if not vol or vol <= 0:
            return sl_pct
        buffer = min(float(vol) * self._sl_slip_factor, self._sl_slip_cap_pct)
        return max(sl_pct - buffer, sl_pct * 0.5)

    def _net_qty(self, sid: str, symbol: str) -> Decimal:
        try:
            for sym, qty in self._store.get_positions(sid):
                if sym == symbol:
                    return Decimal(str(qty))
        except Exception:  # noqa: BLE001
            pass
        return Decimal("0")
