from __future__ import annotations
from typing import Protocol, runtime_checkable
from dataclasses import dataclass
import pandas as pd


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
    action: str  # "buy" | "sell" | "hold"
    size: float  # fraction of equity (0.0 - 1.0)
    reason: str


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
