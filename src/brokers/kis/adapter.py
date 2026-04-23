from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from src.brokers.base import (
    Balance,
    BrokerAdapter,
    Closeable,
    HealthStatus,
    MarginType,
    OrderAck,
    OrderRequest,
    OrderType,
    Position,
    PositionSide,
)
from src.brokers.errors import ConfigurationError, UnsupportedOperationError
from src.brokers.kis.auth import KISAuth
from src.brokers.kis.krx_ticks import quantize_price_krx
from src.brokers.kis.rest import KISClient
from src.brokers.types import BrokerFill
from src.execution.base import Side

log = logging.getLogger(__name__)

_CREDIT_NUMBER_HELP = (
    "HANTOO_CREDIT_NUMBER 포맷: ^[0-9]{8}-[0-9]{2}$ (예: '12345678-01')"
)


def _parse_credit_number(credit_number: str) -> tuple[str, str]:
    import re
    pattern = re.compile(r"^([0-9]{8})-([0-9]{2})$")
    m = pattern.match(credit_number)
    if not m:
        raise ConfigurationError(
            f"HANTOO_CREDIT_NUMBER='{credit_number}' 포맷 오류. {_CREDIT_NUMBER_HELP}"
        )
    return m.group(1), m.group(2)


class _NoopCloseable:
    def close(self) -> None:
        pass


class KISAdapter:
    """KIS(한국투자증권) BrokerAdapter 구현.

    KIS 현물 특성:
    - position_side / reduce_only / close_position 은 개념 없음
    - ensure_leverage / ensure_margin_type / ensure_position_mode → no-op
    """

    name = "kis"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        credit_number: str = "12345678-01",
        paper: bool = True,
        kill_switch=None,
    ) -> None:
        self.paper = paper
        self._kill_switch = kill_switch

        cano, acnt_prdt_cd = _parse_credit_number(credit_number)

        self._auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=paper)
        self._client = KISClient(
            auth=self._auth,
            app_key=app_key,
            app_secret=app_secret,
            cano=cano,
            acnt_prdt_cd=acnt_prdt_cd,
            paper=paper,
        )

    # ------------------------------------------------------------------
    # BrokerAdapter Protocol
    # ------------------------------------------------------------------

    def place_order(self, req: OrderRequest) -> OrderAck:
        if self._kill_switch is not None:
            self._kill_switch.assert_allow_order(liquidation=req.emergency_exit)

        # position_side / reduce_only / close_position 정책 (플랜 I-Imp-5)
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

        resp = self._client.place_order(
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

    def cancel_order(
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
        self._client.cancel_order(
            broker_order_id=broker_order_id,
            symbol=symbol,
            qty=0,
            price=0,
        )

    def get_order(
        self,
        *,
        broker_order_id: str | None = None,
        client_order_id: str | None = None,
        symbol: str,
    ) -> OrderAck:
        raise UnsupportedOperationError(
            "KIS: get_order (주문 상태 조회) 는 본 이슈 범위 외 — 후행 이슈에서 구현"
        )

    def get_positions(self, symbol: str | None = None) -> list[Position]:
        resp = self._client.get_balance()
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

    def get_balance(self) -> list[Balance]:
        resp = self._client.get_balance()
        balances = []
        for summary in resp.output2:
            dnca_tot_amt = summary.get("DNCA_TOT_AMT", "0")
            balances.append(
                Balance(
                    asset="KRW",
                    free=Decimal(str(dnca_tot_amt)),
                    locked=Decimal("0"),
                )
            )
        return balances

    def stream_fills(self, on_fill: Callable[[BrokerFill], None]) -> Closeable:
        log.warning(
            "KIS: stream_fills 는 Task #6 (KIS WS) 에서 구현됩니다. no-op 반환."
        )
        return _NoopCloseable()

    # ------------------------------------------------------------------
    # No-op methods (KIS 현물은 레버리지/마진/포지션 모드 없음)
    # ------------------------------------------------------------------

    def ensure_leverage(self, symbol: str, leverage: int) -> None:
        log.debug("KIS: ensure_leverage no-op (현물)")

    def ensure_margin_type(self, symbol: str, mode: MarginType) -> None:
        log.debug("KIS: ensure_margin_type no-op (현물)")

    def ensure_position_mode(self, *, hedge: bool) -> None:
        log.debug("KIS: ensure_position_mode no-op (현물)")

    def health_check(self) -> HealthStatus:
        try:
            self._auth.get_token()
            return HealthStatus.OK
        except Exception as exc:
            log.warning("KIS health_check failed: %s", exc)
            return HealthStatus.DOWN
