"""Convert universe-scan target weights to OrderIntent list (#218 Phase 2).

Pure function: target weights + current positions + prices → list[OrderIntent].
broker 어댑터 없이 unit test 가능. universe-scan AsyncStrategy 가 매주 리밸 시
weights 를 갱신하면 본 모듈이 그 weights 와 현 포지션의 차이를 종목별 매수/매도
주문으로 변환한다.

핵심 책임:
1. target_qty 산출 = (target_weight × capital) / price
2. diff_qty = target_qty - current_qty
3. lot_size 반올림 (KRX 1주 단위, crypto 소수점 허용)
4. min_diff_threshold 미만 변화 무시 (cost > marginal alpha)
5. cash buffer 보장 (전액 투입 방지, default 1% 잔여)

자산군 차이:
- KRX: lot_size = 1 (1주 단위), price 단위 KRW, 거래대금 ≥ 100원 미만 무시
- Crypto: lot_size = 0.001 (BTC 기준) 또는 거래소별 minQty, 거래액 ≥ 10 USDT
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Mapping, Optional

import pandas as pd

from portfolio.order_intent import OrderIntent


@dataclass(frozen=True, slots=True)
class LotSpec:
    """자산별 주문 단위 + 최소 거래액 규약."""
    lot_size: float                # 매수/매도 단위 (예: KRX=1.0, BTC=0.001)
    min_notional: float            # 최소 거래액 (KRW or quote currency)
    qty_precision: int = 6         # 소수점 자릿수 (crypto 용)


KRX_LOT = LotSpec(lot_size=1.0, min_notional=100.0, qty_precision=0)
BINANCE_BTC_LOT = LotSpec(lot_size=0.00001, min_notional=10.0, qty_precision=5)
BINANCE_DEFAULT_LOT = LotSpec(lot_size=0.0001, min_notional=10.0, qty_precision=4)


def _round_qty(qty: float, lot: LotSpec) -> float:
    """qty 를 lot_size 단위로 floor 내림."""
    if lot.lot_size <= 0:
        return round(qty, lot.qty_precision)
    return floor(abs(qty) / lot.lot_size) * lot.lot_size * (1 if qty >= 0 else -1)


def weights_to_orders(
    strategy_id: str,
    target_weights: pd.Series,
    current_positions: Mapping[str, float],
    prices: pd.Series,
    total_capital: float,
    *,
    lot_spec: LotSpec = KRX_LOT,
    cash_buffer_pct: float = 0.01,
    rebal_reason: str = "weekly_rebal",
) -> list[OrderIntent]:
    """Generate OrderIntent list from target weight diff.

    Args:
        strategy_id: emitting strategy (e.g., "cs_tsmom_kr_daily").
        target_weights: Series[symbol → weight in [0, 1]] — 다음 리밸 후 목표 비중.
        current_positions: dict[symbol → 현재 보유 수량].
        prices: Series[symbol → 현재 가격]. target_weights 의 index 모두 포함 필요.
        total_capital: 가용 자본 (KRW or USDT).
        lot_spec: 자산별 lot_size + min_notional 규약.
        cash_buffer_pct: total_capital 의 이 비율을 항상 현금 유지 (default 1%).
        rebal_reason: OrderIntent.reason 에 들어갈 식별 문자열.

    Returns:
        list[OrderIntent] — qty=0 또는 min_notional 미만 변화는 생략.
    """
    if total_capital <= 0:
        return []
    investable = total_capital * (1.0 - cash_buffer_pct)

    # Target quantity = weight × investable / price
    universe = set(target_weights.index) | set(current_positions.keys())
    orders: list[OrderIntent] = []

    for symbol in sorted(universe):
        target_w = float(target_weights.get(symbol, 0.0))
        current_q = float(current_positions.get(symbol, 0.0))
        price = float(prices.get(symbol, 0.0))
        if price <= 0:
            # 가격 없으면 청산만 수행 (target=0 으로 간주)
            target_w = 0.0

        target_q_raw = (target_w * investable) / price if price > 0 else 0.0
        target_q = _round_qty(target_q_raw, lot_spec)
        diff_q = target_q - current_q

        if abs(diff_q) < lot_spec.lot_size and lot_spec.lot_size > 0:
            continue
        notional_change = abs(diff_q) * price
        if notional_change < lot_spec.min_notional:
            continue

        side = "buy" if diff_q > 0 else "sell"
        orders.append(OrderIntent(
            strategy_id=strategy_id,
            symbol=symbol,
            side=side,
            qty=abs(diff_q),
            reason=f"{rebal_reason}:target={target_w:.4f}",
            meta={
                "target_weight": target_w,
                "current_qty": current_q,
                "target_qty": target_q,
                "price_used": price,
                "notional": notional_change,
            },
        ))
    return orders


def estimate_post_rebal_cash(
    target_weights: pd.Series,
    prices: pd.Series,
    total_capital: float,
    *,
    cash_buffer_pct: float = 0.01,
    lot_spec: LotSpec = KRX_LOT,
) -> float:
    """리밸 후 현금 잔여 추정 (lot 반올림으로 인한 소수 차이 + cash_buffer)."""
    investable = total_capital * (1.0 - cash_buffer_pct)
    spent = 0.0
    for symbol, w in target_weights.items():
        price = float(prices.get(symbol, 0.0))
        if price <= 0 or w <= 0:
            continue
        target_q = _round_qty((w * investable) / price, lot_spec)
        spent += target_q * price
    return total_capital - spent
