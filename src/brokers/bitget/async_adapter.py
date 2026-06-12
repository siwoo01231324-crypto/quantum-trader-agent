"""Bitget USDT-M Futures async adapter implementing AsyncBrokerAdapter protocol.

REST-only (P1). WS user-data + market-data come in P2 (``async_ws.py`` /
``market_ws.py``). ``stream_fills()`` raises ``NotImplementedError`` for
parity with Binance's pre-WS Phase 1.

P1 limitations (documented for review):
  - One-way mode only (PositionSide.BOTH). Hedge mode (LONG/SHORT separate
    accounting) requires tradeSide=open/close — implemented but not exercised
    in this commit's CLI integration.
  - Bitget v2 expects ``size`` denominated in *base coin* units (e.g. BTC for
    BTCUSDT). Our OrderRequest.qty already uses base-coin units (consistent
    with Binance Futures). 1000PEPE-style multiplier (Binance ``1000PEPEUSDT``
    has no Bitget equivalent) is translated at the strategy-config layer, not
    here.
  - max-notional cooldown (Binance -2027 pattern) ported as-is using Bitget
    "40774" code.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.brokers import client_id as client_id_mod
from src.brokers.base import (
    AsyncBrokerAdapter,
    Balance,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
)
from src.brokers.bitget.async_http import (
    AsyncBitgetFuturesClient,
    DEMO_PRODUCT_TYPE,
    LIVE_PRODUCT_TYPE,
    REST_BASE_LIVE,
)
from src.brokers.bitget.async_ws import AsyncBitgetUserDataStream, OverflowPolicy
from src.brokers.bitget.symbol_filters import SymbolFilters
from src.brokers.errors import (
    BrokerClosedError,
    BrokerError,
    BrokerStartupError,
    InvalidOrderError,
)
from src.brokers.types import BrokerFill
from src.execution.base import Side
from src.observability.metrics import Metrics  # type: ignore  # noqa: F401  (parity)

log = logging.getLogger(__name__)


class AsyncBitgetFuturesAdapter:
    """AsyncBrokerAdapter implementation for Bitget USDT-M Futures.

    Demo trading is selected by ``paper=True`` (paptrading header + SUSDT-FUTURES).
    """

    name = "bitget_futures_async"

    def __init__(
        self,
        *,
        api_key: str,
        secret: str,
        passphrase: str,
        base_url: str = REST_BASE_LIVE,
        paper: bool = True,
        kill_switch: object | None = None,
        fill_queue_size: int = 1000,
        overflow_policy: "OverflowPolicy" = "block",
    ) -> None:
        self.paper = paper
        self._kill_switch = kill_switch
        self._closing = False
        # 2026-06-10 데이터안정화 P2 — 진입에 거래소 네이티브 preset TP/SL 이
        # *실제로 등록된* 종목 집합. synthetic SL(LivePositionRiskManager)이
        # 이 종목은 손 떼고(거래소가 라인에서 청산) preset 실패(40836/40832
        # naked)·청산된 종목만 백업하도록 — 노이즈성 조기청산(+0.83% 등) 차단.
        self._native_tpsl_symbols: set[str] = set()
        self._client = AsyncBitgetFuturesClient(
            api_key=api_key, secret=secret, passphrase=passphrase,
            base_url=base_url, paper=paper,
        )
        self._product_type = DEMO_PRODUCT_TYPE if paper else LIVE_PRODUCT_TYPE
        self._symbol_filters = SymbolFilters(base_url=base_url, paper=paper)
        self._hedge_mode: bool | None = None
        # #380 — ensure_leverage_target 캐시. symbol→이미 set 한 leverage.
        # 동일 (symbol, leverage) 재요청은 REST 생략 (발주마다 set-leverage 폭주 방지).
        self._leverage_forced: dict[str, int] = {}
        # Bitget -2027 equivalent: code 40762 (qty exceeds upper limit).
        # Local cooldown to prevent rate-limit cascade (mirrors Binance fix).
        self._max_notional_cooldown: dict[tuple[str, str], float] = {}
        self._MAX_NOTIONAL_COOLDOWN_SEC: float = 300.0
        # WS user-data stream — lazily built on first stream_fills().
        self._ws_stream: AsyncBitgetUserDataStream | None = None
        self._ws_creds = (api_key, secret, passphrase)
        self._ws_paper = paper
        self._fill_queue_size = fill_queue_size
        self._overflow_policy: "OverflowPolicy" = overflow_policy

    # ── kill switch ──────────────────────────────────────────────────────────

    def _assert_allow_order(self, emergency_exit: bool) -> None:
        if self._closing:
            raise BrokerClosedError("Adapter is closing; new orders are rejected.")
        if self._kill_switch is not None:
            self._kill_switch.assert_allow_order(liquidation=emergency_exit)

    # ── place_order ──────────────────────────────────────────────────────────

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self._assert_allow_order(req.emergency_exit)

        # Pre-flight cooldown — same (symbol, side) blocked 5min after a 40774.
        cooldown_key = (req.symbol, req.side.value)
        expiry = self._max_notional_cooldown.get(cooldown_key)
        if expiry is not None:
            now = time.monotonic()
            if now < expiry:
                log.info(
                    "bitget place_order skipped — max-notional cooldown %s %s remaining=%.0fs",
                    req.symbol, req.side.value, expiry - now,
                )
                return OrderAck(
                    broker_order_id="",
                    client_order_id=req.client_order_id,
                    symbol=req.symbol,
                    status="REJECTED",
                    ts=datetime.now(tz=timezone.utc),
                    qty=req.qty,
                    price=None,
                )
            else:
                self._max_notional_cooldown.pop(cooldown_key, None)

        # Quantize qty to Bitget's sizeMultiplier (LOT_SIZE-equivalent).
        req = self._quantize_qty(req)
        if req.order_type == OrderType.LIMIT and req.price is not None:
            req = self._quantize_price(req)

        # Side mapping: our enum value → Bitget literal.
        side_str = "buy" if req.side == Side.BUY else "sell"
        order_type_str = "market" if req.order_type == OrderType.MARKET else "limit"

        # Hedge mode tradeSide hint.
        trade_side: str | None = None
        if req.position_side != PositionSide.BOTH:
            # LONG enter = open, LONG exit = close (handled by caller intent).
            trade_side = "close" if req.reduce_only else "open"

        cid = self._normalize_cid(req.client_order_id, req)

        # 2026-06-08 — 진입 주문에 거래소 네이티브 TP/SL 가격 첨부 (BITGET_NATIVE_TPSL=1).
        # reduce_only(청산)엔 안 붙임. 트리거 가격은 거래소 tick 으로 양자화.
        _ptp = _psl = None
        if (
            os.environ.get("BITGET_NATIVE_TPSL", "0") == "1"
            and not req.reduce_only
        ):
            def _q(p):
                if p is None:
                    return None
                try:
                    return self._symbol_filters.quantize_price(req.symbol, p)
                except Exception:  # noqa: BLE001
                    return p
            _ptp = _q(req.preset_tp_price)
            _psl = _q(req.preset_sl_price)
            if _ptp is not None or _psl is not None:
                log.info(
                    "bitget preset TP/SL %s %s TP=%s SL=%s",
                    req.symbol, side_str, _ptp, _psl,
                )

        # P2 — 거래소 preset TP/SL 이 *실제 등록되는* 진입인가 (naked 면 False).
        _native_tpsl_active = (
            (_ptp is not None or _psl is not None) and not req.reduce_only
        )
        try:
            resp = await self._client.place_order(
                symbol=req.symbol,
                side=side_str,
                order_type=order_type_str,
                size=req.qty,
                price=req.price if req.order_type == OrderType.LIMIT else None,
                client_oid=cid,
                trade_side=trade_side,
                reduce_only=req.reduce_only,
                preset_tp_price=_ptp,
                preset_sl_price=_psl,
            )
        except BrokerError as exc:
            # 40762 = order qty exceeds upper limit (= Binance -2027 equivalent).
            # 40774 looks like a max-notional error by name but Bitget actually
            # uses it for "order type / position mode mismatch" — NOT cooldown.
            err_str = str(exc)
            if isinstance(exc, InvalidOrderError) and (
                "[40762]" in err_str
                or ("[40774]" in err_str and "exceeds" in err_str.lower())
            ):
                self._max_notional_cooldown[cooldown_key] = (
                    time.monotonic() + self._MAX_NOTIONAL_COOLDOWN_SEC
                )
                log.warning(
                    "bitget max_notional_cooldown registered %s %s for %.0fs",
                    req.symbol, req.side.value, self._MAX_NOTIONAL_COOLDOWN_SEC,
                )
                raise

            # 2026-06-09 — 거래소 preset TP/SL 가격 거부(40836 등): 진입가 대비
            # 시장이 움직여 SL/TP 가 즉시 트리거 조건이 되면 *주문 전체가 거부*돼
            # 진입을 통째로 잃는다 (consume 모드가 변동성 큰 소형주까지 잡으며
            # 빈발). 진입을 살리기 위해 preset 없이 1회 재시도 — 체결 후 synthetic
            # TP/SL (LivePositionRiskManager, 실 체결가 기준) 이 보호한다.
            preset_attached = _ptp is not None or _psl is not None
            if preset_attached and self._is_preset_price_error(err_str):
                log.warning(
                    "bitget preset TP/SL rejected (%s) — retrying %s %s "
                    "WITHOUT preset (synthetic TP/SL covers)",
                    err_str[:90], req.symbol, side_str,
                )
                resp = await self._client.place_order(
                    symbol=req.symbol,
                    side=side_str,
                    order_type=order_type_str,
                    size=req.qty,
                    price=req.price if req.order_type == OrderType.LIMIT else None,
                    client_oid=cid,
                    trade_side=trade_side,
                    reduce_only=req.reduce_only,
                    preset_tp_price=None,
                    preset_sl_price=None,
                )
                _native_tpsl_active = False  # naked 진입 → synthetic 이 보호
            else:
                raise

        # P2 — 거래소 preset TP/SL 활성 종목 추적 (synthetic stand-down).
        #   진입+preset 성공 → add (synthetic 손 뗌, 거래소가 라인 청산)
        #   naked(40836/40832) 또는 청산(reduce_only) → discard (synthetic 백업)
        if req.reduce_only or not _native_tpsl_active:
            self._native_tpsl_symbols.discard(req.symbol)
        else:
            self._native_tpsl_symbols.add(req.symbol)

        # Bitget place-order response is minimal — fetch detail for status.
        return OrderAck(
            broker_order_id=str(resp.orderId),
            client_order_id=str(resp.clientOid or cid),
            symbol=req.symbol,
            status="NEW",  # Bitget place response doesn't echo status; treat as NEW.
            ts=datetime.now(tz=timezone.utc),
            qty=req.qty,
            price=req.price if req.order_type == OrderType.LIMIT else None,
        )

    def has_native_tpsl(self, symbol: str) -> bool:
        """이 종목에 거래소 네이티브 preset TP/SL 이 활성 등록돼 있나 (P2).

        LivePositionRiskManager 가 evaluate 시 콜백으로 조회 — True 면 synthetic
        SL/TP 를 *발동 안 함*(거래소가 라인에서 청산). preset 실패(naked)·청산된
        종목은 False → synthetic 이 백업으로 보호.
        """
        return symbol in self._native_tpsl_symbols

    @staticmethod
    def _is_preset_price_error(err_str: str) -> bool:
        """주문 거부가 *preset TP/SL 가격* 검증 실패인가 (진입가 대비 시장 이동).

        확정: 40836 (숏 SL 가격이 현재가보다 커야 함). long/TP variant 도
        메시지 키워드로 포괄 — 코드 추측 없이 견고하게.
        """
        if "[40836]" in err_str:
            return True
        s = err_str.lower()
        mentions_preset = (
            "stop loss price" in s
            or "take profit price" in s
            or "stop surplus" in s
            or "preset" in s
        )
        relational = (
            "greater than" in s or "less than" in s or "should be" in s
        )
        return mentions_preset and relational

    def _normalize_cid(self, raw_cid: str, req: OrderRequest) -> str:
        # Bitget v2 클라이언트 OID 는 1~36자, 영숫자+_-. 패턴 외엔 fallback.
        if raw_cid and len(raw_cid) <= 36 and all(c.isalnum() or c in "_-" for c in raw_cid):
            return raw_cid
        return client_id_mod.generate(
            strategy="fallback",
            symbol=req.symbol,
            side=req.side.value,
            ts_ms=int(time.time() * 1000),
        )

    def _quantize_qty(self, req: OrderRequest) -> OrderRequest:
        from dataclasses import replace  # noqa: PLC0415
        try:
            step = self._symbol_filters.lot_step(req.symbol)
            min_qty = self._symbol_filters.min_qty(req.symbol)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bitget lot filter unavailable for %s (%s); submitting un-floored qty %s",
                req.symbol, exc, req.qty,
            )
            return req
        floored = (req.qty // step) * step
        floored = floored.quantize(step)
        if floored < min_qty:
            raise InvalidOrderError(
                f"{req.symbol}: qty {req.qty} floored to {floored} (step {step}) "
                f"< minQty {min_qty}; order dropped"
            )
        if floored == req.qty:
            return req
        log.info("bitget quantized %s qty %s → %s (step %s)",
                 req.symbol, req.qty, floored, step)
        return replace(req, qty=floored)

    def _quantize_price(self, req: OrderRequest) -> OrderRequest:
        from dataclasses import replace  # noqa: PLC0415
        if req.price is None:
            return req
        try:
            quantized = self._symbol_filters.quantize_price(req.symbol, req.price)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "bitget price filter unavailable for %s (%s); submitting un-quantized %s",
                req.symbol, exc, req.price,
            )
            return req
        if quantized == req.price:
            return req
        log.info("bitget quantized %s price %s → %s", req.symbol, req.price, quantized)
        return replace(req, price=quantized)

    # ── 거래소 네이티브 보호주문 (TP/SL plan order, 2026-06-08) ─────────────────
    # 진입 직후 거래소에 익절/손절을 직접 등록 → Bitget 매칭엔진이 서버측에서
    # 즉시 청산. synthetic(LivePositionRiskManager 가 mark-price 보다가 청산)보다
    # 빠르고(WS 지연 0) robust(봇 다운/WS 끊김에도 작동). ProtectiveBrokerProtocol
    # 의 async 변형 — bitget 어댑터가 전부 async 라 동기 ProtectiveOrderManager
    # 대신 wiring 에서 직접 await 한다.
    #
    # ⚠️ trigger price 는 *코인가격* 기준 (entry × (1 ± 가격pct)). ROI 가 아님 —
    # ROI = 가격변동% × leverage. stop_loss_pct=0.005(가격-0.5%)는 10x 에서
    # ROI -5%, take_profit_pct=0.01(가격+1%)는 ROI +10%. 호출자가 가격pct 를
    # 넘긴다 (전략 take_profit_pct/stop_loss_pct = 이미 가격pct).

    async def place_protective_order(
        self,
        *,
        symbol: str,
        side: str,           # close_side: "BUY"(숏 청산) | "SELL"(롱 청산)
        qty: Decimal,
        stop_price: Decimal,
        kind: str,           # "STOP_MARKET"(SL) | "TAKE_PROFIT_MARKET"(TP)
    ) -> str:
        # 2026-06-12 — **포지션 전체(whole-position) TPSL.** pos_profit(TP)/pos_loss
        # (SL) + size 생략 → 거래소가 전체 포지션을 라인에서 청산. partial(profit_plan
        # /loss_plan + size)은 50% 만 걸려 포지션이 늘어지던 사고의 정체(유저 거래소
        # 확인 + 데모 발동테스트로 확정). qty 는 사용 안 함(전체).
        plan_type = "pos_profit" if kind == "TAKE_PROFIT_MARKET" else "pos_loss"
        # close_side BUY = 숏 포지션 청산(보호대상=숏), SELL = 롱 청산(보호대상=롱).
        # 2026-06-11 데모 실측: Bitget v2 place-tpsl-order 의 holdSide 는 **포지션
        # 모드에 따라 값이 다르다.**
        #   one-way 모드: 포지션 *개시 방향* "buy"(롱) / "sell"(숏).
        #     ("short"/"long" → [43011] "holdSide error", "sell" → ACCEPTED 확인.
        #      "buy" 는 롱으로 해석돼 [40917] long-position 검증을 탐 → 매핑 확정.)
        #   hedge 모드: "long" / "short".
        # 우리 운영은 one-way(ensure_position_mode(hedge=False)) → _hedge_mode False/
        # None. None(미설정) 은 one-way default 로 간주.
        side_str = (side.value if hasattr(side, "value") else str(side)).upper()
        if bool(getattr(self, "_hedge_mode", None)):
            hold_side = "short" if side_str == "BUY" else "long"
        else:
            hold_side = "sell" if side_str == "BUY" else "buy"
        try:
            trigger = self._symbol_filters.quantize_price(symbol, stop_price)
        except Exception:  # noqa: BLE001 — 필터 없으면 un-quantized 제출
            trigger = stop_price
        cid = client_id_mod.generate(
            strategy="prot", symbol=symbol, side=plan_type,
            ts_ms=int(time.time() * 1000),
        )
        # 43023 settlement race (2026-06-11 라이브 관측): 진입 체결 WS 이벤트
        # 직후 거래소가 포지션을 아직 등록하기 전에 stop 을 걸면 [43023]
        # "Insufficient position, can not set profit or stop loss". 데모에선 진입
        # 후 2초 대기 시 정상 등록 → coordinator 의 즉시 호출이 레이스. 짧은
        # backoff 로 재시도(포지션 정착 대기). 마지막까지 실패면 raise → coordinator
        # 가 로그+synthetic 백업 (기존 동작).
        try:
            order_id = await self._place_tpsl_with_settle_retry(
                symbol=symbol, plan_type=plan_type, trigger=trigger,
                hold_side=hold_side, qty=qty, cid=cid,
            )
        except Exception as err:  # noqa: BLE001
            # 45122/40836/40832 = SL/TP 가격이 현재가(mark)를 *이미 지나감*(진입 후
            # 가격이 그새 SL/TP 트리거를 통과). 즉시청산 대신 **mark 바로 너머로
            # trigger 재계산해 1회 재배치** (B 옵션: server-side stop 유지 → 반등 시
            # TP 기회도 남김, naked 방지). 에러 문구에 mark·방향(>/<)이 들어있어
            # 파싱. price-past-mark 코드 아니거나 파싱 실패면 그대로 raise(기존 동작).
            adj = self._adjust_trigger_past_mark(str(err))
            if adj is None:
                raise
            try:
                trigger = self._symbol_filters.quantize_price(symbol, adj)
            except Exception:  # noqa: BLE001
                trigger = adj
            cid = client_id_mod.generate(
                strategy="prot", symbol=symbol, side=plan_type,
                ts_ms=int(time.time() * 1000),
            )
            order_id = await self._client.place_tpsl_order(
                symbol=symbol, plan_type=plan_type, trigger_price=trigger,
                hold_side=hold_side, size=None, client_oid=cid,
            )
            log.info(
                "bitget protective price-past-mark 재배치 %s %s → trigger=%s (mark 너머) oid=%s",
                symbol, plan_type, trigger, order_id,
            )
        log.info(
            "bitget protective placed %s %s trigger=%s holdSide=%s size=%s oid=%s",
            symbol, plan_type, trigger, hold_side, qty, order_id,
        )
        return order_id

    # 45122/40836/40832 = TPSL 가격이 현재가(mark)를 지나감. mark 너머 0.15% 에 재배치.
    _PRICE_PAST_MARK_CODES: tuple[str, ...] = ("45122", "40836", "40832")
    _MARK_BUFFER = Decimal("0.0015")

    @classmethod
    def _adjust_trigger_past_mark(cls, err_str: str) -> "Decimal | None":
        """price-validation 에러(45122 등)에서 mark·방향 파싱 → 유효 trigger 반환.

        에러 예: ``Short position stop loss price please > mark price 31.915``
          ``>`` = trigger 가 mark 보다 *커야* 함 → ``mark×(1+buf)``
          ``<`` = trigger 가 mark 보다 *작아야* 함 → ``mark×(1−buf)``
        해당 코드/패턴 아니면 None (호출자가 raise).
        """
        if not any(c in err_str for c in cls._PRICE_PAST_MARK_CODES):
            return None
        import re
        m = re.search(r"([<>])\s*(?:mark\s*price\s*)?([0-9]+\.?[0-9]*)", err_str)
        if not m:
            return None
        try:
            mark = Decimal(m.group(2))
        except Exception:  # noqa: BLE001
            return None
        if not (mark > 0):
            return None
        return (mark * (Decimal("1") + cls._MARK_BUFFER) if m.group(1) == ">"
                else mark * (Decimal("1") - cls._MARK_BUFFER))

    # 43023 = 포지션 미정착(체결 직후 레이스). backoff 누적 ~4s (0.4+0.8+1.2+1.6).
    _PROTECTIVE_SETTLE_BACKOFF: tuple[float, ...] = (0.4, 0.8, 1.2, 1.6)

    async def _place_tpsl_with_settle_retry(
        self, *, symbol: str, plan_type: str, trigger, hold_side: str, qty, cid: str,
    ) -> str:
        """place_tpsl_order — [43023] Insufficient position 이면 backoff 재시도.

        43023 외 에러는 즉시 raise(잘못된 가격/방향 등은 재시도 무의미). client_oid
        는 재시도마다 새로 발급(중복 제출 방지)."""
        last_err: Exception | None = None
        attempts = len(self._PROTECTIVE_SETTLE_BACKOFF) + 1
        for i in range(attempts):
            try:
                return await self._client.place_tpsl_order(
                    symbol=symbol, plan_type=plan_type, trigger_price=trigger,
                    hold_side=hold_side, size=None, client_oid=cid,  # None=포지션 전체
                )
            except Exception as err:  # noqa: BLE001 — 43023 만 재시도
                if "43023" not in str(err):
                    raise
                last_err = err
                if i < len(self._PROTECTIVE_SETTLE_BACKOFF):
                    delay = self._PROTECTIVE_SETTLE_BACKOFF[i]
                    log.info(
                        "bitget protective 43023 (포지션 미정착) %s %s — %.1fs 후 재시도 (%d/%d)",
                        symbol, plan_type, delay, i + 1, len(self._PROTECTIVE_SETTLE_BACKOFF),
                    )
                    await asyncio.sleep(delay)
                    cid = client_id_mod.generate(
                        strategy="prot", symbol=symbol, side=plan_type,
                        ts_ms=int(time.time() * 1000),
                    )
        assert last_err is not None
        raise last_err

    async def cancel_protective_order(
        self,
        *,
        symbol: str,
        broker_order_id: str,
    ) -> None:
        await self._client.cancel_tpsl_order(
            symbol=symbol, order_id=broker_order_id,
        )

    async def list_open_protective_orders(
        self,
        *,
        symbol: str | None = None,
    ) -> list[dict]:
        return await self._client.get_pending_tpsl_orders(symbol=symbol)

    # ── cancel / get_order ───────────────────────────────────────────────────

    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        await self._client.cancel_order(
            symbol=symbol,
            order_id=broker_order_id,
            client_oid=client_order_id,
        )

    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        resp = await self._client.get_order_detail(
            symbol=symbol,
            order_id=broker_order_id,
            client_oid=client_order_id,
        )
        # Bitget status: live / partially_filled / filled / canceled.
        status_map = {
            "live": "NEW",
            "new": "NEW",
            "partially_filled": "PARTIALLY_FILLED",
            "filled": "FILLED",
            "canceled": "CANCELED",
            "cancelled": "CANCELED",
        }
        ts = datetime.fromtimestamp(resp.utime / 1000, tz=timezone.utc) if resp.utime else datetime.now(tz=timezone.utc)
        return OrderAck(
            broker_order_id=resp.orderId,
            client_order_id=resp.clientOid,
            symbol=resp.symbol,
            status=status_map.get(resp.status.lower(), resp.status.upper()),
            ts=ts,
            qty=resp.size,
            price=resp.priceAvg if resp.priceAvg else resp.price,
            filled_qty=resp.filledSize,
        )

    # ── positions / balance ──────────────────────────────────────────────────

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        if symbol is not None:
            raw = await self._client.get_single_position(symbol=symbol)
        else:
            raw = await self._client.get_all_positions()
        positions: list[Position] = []
        for p in raw:
            if p.total == Decimal("0"):
                continue
            # Bitget holdSide → PositionSide.
            side = PositionSide.LONG if p.holdSide == "long" else PositionSide.SHORT
            positions.append(
                Position(
                    symbol=p.symbol,
                    side=side,
                    qty=p.total,
                    entry_price=p.averageOpenPrice,
                    liquidation_price=p.liquidationPrice,
                )
            )
        return positions

    async def get_protective_positions(self) -> list[dict]:
        """orphan/phantom 가드용 — 거래소 실포지션 상세 (entry/mark/leverage).

        broker truth 기반 TP/SL 평가에 쓴다. WS 가 체결을 흘려 store 가
        어긋나도(orphan), 거래소엔 멀쩡히 있는 포지션을 이걸로 직접 본다.
        반환: [{symbol, hold_side, qty(signed), entry, mark, leverage, upl}]
        """
        raw = await self._client.get_all_positions()
        out: list[dict] = []
        for p in raw:
            if p.total == Decimal("0"):
                continue
            signed = p.total if p.holdSide == "long" else -p.total
            out.append({
                "symbol": p.symbol,
                "hold_side": p.holdSide,          # "long" | "short"
                "qty": signed,                    # +long / -short
                "entry": p.averageOpenPrice,
                "mark": p.markPrice,
                "leverage": Decimal(str(p.leverage or 1)),
                "upl": p.unrealizedPL,
            })
        return out

    async def get_net_positions(self) -> dict[str, Decimal]:
        """SIGNED net qty per symbol — mirrors Binance adapter.

        Bitget v2 의 PositionResponse 는 ``total`` (abs qty) + ``holdSide``
        (long/short). PositionReconciler 가 ground-truth 비교에 sign 필요해서
        long = +total, short = -total. 0 인 symbol 은 제외 — caller 는
        absent 면 net 0 으로 간주.
        """
        raw = await self._client.get_all_positions()
        out: dict[str, Decimal] = {}
        for p in raw:
            if p.total == Decimal("0"):
                continue
            signed = p.total if p.holdSide == "long" else -p.total
            out[p.symbol] = out.get(p.symbol, Decimal("0")) + signed
        return out

    async def get_balance(self) -> list[Balance]:
        # USDT margin coin only (matches whitelist + production.yaml).
        # MVP: query BTCUSDT as proxy symbol for marginCoin=USDT account.
        try:
            acc = await self._client.get_account(symbol="BTCUSDT", margin_coin="USDT")
        except Exception as exc:  # noqa: BLE001
            log.warning("bitget get_balance failed: %s", exc)
            return []
        return [Balance(asset=acc.marginCoin, free=acc.available, locked=acc.locked)]

    # ── ensure_* ─────────────────────────────────────────────────────────────

    async def ensure_leverage(self, symbol: str, leverage: int) -> None:
        try:
            await self._client.set_leverage(symbol=symbol, leverage=leverage)
        except InvalidOrderError as exc:
            log.warning("bitget set_leverage failed sym=%s lev=%d: %s", symbol, leverage, exc)
            raise

    async def ensure_leverage_minimum(
        self, symbol: str, fallback_leverage: int = 1,
    ) -> None:
        """Mirror Binance helper — Bitget 도 종목 leverage 미설정 시 처음 발주가
        실패할 수 있어 first-trade 직전 안전망. 기본은 1x.
        """
        try:
            poss = await self._client.get_single_position(symbol=symbol)
            if poss and poss[0].leverage > 0:
                return
        except Exception:  # noqa: BLE001
            pass
        await self.ensure_leverage(symbol, fallback_leverage)

    async def ensure_leverage_target(self, symbol: str, leverage: int) -> None:
        """leverage 를 *강제로* ``leverage`` 로 설정 (종목당 1회, 캐시).

        ``ensure_leverage_minimum`` 과 차이: minimum 은 "미설정이면 1x" 라
        브로커 UI/이전값을 override 하지 않는다. 본 메서드는 config 가
        leverage 를 강제하도록 *현재값과 무관하게* set 한다 (#380 — 데모 UI
        수동 10x 설정 불가 케이스. 코드가 leverage 의 truth source).

        open position 이 있으면 거래소가 set-leverage 를 거부할 수 있으나
        (cross margin 은 보통 허용) 예외는 삼켜 발주는 진행한다 — leverage 는
        다음 청산 후 진입부터 반영. 캐시는 *성공 시에만* 채워 재시도 가능.
        """
        if leverage <= 0:
            return
        if self._leverage_forced.get(symbol) == leverage:
            return
        try:
            await self.ensure_leverage(symbol, leverage)
            self._leverage_forced[symbol] = leverage
        except Exception as exc:  # noqa: BLE001 — leverage 못 set 해도 발주 시도
            log.warning(
                "bitget ensure_leverage_target failed sym=%s lev=%d: %s — proceed",
                symbol, leverage, exc,
            )

    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        bitget_mode = "crossed" if mode == MarginType.CROSSED else "isolated"
        await self._client.set_margin_mode(symbol=symbol, mode=bitget_mode)

    async def ensure_position_mode(self, *, hedge: bool) -> None:
        if self._hedge_mode is not None and self._hedge_mode == hedge:
            return
        try:
            await self._client.set_position_mode(hedge=hedge)
            self._hedge_mode = hedge
        except Exception as exc:  # noqa: BLE001
            log.warning("bitget ensure_position_mode failed: %s", exc)
            raise BrokerStartupError(
                f"Bitget position mode set failed (target hedge={hedge}): {exc}"
            ) from exc

    # ── health / lifecycle ───────────────────────────────────────────────────

    async def health_check(self) -> HealthStatus:
        try:
            await self._client.ping()
            return HealthStatus.OK
        except Exception:  # noqa: BLE001
            return HealthStatus.DOWN

    def stream_fills(self) -> AsyncIterator[BrokerFill]:
        """Return an AsyncIterator that yields BrokerFill from Bitget private WS."""
        key, sec, pas = self._ws_creds
        self._ws_stream = AsyncBitgetUserDataStream(
            api_key=key, secret=sec, passphrase=pas,
            paper=self._ws_paper,
            queue_size=self._fill_queue_size,
            overflow_policy=self._overflow_policy,
        )
        return self._ws_stream.stream_fills()

    async def aclose(self) -> None:
        self._closing = True
        if self._ws_stream is not None:
            await self._ws_stream.aclose()
        await self._client.aclose()
