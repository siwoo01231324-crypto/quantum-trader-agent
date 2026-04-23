from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

# KRX 호가단위 테이블 (2025년 KRX 매매거래제도 기준)
# 출처: KRX 공식 PDF "유가증권시장 업무규정 시행세칙" / KRX 시장정보시스템
#
# 코스피(KOSPI): 가격대별 호가단위
#   1원 미만       → 미해당 (상장주식 최소 1원)
#   1,000원 미만   → 1원
#   5,000원 미만   → 5원
#   10,000원 미만  → 10원
#   50,000원 미만  → 50원
#   100,000원 미만 → 100원
#   500,000원 미만 → 500원
#   500,000원 이상 → 1,000원
#
# 코스닥(KOSDAQ): 가격대별 호가단위
#   1,000원 미만   → 1원
#   5,000원 미만   → 5원
#   10,000원 미만  → 10원
#   50,000원 미만  → 50원
#   100,000원 미만 → 100원
#   (코스닥은 500,000원 이상 구간 없음 — 100원 단위 유지)
#
# ETF: 5원 단위 (NAV 기반 단일 단위)

_KOSPI_TICKS: list[tuple[Decimal, Decimal]] = [
    (Decimal("1000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("10000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("100000"), Decimal("100")),
    (Decimal("500000"), Decimal("500")),
    (Decimal("999999999"), Decimal("1000")),
]

_KOSDAQ_TICKS: list[tuple[Decimal, Decimal]] = [
    (Decimal("1000"), Decimal("1")),
    (Decimal("5000"), Decimal("5")),
    (Decimal("10000"), Decimal("10")),
    (Decimal("50000"), Decimal("50")),
    (Decimal("999999999"), Decimal("100")),
]

_ETF_TICK = Decimal("5")


def _tick_size(price: Decimal, ticks: list[tuple[Decimal, Decimal]]) -> Decimal:
    for upper_bound, tick in ticks:
        if price < upper_bound:
            return tick
    return ticks[-1][1]


def quantize_price_krx(
    price: Decimal,
    market: str = "KOSPI",
) -> Decimal:
    """KRX 호가단위에 맞게 가격을 내림(ROUND_DOWN) 처리.

    Args:
        price: 원본 가격 (Decimal)
        market: "KOSPI" | "KOSDAQ" | "ETF"

    Returns:
        호가단위에 맞춰 내림한 Decimal 가격
    """
    market = market.upper()
    if market == "ETF":
        tick = _ETF_TICK
    elif market == "KOSDAQ":
        tick = _tick_size(price, _KOSDAQ_TICKS)
    else:
        tick = _tick_size(price, _KOSPI_TICKS)

    return (price // tick) * tick
