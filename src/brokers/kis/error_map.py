from __future__ import annotations

from src.brokers.errors import (
    BrokerError,
    InsufficientFundsError,
    InvalidOrderError,
    RateLimitError,
    UnknownError,
)

# rt_cd="1" + msg_cd → BrokerError subclass
# 출처: hky035.github.io/web/kis-api-throttling/, KIS Developers 공식 포털
_MSG_CD_MAP: dict[str, type[BrokerError]] = {
    "EGW00201": RateLimitError,       # API 호출 유량 초과
    "APBK0013": InvalidOrderError,    # 주문 거부 (잔고부족, 장외시간, 호가단위 위반 등)
    "APBK0014": InsufficientFundsError,  # 잔고 부족
    "APBK0916": InvalidOrderError,    # 장외시간 주문
    "APBK1013": InvalidOrderError,    # 호가단위 위반
}


def map_error(msg_cd: str, msg1: str) -> BrokerError:
    """rt_cd='1' 응답을 BrokerError 하위 클래스로 변환."""
    cls = _MSG_CD_MAP.get(msg_cd, UnknownError)
    return cls(f"[{msg_cd}] {msg1}")
