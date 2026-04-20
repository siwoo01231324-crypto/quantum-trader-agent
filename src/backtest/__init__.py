"""Lightweight event-driven backtest engine."""

from .protocol import Bar, Signal, Strategy
from .engine import BacktestConfig, BacktestResult, run_backtest
from .metrics import (
    compute_sharpe,
    compute_max_drawdown,
    compute_total_return,
    compute_win_rate,
    compute_all_metrics,
)
from .bundle import load_ohlcv_from_parquet

__all__ = [
    "Bar",
    "Signal",
    "Strategy",
    "BacktestConfig",
    "BacktestResult",
    "run_backtest",
    "compute_sharpe",
    "compute_max_drawdown",
    "compute_total_return",
    "compute_win_rate",
    "compute_all_metrics",
    "load_ohlcv_from_parquet",
]
