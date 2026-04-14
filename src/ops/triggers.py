"""Automatic trip triggers (spec section 4).

Three triggers required by AC:
  1. Daily drawdown breach
  2. Consecutive API errors
  3. Suspicious fill pattern (burst)
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque

from .kill_switch import KillSwitch


@dataclass
class DrawdownTrigger:
    """Trips when intraday DD breaches limit (negative ratio)."""
    kill: KillSwitch
    limit: float = -0.03  # -3%
    starting_equity: float = 0.0

    def update(self, current_equity: float) -> bool:
        if self.starting_equity <= 0:
            return False
        dd = (current_equity - self.starting_equity) / self.starting_equity
        if dd <= self.limit:
            self.kill.trip(
                reason=f"DD {dd:.4f} <= limit {self.limit:.4f}",
                source="auto:dd",
            )
            return True
        return False


@dataclass
class ApiErrorTrigger:
    """Trips after N consecutive API errors."""
    kill: KillSwitch
    threshold: int = 5
    _consecutive: int = 0

    def record_error(self) -> bool:
        self._consecutive += 1
        if self._consecutive >= self.threshold:
            self.kill.trip(
                reason=f"API errors {self._consecutive} >= {self.threshold}",
                source="auto:api",
            )
            return True
        return False

    def record_success(self) -> None:
        self._consecutive = 0


@dataclass
class FillAnomalyTrigger:
    """Trips when burst fills exceed threshold within window seconds for one symbol."""
    kill: KillSwitch
    window_seconds: float = 1.0
    burst_threshold: int = 5
    _fills: dict[str, Deque[float]] = field(default_factory=dict)

    def record_fill(self, symbol: str, ts: float | None = None) -> bool:
        ts = ts if ts is not None else time.time()
        dq = self._fills.setdefault(symbol, deque())
        dq.append(ts)
        cutoff = ts - self.window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.burst_threshold:
            self.kill.trip(
                reason=f"fill burst {len(dq)} on {symbol} within {self.window_seconds}s",
                source="auto:anomaly",
            )
            return True
        return False
