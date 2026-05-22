from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True, slots=True)
class OrderIntent:
    """Immutable intent to place an order, emitted by AsyncStrategyOrchestrator.run_bar.

    Downstream consumers (#80 BrokerExecutor) receive a list[OrderIntent] and are
    responsible for translating each intent into a broker-level Order.

    INVARIANT #6: all fields are populated by deterministic code only.
    LLM output must not be assigned here directly.
    """
    strategy_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    reason: str
    meta: dict | None = field(default=None)
    # #238 Item 7 — when True the broker order is submitted reduceOnly so it
    # can only shrink an existing position, never open/extend one. Set for
    # every strategy SELL: our strategies are long-only, so a sell is always
    # an exit, and reduceOnly makes the exchange itself refuse to turn a
    # "sell with no long" into a naked short (the root incident).
    reduce_only: bool = False
    # 2026-05-22 post-only Maker 진입 (post-only-maker-entry.draft.md, 2~4단계).
    #   "market"    — 기존 시장가(Taker) 동작. default → 모든 기존 경로 byte-identical.
    #   "post_only" — executor 가 GTX(post-only) LIMIT 으로 발주 (진입 Maker
    #                 수수료 0.018% vs Taker 0.045%). 미체결 시 fallback 시장가 재발주.
    # AsyncStrategyOrchestrator.run_bar 가 strategy 의 ``entry_order_type``
    # 속성을 읽어 BUY 진입 intent 에만 stamp. SELL(청산)은 항상 "market" —
    # 확실한 체결이 수수료 절감보다 우선.
    entry_order_type: Literal["market", "post_only"] = "market"
    # post-only LIMIT 가격 산출 기준가. orchestrator 가 per-symbol 로 계산한
    # ``order_price`` 를 그대로 stamp (멀티심볼 배치에서도 심볼별 정확 — gap A).
    # None → executor 가 post-only 산출 불가 → 안전하게 market 으로 강등.
    ref_price: float | None = None
