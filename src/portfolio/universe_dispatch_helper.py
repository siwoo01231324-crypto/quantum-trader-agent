"""Orchestrator + universe-scan dispatch glue (#218 Phase 2 P0 finalize).

`AsyncStrategyOrchestrator.run_bar` 가 universe-scan 전략에 대해 single OrderIntent
(symbol="*_BASKET") 를 emit 한다. 본 helper 는 그 basket-level intent 를 실제
다종목 OrderIntent 리스트로 expand 하고 broker 에 발주.

Live loop 의 BrokerExecutor 가 본 helper 를 호출 (또는 inline) — 기존 단일종목
OrderIntent path 는 그대로 (legacy 전략은 BASKET symbol 사용 안 함).

본 모듈은 orchestrator 코드 미변경 — orchestrator 의 `_strategies` 딕셔너리에서
strategy.latest_weights 를 읽어 expand. additive only.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Mapping

import pandas as pd

from brokers.base import AsyncBrokerAdapter
from portfolio.cs_rebalance_dispatch import RebalanceReport, dispatch_rebalance
from portfolio.order_intent import OrderIntent
from portfolio.weights_to_orders import KRX_LOT, BINANCE_DEFAULT_LOT, LotSpec

logger = logging.getLogger(__name__)

_BASKET_SUFFIX = "_BASKET"


@dataclass
class UniverseDispatchSummary:
    intent_count_in: int = 0
    basket_intents: list[OrderIntent] = field(default_factory=list)
    passthrough_intents: list[OrderIntent] = field(default_factory=list)
    rebal_reports: list[RebalanceReport] = field(default_factory=list)


def is_basket_intent(intent: OrderIntent) -> bool:
    """Symbol 이 universe-scan basket 식별자 (`*_BASKET`) 인지."""
    return isinstance(intent.symbol, str) and intent.symbol.endswith(_BASKET_SUFFIX)


async def expand_basket_intents(
    intents: list[OrderIntent],
    orchestrator,                                 # AsyncStrategyOrchestrator
    broker: AsyncBrokerAdapter,
    *,
    prices_provider: Callable[[str, list[str]], pd.Series],
    positions_provider: Callable[[str], Mapping[str, float]],
    capital_provider: Callable[[str], float],
    lot_specs: Mapping[str, LotSpec] | None = None,
    cash_buffer_pct: float = 0.01,
) -> UniverseDispatchSummary:
    """run_bar 결과를 받아서 basket 인텐트만 dispatch_rebalance 로 expand.

    Args:
        intents: orchestrator.run_bar 가 반환한 OrderIntent 리스트.
        orchestrator: strategy.latest_weights 조회용. `orchestrator._strategies` 접근.
        broker: AsyncBrokerAdapter — paper / KIS / Binance 호환.
        prices_provider: (strategy_id, symbol_list) → pd.Series[symbol → price].
                        캐시된 universe quote 또는 broker 가 fetch 한 결과 사용.
        positions_provider: strategy_id → dict[symbol → 현재 보유 qty].
        capital_provider: strategy_id → 가용 자본 (KRW or USDT).
        lot_specs: optional strategy_id → LotSpec mapping. 미지정 시 symbol prefix
                   기준 자동 (e.g. "KRX_*" → KRX_LOT, "CRYPTO_*" → BINANCE_DEFAULT_LOT).
        cash_buffer_pct: 보유 자본의 이 비율은 항상 현금 (default 1%).

    Returns:
        UniverseDispatchSummary — 각 basket 의 RebalanceReport + passthrough intents.
    """
    summary = UniverseDispatchSummary(intent_count_in=len(intents))
    lot_specs = lot_specs or {}

    for intent in intents:
        if not is_basket_intent(intent):
            summary.passthrough_intents.append(intent)
            continue
        summary.basket_intents.append(intent)

        sid = intent.strategy_id
        strategy = orchestrator._strategies.get(sid)
        if strategy is None:
            logger.warning("universe_dispatch_unknown_strategy strategy_id=%s", sid)
            continue
        weights = getattr(strategy, "latest_weights", None)
        if weights is None or weights.empty:
            logger.info("universe_dispatch_no_weights strategy_id=%s reason=warmup_or_empty", sid)
            continue

        symbols = list(weights.index)
        try:
            prices = prices_provider(sid, symbols)
            current_positions = positions_provider(sid)
            capital = capital_provider(sid)
        except Exception as exc:
            logger.error(
                "universe_dispatch_provider_error strategy_id=%s error=%s",
                sid, exc,
            )
            continue

        lot = lot_specs.get(sid) or _infer_lot_spec(intent.symbol)
        report = await dispatch_rebalance(
            strategy_id=sid,
            target_weights=weights,
            current_positions=current_positions,
            prices=prices,
            total_capital=capital,
            broker=broker,
            lot_spec=lot,
            cash_buffer_pct=cash_buffer_pct,
            rebal_reason="universe_scan_rebal",
        )
        summary.rebal_reports.append(report)
    return summary


def _infer_lot_spec(symbol: str) -> LotSpec:
    """Basket symbol prefix 에서 lot spec 추론. KRX_* → KRX_LOT, 기타 → BINANCE_DEFAULT_LOT."""
    if symbol.startswith("KRX_"):
        return KRX_LOT
    return BINANCE_DEFAULT_LOT
