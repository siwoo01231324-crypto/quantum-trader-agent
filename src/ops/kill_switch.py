"""Kill switch gate - all new orders MUST pass through allow_order().

Spec: docs/specs/kill-switch-dr.md
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class KillEvent:
    ts: float
    reason: str
    source: str  # "auto:dd" | "auto:api" | "auto:anomaly" | "manual:cli" | ...


class KillSwitchTripped(Exception):
    pass


@dataclass
class KillSwitch:
    dry_run: bool = False
    _tripped: bool = False
    _events: list[KillEvent] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def tripped(self) -> bool:
        return self._tripped

    def trip(self, reason: str, source: str) -> KillEvent:
        with self._lock:
            ev = KillEvent(ts=time.time(), reason=reason, source=source)
            self._events.append(ev)
            if not self._tripped:
                self._tripped = True
                logger.critical("KILL SWITCH TRIPPED source=%s reason=%s dry_run=%s",
                                source, reason, self.dry_run)
            return ev

    def release(self, operator: str) -> None:
        with self._lock:
            if not self._tripped:
                return
            self._tripped = False
            logger.warning("KILL SWITCH RELEASED operator=%s", operator)

    def allow_order(self, *, liquidation: bool = False) -> bool:
        """Gate function. Returns True if order may proceed.

        New orders are blocked when tripped. Liquidation orders are allowed
        even when tripped (whitelist), per spec section 3.
        """
        if not self._tripped:
            return True
        if liquidation:
            return True
        return False

    def assert_allow_order(self, *, liquidation: bool = False) -> None:
        if not self.allow_order(liquidation=liquidation):
            raise KillSwitchTripped(
                f"new orders blocked; events={len(self._events)}"
            )

    def last_event(self) -> Optional[KillEvent]:
        return self._events[-1] if self._events else None

    def history(self) -> list[KillEvent]:
        return list(self._events)
