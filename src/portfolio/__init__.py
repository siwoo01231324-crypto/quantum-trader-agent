"""Portfolio layer — strategy aggregation, risk gating, and order evaluation.

Public API (frozen for #79/#80):
- AsyncStrategyOrchestrator: multi-strategy async tick driver (added in T2/#78)
- OrderIntent: immutable order intent dataclass

_SyncStrategyOrchestrator is private (composition backend).
StrategyOrchestrator is the backward-compat alias for #70 tests.
"""
from ._async_orchestrator import AsyncStrategyOrchestrator
from .order_intent import OrderIntent
from .orchestrator import _SyncStrategyOrchestrator, StrategyOrchestrator

__all__ = ["AsyncStrategyOrchestrator", "OrderIntent"]
