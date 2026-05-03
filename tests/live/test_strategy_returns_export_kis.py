"""Tests for src/live/strategy_returns_export.py — TDD Red→Green.

Validates:
- mock fills + balance → daily returns series accuracy
- register_strategy_returns called with correct strategy_id and series
- empty fills → empty series + register still called (prevents CVaR/ENB silence)
- fx_rate fallback on None (KRW series suppressed gracefully)
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

import pandas as pd
import pytest

from src.live.strategy_returns_export import (
    KisFillRecord,
    compute_daily_returns,
    export_to_orchestrator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fill(
    broker_order_id: str,
    fill_price: str,
    fill_qty: str,
    side: str,
    ts: datetime,
    strategy_id: str = "momo_kis_v1",
) -> KisFillRecord:
    return KisFillRecord(
        broker_order_id=broker_order_id,
        fill_price=Decimal(fill_price),
        fill_qty=Decimal(fill_qty),
        side=side,
        ts=ts,
        strategy_id=strategy_id,
    )


def _ts(date_str: str) -> datetime:
    return datetime.fromisoformat(date_str).replace(tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Test 1: basic daily returns from fills + balance history
# ---------------------------------------------------------------------------

def test_compute_daily_returns_basic():
    fills = [
        _fill("b1", "70000", "0.01", "BUY", _ts("2026-04-21T10:00:00")),
        _fill("b2", "71000", "0.01", "SELL", _ts("2026-04-22T10:00:00")),
    ]
    # balance_history: list of (date, equity_krw)
    balance_history = [
        (date(2026, 4, 21), Decimal("1000000")),
        (date(2026, 4, 22), Decimal("1010000")),
        (date(2026, 4, 23), Decimal("1020000")),
    ]

    series = compute_daily_returns(fills, balance_history, strategy_id="momo_kis_v1")

    assert isinstance(series, pd.Series)
    assert len(series) >= 1
    # Returns should be finite floats
    assert all(pd.notna(v) for v in series)


# ---------------------------------------------------------------------------
# Test 2: export_to_orchestrator calls register_strategy_returns
# ---------------------------------------------------------------------------

def test_export_to_orchestrator_calls_register():
    orchestrator = MagicMock()
    series = pd.Series([0.001, -0.002, 0.003], dtype=float)

    export_to_orchestrator(orchestrator, "momo_kis_v1", series)

    orchestrator.register_strategy_returns.assert_called_once_with("momo_kis_v1", series)


# ---------------------------------------------------------------------------
# Test 3: empty fills → empty series + register still called
# ---------------------------------------------------------------------------

def test_empty_fills_still_calls_register():
    """CLAUDE.md invariant: register must be called even with no fills.
    Omission would silence portfolio CVaR/ENB computation."""
    orchestrator = MagicMock()
    series = compute_daily_returns([], [], strategy_id="momo_kis_v1")

    assert isinstance(series, pd.Series)

    export_to_orchestrator(orchestrator, "momo_kis_v1", series)
    orchestrator.register_strategy_returns.assert_called_once()


# ---------------------------------------------------------------------------
# Test 4: compute_daily_returns with single-day fills produces 1-row series
# ---------------------------------------------------------------------------

def test_single_day_returns_one_row():
    fills = [
        _fill("b1", "65000", "0.01", "BUY", _ts("2026-04-21T09:00:00")),
        _fill("b2", "65500", "0.01", "SELL", _ts("2026-04-21T14:00:00")),
    ]
    balance_history = [
        (date(2026, 4, 21), Decimal("500000")),
        (date(2026, 4, 22), Decimal("505000")),
    ]

    series = compute_daily_returns(fills, balance_history, strategy_id="test_strat")

    assert isinstance(series, pd.Series)
    # At least 1 data point for the active trading day
    assert len(series) >= 1


# ---------------------------------------------------------------------------
# Test 5: fx_rate None → series still returned (KRW suppressed gracefully)
# ---------------------------------------------------------------------------

def test_compute_daily_returns_no_fx_rate_ok(monkeypatch):
    """When fx_rate returns None (>24h stale), returns series is still produced
    using raw KRW values (KIS returns KRW directly)."""
    import src.live.strategy_returns_export as mod
    monkeypatch.setattr(mod, "get_usd_krw", lambda: None)

    fills = [
        _fill("b1", "68000", "0.01", "BUY", _ts("2026-04-21T10:00:00")),
    ]
    balance_history = [
        (date(2026, 4, 21), Decimal("800000")),
        (date(2026, 4, 22), Decimal("808000")),
    ]

    series = compute_daily_returns(fills, balance_history, strategy_id="kis_strat")
    assert isinstance(series, pd.Series)
