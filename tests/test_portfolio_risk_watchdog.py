"""Tests for src/risk/watchdog.py — PortfolioRiskWatchdog (#120).

AC coverage:
  - timestamp tracking for periodic evaluator
  - 30-min staleness → alert + qta_portfolio_risk_watchdog_state metric
  - fail-closed: new buy orders blocked during silence
  - evaluator failure simulation → watchdog fires + buy blocked
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from risk.watchdog import PortfolioRiskWatchdog  # noqa: E402


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_watchdog(staleness_seconds: int = 1800) -> PortfolioRiskWatchdog:
    return PortfolioRiskWatchdog(staleness_seconds=staleness_seconds)


# ---------------------------------------------------------------------------
# timestamp tracking
# ---------------------------------------------------------------------------

class TestTimestampTracking:
    def test_initial_state_is_stale(self):
        wd = _make_watchdog()
        assert wd.is_stale() is True
        assert wd.last_success_dt is None
        assert wd.seconds_since_last_success() is None

    def test_record_success_clears_stale(self):
        wd = _make_watchdog(staleness_seconds=60)
        wd.record_success()
        assert wd.is_stale() is False
        assert wd.last_success_dt is not None

    def test_seconds_since_last_success_increases(self):
        wd = _make_watchdog(staleness_seconds=9999)
        wd.record_success()
        t0 = wd.seconds_since_last_success()
        assert t0 is not None
        assert t0 >= 0.0

    def test_stale_after_threshold(self):
        wd = _make_watchdog(staleness_seconds=1)
        wd.record_success()
        # manipulate internal monotonic to simulate 2 s elapsed
        wd._last_success_ts = time.monotonic() - 2
        assert wd.is_stale() is True


# ---------------------------------------------------------------------------
# alert firing
# ---------------------------------------------------------------------------

class TestAlertFiring:
    def test_check_fires_alert_when_stale(self):
        wd = _make_watchdog(staleness_seconds=1)
        wd._last_success_ts = time.monotonic() - 2

        with patch("risk.watchdog.notify") as mock_notify:
            result = wd.check()

        assert result is True
        mock_notify.assert_called_once()
        call_kwargs = mock_notify.call_args
        assert call_kwargs.kwargs["level"] == "critical" or call_kwargs.args[0] == "critical"

    def test_alert_fires_only_once_per_episode(self):
        wd = _make_watchdog(staleness_seconds=1)
        wd._last_success_ts = time.monotonic() - 2

        with patch("risk.watchdog.notify") as mock_notify:
            wd.check()
            wd.check()
            wd.check()

        assert mock_notify.call_count == 1

    def test_alert_resets_after_recovery(self):
        wd = _make_watchdog(staleness_seconds=1)
        wd._last_success_ts = time.monotonic() - 2

        with patch("risk.watchdog.notify") as mock_notify:
            wd.check()  # fires alert

        wd.record_success()  # evaluator recovered

        wd._last_success_ts = time.monotonic() - 2  # stale again

        with patch("risk.watchdog.notify") as mock_notify2:
            wd.check()  # should fire again

        mock_notify2.assert_called_once()

    def test_no_alert_when_healthy(self):
        wd = _make_watchdog(staleness_seconds=9999)
        wd.record_success()

        with patch("risk.watchdog.notify") as mock_notify:
            result = wd.check()

        assert result is False
        mock_notify.assert_not_called()

    def test_never_ran_triggers_alert(self):
        wd = _make_watchdog()
        with patch("risk.watchdog.notify") as mock_notify:
            result = wd.check()
        assert result is True
        mock_notify.assert_called_once()


# ---------------------------------------------------------------------------
# fail-closed buy block
# ---------------------------------------------------------------------------

class TestFailClosed:
    def test_should_block_buy_when_stale(self):
        wd = _make_watchdog(staleness_seconds=1)
        wd._last_success_ts = time.monotonic() - 2
        assert wd.should_block_buy() is True

    def test_should_not_block_buy_when_healthy(self):
        wd = _make_watchdog(staleness_seconds=9999)
        wd.record_success()
        assert wd.should_block_buy() is False

    def test_never_ran_blocks_buy(self):
        wd = _make_watchdog()
        assert wd.should_block_buy() is True


# ---------------------------------------------------------------------------
# metric integration
# ---------------------------------------------------------------------------

class TestMetricIntegration:
    def test_metric_set_to_1_when_stale(self):
        from prometheus_client import CollectorRegistry
        from observability.metrics import Metrics

        registry = CollectorRegistry()
        metrics = Metrics(registry=registry)

        wd = _make_watchdog(staleness_seconds=1)
        wd._last_success_ts = time.monotonic() - 2

        with patch("risk.watchdog.get_registry", return_value=metrics):
            with patch("risk.watchdog.notify"):
                wd.check()

        val = registry.get_sample_value("qta_portfolio_risk_watchdog_state")
        assert val == 1.0

    def test_metric_set_to_0_when_healthy(self):
        from prometheus_client import CollectorRegistry
        from observability.metrics import Metrics

        registry = CollectorRegistry()
        metrics = Metrics(registry=registry)

        wd = _make_watchdog(staleness_seconds=9999)

        with patch("risk.watchdog.get_registry", return_value=metrics):
            wd.record_success()
            wd.check()

        val = registry.get_sample_value("qta_portfolio_risk_watchdog_state")
        assert val == 0.0


# ---------------------------------------------------------------------------
# evaluator failure simulation (AC integration)
# ---------------------------------------------------------------------------

class TestEvaluatorFailureSimulation:
    """Simulates the periodic evaluator failing and verifies watchdog responds."""

    def test_evaluator_fails_watchdog_fires_buy_blocked(self):
        """Simulate: evaluator ran once, then stopped → watchdog fires + blocks buy."""
        wd = _make_watchdog(staleness_seconds=30 * 60)

        # evaluator ran successfully
        wd.record_success()
        assert wd.should_block_buy() is False

        # evaluator silenced for > threshold
        wd._last_success_ts = time.monotonic() - (30 * 60 + 1)

        assert wd.is_stale() is True
        assert wd.should_block_buy() is True

        with patch("risk.watchdog.notify") as mock_notify:
            fired = wd.check()

        assert fired is True
        mock_notify.assert_called_once()
        # alert body mentions "BLOCKED"
        call_args = mock_notify.call_args
        body_arg = call_args.kwargs.get("body", "") or (call_args.args[2] if len(call_args.args) > 2 else "")
        assert "BLOCKED" in body_arg

    def test_evaluator_recovers_unblocks_buy(self):
        wd = _make_watchdog(staleness_seconds=1)
        wd._last_success_ts = time.monotonic() - 2

        with patch("risk.watchdog.notify"):
            wd.check()

        assert wd.should_block_buy() is True

        wd.record_success()

        assert wd.should_block_buy() is False
        assert wd._alert_fired is False
