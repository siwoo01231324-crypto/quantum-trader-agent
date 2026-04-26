"""Automatic trip triggers (spec section 4).

Three triggers required by AC:
  1. Daily drawdown breach
  2. Consecutive API errors  (ApiErrorTrigger — legacy)
  3. Suspicious fill pattern (burst)

Phase D-1 additions:
  - DrawdownTrigger: peak tracking
  - ApiErrorRateTrigger: sliding-window rate-based (new)
  - FillAnomalyTrigger: dump_path JSONL dump on burst
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque

from .kill_switch import KillSwitch

logger = logging.getLogger(__name__)


@dataclass
class DrawdownTrigger:
    """Trips when equity drops more than `limit` (negative ratio) below the **running peak**.

    peak 기반 tracking 으로 Phase 1 USDT-M 잔고 시뮬에 적합.
    starting_equity 가 0 또는 음수면 첫 update 의 current_equity 를 peak 로 채택.
    """
    kill: KillSwitch
    limit: float = -0.03
    starting_equity: float = 0.0  # backward compat — 첫 update 때 peak 초기화에 사용
    _peak: float = field(default=0.0, init=False, repr=False)  # internal peak tracker

    def update(self, current_equity: float) -> bool:
        # 첫 호출 시 peak 초기화
        if self._peak <= 0:
            self._peak = max(self.starting_equity, current_equity)
        # peak 갱신
        self._peak = max(self._peak, current_equity)
        # peak 대비 DD 계산
        if self._peak <= 0:
            return False
        dd = (current_equity - self._peak) / self._peak
        if dd <= self.limit:
            self.kill.trip(
                reason=f"DD {dd:.4f} <= limit {self.limit:.4f} (peak={self._peak:.2f}, current={current_equity:.2f})",
                source="auto:dd",
            )
            return True
        return False


@dataclass
class ApiErrorTrigger:
    """Trips after N consecutive API errors. (legacy — preserved for backward compat)"""
    kill: KillSwitch
    threshold: int = 5
    _consecutive: int = field(default=0, init=False, repr=False)

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
class ApiErrorRateTrigger:
    """Sliding window rate-based API error trigger.

    윈도우 5분, 임계 5%, 최소 표본 20건.
    (error_count / total_count) > error_rate_threshold AND total_count >= min_samples 일 때 trip.
    """
    kill: KillSwitch
    window_seconds: float = 300.0
    error_rate_threshold: float = 0.05  # 5%
    min_samples: int = 20
    _events: Deque[tuple[float, bool]] = field(default_factory=deque, init=False, repr=False)

    def record(self, *, is_error: bool, ts: float | None = None) -> bool:
        ts = ts if ts is not None else time.time()
        self._events.append((ts, is_error))
        cutoff = ts - self.window_seconds
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
        if len(self._events) < self.min_samples:
            return False
        error_count = sum(1 for _, e in self._events if e)
        total = len(self._events)
        rate = error_count / total
        if rate > self.error_rate_threshold:
            self.kill.trip(
                reason=f"API error rate {rate:.4f} > {self.error_rate_threshold:.4f} (n={total}, errors={error_count})",
                source="auto:api",
            )
            return True
        return False


@dataclass
class FillAnomalyTrigger:
    """Trips when burst fills exceed threshold within window seconds for one symbol."""
    kill: KillSwitch
    window_seconds: float = 1.0
    burst_threshold: int = 5
    dump_path: Path | None = None  # JSONL 덤프 경로 (None 이면 logger.warning 만)
    _fills: dict[str, Deque[float]] = field(default_factory=dict, init=False, repr=False)

    def record_fill(self, symbol: str, ts: float | None = None) -> bool:
        ts = ts if ts is not None else time.time()
        dq = self._fills.setdefault(symbol, deque())
        dq.append(ts)
        cutoff = ts - self.window_seconds
        while dq and dq[0] < cutoff:
            dq.popleft()
        if len(dq) >= self.burst_threshold:
            window_fills = list(dq)
            self.kill.trip(
                reason=f"fill burst {len(dq)} on {symbol} within {self.window_seconds}s",
                source="auto:anomaly",
            )
            self._dump(symbol, window_fills, ts)
            return True
        return False

    def _dump(self, symbol: str, fills: list[float], trip_ts: float) -> None:
        record = {
            "trigger": "fill_anomaly",
            "symbol": symbol,
            "window_seconds": self.window_seconds,
            "burst_threshold": self.burst_threshold,
            "fills_in_window": fills,
            "trip_ts": trip_ts,
        }
        if self.dump_path is not None:
            self.dump_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.dump_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.warning("fill anomaly dump: %s", record)
