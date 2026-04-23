"""Portfolio layer — strategy aggregation, risk gating, and order evaluation.

This is the single assembly point where individual strategy outputs meet the
portfolio-level risk module (src/risk/). Full multi-strategy async execution
is tracked under issue #78; this module provides the stable interface that
#78 will elaborate.
"""
from .orchestrator import StrategyOrchestrator

__all__ = ["StrategyOrchestrator"]
