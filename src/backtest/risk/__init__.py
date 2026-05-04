"""Backtest risk sub-package: intra-bar stop-loss / take-profit simulation."""

from .stop_take import StopTakeConfig, StopTakeResult, simulate_stop_take

__all__ = ["StopTakeConfig", "StopTakeResult", "simulate_stop_take"]
