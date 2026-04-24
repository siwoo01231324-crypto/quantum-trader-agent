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
