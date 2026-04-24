from __future__ import annotations
from typing import Optional, Protocol, runtime_checkable
from dataclasses import dataclass, field
import pandas as pd

# INVARIANT #6: expected_return, win_probability, confidence MUST be computed by
# deterministic code only. LLM output MUST NOT be assigned to these fields directly.
# See CLAUDE.md invariant #6 and scripts/check_invariants.py::_check_llm_delegation.


@dataclass
class Bar:
    ts: pd.Timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Signal:
    action: str   # "buy" | "sell" | "hold"
    size: float   # fraction of equity (0.0 - 1.0)
    reason: str
    # kw-only Optional fields — backward compatible; legacy Signal(action, size, reason) unchanged.
    # strategy_id is NOT here; pass it to register_strategy_returns(strategy_id, ...) instead.
    expected_return: Optional[float] = field(default=None, kw_only=True)
    win_probability: Optional[float] = field(default=None, kw_only=True)
    confidence: Optional[float] = field(default=None, kw_only=True)


@runtime_checkable
class Strategy(Protocol):
    # Optional convention (not on the Protocol): strategies may declare a
    #   required_factors: ClassVar[list[str]] = ["rsi", "sma", ...]
    # class attribute. The engine uses getattr(strategy, "required_factors", [])
    # and precomputes each listed factor via the signals registry, injecting the
    # result as context["factors"][name] before every on_bar call. Documented in
    # src/backtest/.ai.md; kept off the Protocol so isinstance() stays permissive.
    def on_init(self, context: dict) -> None: ...
    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal: ...


class AsyncStrategy(Protocol):
    """Async variant of Strategy for use with AsyncStrategyOrchestrator (#78).

    Frozen interface — consumed by #79 (Signal Router) and #80 (Broker Executor).
    engine.py continues to use the sync Strategy Protocol unchanged.
    """
    async def on_bar(self, ctx: object) -> "Signal | None": ...
