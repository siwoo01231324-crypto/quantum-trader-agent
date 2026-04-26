"""KIS AsyncBrokerAdapter 구현.

AsyncBrokerAdapter Protocol 준수.
sync adapter (adapter.py) 수정 금지 — 이 파일이 async 표면.
KIS 현물 특이 정책 (reduce_only+BUY raise, position_side 경고 등) sync adapter 와 동일.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import AsyncIterator

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
from src.brokers.errors import BrokerClosedError, UnsupportedOperationError
from src.brokers.kis.async_http import KISAsyncClient
from src.brokers.kis.async_ws import KISAsyncWebSocket
from src.brokers.kis.auth import KISAuth
from src.brokers.types import BrokerFill
from src.execution.base import Side

log = logging.getLogger(__name__)


def _parse_credit_number(credit_number: str) -> tuple[str, str]:
    import re
    pattern = re.compile(r"^([0-9]{8})-([0-9]{2})$")
    m = pattern.match(credit_number)
    if not m:
        from src.brokers.errors import ConfigurationError
        raise ConfigurationError(
            f"HANTOO_CREDIT_NUMBER='{credit_number}' 포맷 오류. "
            "포맷: ^[0-9]{8}-[0-9]{2}$ (예: '12345678-01')"
        )
    return m.group(1), m.group(2)


class KISAsyncAdapter:
    """KIS AsyncBrokerAdapter 구현 (httpx + websockets 기반).

    Protocol: AsyncBrokerAdapter
    KIS 현물 특성:
    - position_side / reduce_only / close_position 개념 없음
    - ensure_leverage / ensure_margin_type / ensure_position_mode → no-op
    """

    name = "kis"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        hts_id: str,
        credit_number: str = "12345678-01",
        paper: bool = True,
        kill_switch=None,
    ) -> None:
        self.paper = paper
        self._kill_switch = kill_switch
        self._hts_id = hts_id

        cano, acnt_prdt_cd = _parse_credit_number(credit_number)

        self._auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=paper)
        self._client = KISAsyncClient(
            auth=self._auth,
            app_key=app_key,
            app_secret=app_secret,
            cano=cano,
            acnt_prdt_cd=acnt_prdt_cd,
            paper=paper,
        )
        self._ws = KISAsyncWebSocket(
            auth=self._auth,
            app_key=app_key,
            hts_id=hts_id,
            paper=paper,
        )
        self._closing = False

    # ------------------------------------------------------------------
    # AsyncBrokerAdapter Protocol
    # ------------------------------------------------------------------

    async def place_order(self, req: OrderRequest) -> OrderAck:
        if self._closing:
            raise BrokerClosedError("KISAsyncAdapter is closing; new orders rejected")
        if self._kill_switch is not None:
            self._kill_switch.assert_allow_order(liquidation=req.emergency_exit)

        if req.reduce_only and req.side == Side.BUY:
            raise UnsupportedOperationError(
                "KIS 현물: reduce_only=True + side=BUY 는 지원하지 않습니다."
            )
        if req.reduce_only and req.side == Side.SELL:
            log.debug("KIS: reduce_only=True + SELL — 허용 (매도=reduce 암묵적)")

        if req.position_side != PositionSide.BOTH:
            log.warning(
                "KIS: position_side=%s 는 무시됩니다 (현물은 BOTH only)",
                req.position_side,
            )

        if req.close_position:
            log.warning("KIS: close_position=True 는 무시됩니다 (전량 청산 개념 없음)")

        resp = await self._client.place_order(
            symbol=req.symbol,
            side=req.side,
            order_type=req.order_type,
            qty=req.qty,
            price=req.price,
        )

        output = resp.output
        return OrderAck(
            broker_order_id=output.ODNO if output else "",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="NEW",
            ts=datetime.now(tz=timezone.utc),
            qty=req.qty,
            price=req.price,
        )

    async def cancel_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> None:
        if broker_order_id is None:
            raise UnsupportedOperationError(
                "KIS: cancel_order 는 broker_order_id 필수 (client_order_id 취소 미지원)"
            )
        await self._client.cancel_order(
            broker_order_id=broker_order_id,
            symbol=symbol,
            qty=0,
            price=0,
        )

    async def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        raise UnsupportedOperationError(
            "KIS: get_order (주문 상태 조회) 는 본 이슈 범위 외 — 후행 이슈에서 구현"
        )

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        resp = await self._client.get_balance()
        positions = []
        for stock in resp.output1:
            if symbol and stock.PDNO != symbol:
                continue
            qty = stock.qty
            if qty == Decimal("0"):
                continue
            positions.append(
                Position(
                    symbol=stock.PDNO,
                    side=PositionSide.BOTH,
                    qty=qty,
                    entry_price=stock.avg_price,
                )
            )
        return positions

    async def get_balance(self) -> list[Balance]:
        resp = await self._client.get_balance()
        balances = []
        for summary in resp.output2:
            # KIS pydantic schema deserializes to lowercase field names (dnca_tot_amt).
            # Accept both cases for forward-compat with raw dict consumers.
            d = dict(summary)
            dnca_tot_amt = d.get("dnca_tot_amt", d.get("DNCA_TOT_AMT", "0")) or "0"
            balances.append(
                Balance(
                    asset="KRW",
                    free=Decimal(str(dnca_tot_amt)),
                    locked=Decimal("0"),
                )
            )
        return balances

    def stream_fills(self) -> AsyncIterator[BrokerFill]:
        return self._ws.stream_fills()

    # ------------------------------------------------------------------
    # No-op (KIS 현물: 레버리지/마진/포지션 모드 없음)
    # ------------------------------------------------------------------

    async def ensure_leverage(self, symbol: str, leverage: int) -> None:
        log.debug("KIS: ensure_leverage no-op (현물)")

    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        log.debug("KIS: ensure_margin_type no-op (현물)")

    async def ensure_position_mode(self, *, hedge: bool) -> None:
        log.debug("KIS: ensure_position_mode no-op (현물)")

    async def health_check(self) -> HealthStatus:
        try:
            await self._auth.get_token_async()
            return HealthStatus.OK
        except Exception as exc:
            log.warning("KIS health_check failed: %s", exc)
            return HealthStatus.DOWN

    # ------------------------------------------------------------------
    # aclose (5단계, KIS: step 3 = no-op)
    # ------------------------------------------------------------------

    async def aclose(self) -> None:
        if self._closing:
            return
        # step 1: 신규 주문 차단
        self._closing = True
        # step 2: WS close (KISAsyncWebSocket 내부에서 closing=True → generator 종료)
        await self._ws.aclose()
        # step 3: KIS 는 listenKey keepalive task 없음 — skip
        # step 4: inflight REST CancelledError 전파 (httpx client aclose 전 대기)
        # (httpx AsyncClient 는 자체적으로 진행 중인 요청을 drain 하지 않으므로
        #  adapter 레벨에서 inflight tracking 없이 step 5 로 바로 진행)
        # step 5: httpx aclose
        await self._client.aclose()
