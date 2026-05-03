"""Portfolio risk evaluator watchdog.

Tracks the last successful evaluation timestamp and fires alerts + blocks
new buy orders when the evaluator has been silent for > staleness_seconds.

AC:
  - timestamp tracking for periodic evaluator
  - alert (Telegram/Slack) + qta_portfolio_risk_watchdog_state metric on staleness
  - fail-closed: block new buy orders during silence
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Optional

from observability.alerts import notify
from observability.metrics import get_registry


class PortfolioRiskWatchdog:
    """Watchdog for the per_portfolio_risk periodic evaluator.

    Args:
        staleness_seconds: seconds of silence before triggering alert (default 1800 = 30 min).
    """

    def __init__(self, staleness_seconds: int = 1800) -> None:
        self.staleness_seconds = staleness_seconds
        self._last_success_ts: Optional[float] = None  # monotonic
        self._last_success_dt: Optional[datetime] = None
        self._alert_fired: bool = False

    def record_success(self) -> None:
        """Call after every successful portfolio risk evaluation."""
        self._last_success_ts = time.monotonic()
        self._last_success_dt = datetime.now(timezone.utc)
        self._alert_fired = False
        try:
            get_registry().portfolio_risk_watchdog_state.set(0)
        except Exception:
            pass

    @property
    def last_success_dt(self) -> Optional[datetime]:
        return self._last_success_dt

    def seconds_since_last_success(self) -> Optional[float]:
        if self._last_success_ts is None:
            return None
        return time.monotonic() - self._last_success_ts

    def is_stale(self) -> bool:
        """True when evaluator has been silent longer than staleness_seconds."""
        elapsed = self.seconds_since_last_success()
        if elapsed is None:
            return True  # never ran → treat as stale
        return elapsed >= self.staleness_seconds

    def check(self) -> bool:
        """Check staleness; fire alert once per stale episode. Returns is_stale()."""
        stale = self.is_stale()
        try:
            get_registry().portfolio_risk_watchdog_state.set(1 if stale else 0)
        except Exception:
            pass
        if stale and not self._alert_fired:
            elapsed = self.seconds_since_last_success()
            elapsed_str = f"{elapsed:.0f}s" if elapsed is not None else "never"
            notify(
                level="critical",
                title="portfolio_risk watchdog: evaluator silent",
                body=(
                    f"per_portfolio_risk periodic evaluator has not run for {elapsed_str} "
                    f"(threshold={self.staleness_seconds}s). "
                    "New buy orders are BLOCKED (fail-closed) until evaluator recovers."
                ),
                fields={
                    "last_success": (
                        self._last_success_dt.isoformat() if self._last_success_dt else "never"
                    ),
                    "staleness_threshold_s": str(self.staleness_seconds),
                },
            )
            self._alert_fired = True
        return stale

    def should_block_buy(self) -> bool:
        """Fail-closed gate: block new buy orders when evaluator is stale."""
        return self.is_stale()
