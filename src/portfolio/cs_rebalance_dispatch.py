"""Cross-sectional 전략 rebal dispatch — orchestrator + broker 통합 (#218 Phase 2 P0).

universe-scan AsyncStrategy 가 매주 리밸 시점에 산출한 weights 를 받아서,
종목별 OrderIntent → OrderRequest → broker.place_order 까지 연결.

흐름:
1. CSAsyncStrategy.on_bar(ctx) → Signal(buy/sell, size=exposure)
2. dispatch_rebalance(strategy, broker, ...) 호출
3. strategy.latest_weights 읽어 weights_to_orders 변환
4. 각 OrderIntent → OrderRequest 변환 → broker.place_order 순차 호출
5. failed orders 수집 + 결과 보고

본 모듈은 broker 측 구현 변경 없이 동작 — paper broker / future KIS / Binance
broker 모두 AsyncBrokerAdapter 인터페이스 (place_order(OrderRequest) → OrderAck) 만
충족하면 OK.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Mapping, Optional

import pandas as pd

from brokers.base import (
    AsyncBrokerAdapter,
    OrderAck,
    OrderRequest,
    OrderType,
)
from execution.base import Side, TimeInForce
from portfolio.order_intent import OrderIntent
from portfolio.weights_to_orders import KRX_LOT, LotSpec, weights_to_orders

logger = logging.getLogger(__name__)


@dataclass
class RebalanceReport:
    strategy_id: str
    submitted: list[OrderAck] = field(default_factory=list)
    rejected: list[OrderAck] = field(default_factory=list)
    skipped: list[OrderIntent] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    @property
    def total_orders(self) -> int:
        return len(self.submitted) + len(self.rejected)

    @property
    def n_buys(self) -> int:
        return len([a for a in self.submitted if "buy" in str(a.client_order_id).lower()])

    @property
    def n_sells(self) -> int:
        return len([a for a in self.submitted if "sell" in str(a.client_order_id).lower()])


def _intent_to_request(intent: OrderIntent) -> OrderRequest:
    """OrderIntent (전략 레벨) → OrderRequest (broker 레벨) 변환."""
    side_enum = Side.BUY if intent.side == "buy" else Side.SELL
    coid = f"{intent.strategy_id}:{intent.side}:{intent.symbol}:{uuid.uuid4().hex[:8]}"
    return OrderRequest(
        client_order_id=coid,
        symbol=intent.symbol,
        side=side_enum,
        qty=Decimal(str(intent.qty)),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )


async def dispatch_rebalance(
    strategy_id: str,
    target_weights: pd.Series,
    current_positions: Mapping[str, float],
    prices: pd.Series,
    total_capital: float,
    broker: AsyncBrokerAdapter,
    *,
    lot_spec: LotSpec = KRX_LOT,
    cash_buffer_pct: float = 0.01,
    rebal_reason: str = "weekly_rebal",
) -> RebalanceReport:
    """전략 weights → orders → broker 발주 일괄 실행.

    Args:
        strategy_id: 발주 식별자 (e.g., "cs_tsmom_kr_daily").
        target_weights: 다음 리밸 후 목표 비중 Series.
        current_positions: 현재 보유 수량 dict.
        prices: 현재 가격 Series.
        total_capital: 가용 자본.
        broker: AsyncBrokerAdapter 호환 인스턴스 (paper/KIS/Binance).
        lot_spec: 자산별 lot 규약.
        cash_buffer_pct: 현금 보유 비율.
        rebal_reason: OrderIntent.reason 문자열.

    Returns:
        RebalanceReport — 제출된/거부된 주문 + 요약 통계.
    """
    intents = weights_to_orders(
        strategy_id=strategy_id,
        target_weights=target_weights,
        current_positions=current_positions,
        prices=prices,
        total_capital=total_capital,
        lot_spec=lot_spec,
        cash_buffer_pct=cash_buffer_pct,
        rebal_reason=rebal_reason,
    )

    report = RebalanceReport(strategy_id=strategy_id)
    if not intents:
        report.summary = {"reason": "no_orders_needed", "n_target": int((target_weights > 0).sum())}
        return report

    n_buys = sum(1 for i in intents if i.side == "buy")
    n_sells = sum(1 for i in intents if i.side == "sell")

    for intent in intents:
        try:
            req = _intent_to_request(intent)
            ack = await broker.place_order(req)
            if ack.status in ("REJECTED", "CANCELED"):
                report.rejected.append(ack)
                logger.warning(
                    "rebal order rejected: strategy=%s symbol=%s side=%s reason=%s",
                    strategy_id, intent.symbol, intent.side, ack.reject_reason,
                )
            else:
                report.submitted.append(ack)
        except Exception as exc:  # broker-side unexpected error
            logger.error(
                "rebal order failed: strategy=%s symbol=%s side=%s error=%s",
                strategy_id, intent.symbol, intent.side, exc,
            )
            report.skipped.append(intent)

    report.summary = {
        "n_intents": len(intents),
        "n_buys_planned": n_buys,
        "n_sells_planned": n_sells,
        "n_submitted": len(report.submitted),
        "n_rejected": len(report.rejected),
        "n_skipped_exception": len(report.skipped),
    }
    logger.info(
        "rebal complete: strategy=%s intents=%d submitted=%d rejected=%d skipped=%d",
        strategy_id, len(intents), len(report.submitted),
        len(report.rejected), len(report.skipped),
    )
    return report


def telegram_digest_message(report: RebalanceReport,
                            target_weights: pd.Series,
                            current_positions: Mapping[str, float]) -> str:
    """주간 rebal Telegram 메시지 1건. universe-scan-runbook §Telegram 정합."""
    held_now = {s for s in current_positions if current_positions[s] > 0}
    target_set = {s for s, w in target_weights.items() if w > 0}
    new_buys = target_set - held_now
    sells = held_now - target_set
    held = held_now & target_set
    s = report.summary
    return (
        f"[REBAL] {report.strategy_id}\n"
        f"  매수 {len(new_buys)}종: {', '.join(sorted(new_buys)[:5])}{'...' if len(new_buys) > 5 else ''}\n"
        f"  매도 {len(sells)}종: {', '.join(sorted(sells)[:5])}{'...' if len(sells) > 5 else ''}\n"
        f"  유지 {len(held)}종\n"
        f"  주문 {s.get('n_submitted', 0)} 제출 / {s.get('n_rejected', 0)} 거부"
    )
