"""Tests for the dashboard's manual-close endpoint + live-price overlay
on ``/api/strategy_positions``.

Manual close: POST ``/api/strategies/{sid}/positions/{sym}/close`` builds
an ``OrderIntent`` from the current net position and routes it through the
``manual_close_executor`` closure that ``run_shadow_loop`` builds.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from src.live.price_cache import LivePriceCache


@pytest.fixture
def state(tmp_path: Path) -> DashboardState:
    """A minimal DashboardState with no WAL — endpoints under test never
    touch the WAL except for the strategy-positions aggregator, which is
    fed by the test-side stub below.
    """
    return DashboardState()


# ── /api/strategy_positions live-price overlay ─────────────────────────


def test_strategy_positions_overlays_mark_price_and_pnl_pct(
    tmp_path: Path, monkeypatch,
) -> None:
    """When ``price_cache`` is wired, each row gets ``mark_price`` (from the
    cache) + ``pnl_pct`` (computed from avg vs mark, sign-corrected for
    LONG/SHORT)."""
    state = DashboardState()
    cache = LivePriceCache()
    cache.set_price("NEARUSDT", Decimal("5.0"), datetime.now(timezone.utc))
    cache.set_price("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc))
    state.price_cache = cache

    # Stub /api/strategy_positions's WAL walk by injecting a fake wal_replay
    # that returns one buy event for each symbol.
    from src.live.types import WALEvent
    fake_events = [
        WALEvent(
            ts="2026-05-21T00:00:00+00:00",
            event_type="order_filled",
            payload={
                "strategy_id": "cand-c-bb",
                "symbol": "NEARUSDT",
                "side": "buy",
                "qty": "10",
                "fill_price": "4.0",
            },
        ),
        WALEvent(
            ts="2026-05-21T00:01:00+00:00",
            event_type="order_filled",
            payload={
                "strategy_id": "cand-c-bb",
                "symbol": "BTCUSDT",
                "side": "sell",  # short
                "qty": "0.1",
                "fill_price": "31000",
            },
        ),
    ]

    def fake_replay(path):
        return (fake_events, [])

    monkeypatch.setattr("src.dashboard.app.wal_replay", fake_replay)

    # Make the endpoint discover at least one "wal path" so it walks events.
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")  # exists, but our stub ignores content
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    assert resp.status_code == 200
    data = resp.json()
    rows = {r["symbol"]: r for r in data["strategies"]}
    # NEARUSDT — LONG: bought 10@4.0, mark 5.0 → +25%
    near = rows["NEARUSDT"]
    assert near["mark_price"] == 5.0
    assert near["pnl_pct"] == pytest.approx(25.0)
    # BTCUSDT — SHORT: sold 0.1@31000, mark 30000 → +3.226% (down is good)
    btc = rows["BTCUSDT"]
    assert btc["mark_price"] == 30000.0
    assert btc["pnl_pct"] > 0  # short profits when price drops


def test_strategy_positions_works_without_cache(tmp_path: Path, monkeypatch) -> None:
    """``price_cache=None`` (paper / dashboard-only mode) keeps the endpoint
    fully functional — mark_price / pnl_pct are ``None``."""
    state = DashboardState()  # no price_cache wired
    from src.live.types import WALEvent
    fake = [WALEvent(
        ts="2026-05-21T00:00:00+00:00",
        event_type="order_filled",
        payload={"strategy_id": "x", "symbol": "ABC", "side": "buy",
                 "qty": "1", "fill_price": "100"},
    )]
    monkeypatch.setattr("src.dashboard.app.wal_replay", lambda _p: (fake, []))
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    assert resp.status_code == 200
    row = resp.json()["strategies"][0]
    assert row["mark_price"] is None
    assert row["pnl_pct"] is None


# ── /api/strategies/{sid}/positions/{sym}/close ───────────────────────


def test_manual_close_503_when_executor_not_wired() -> None:
    state = DashboardState()
    state.position_provider = lambda sid: [("NEARUSDT", 10.0)]
    # manual_close_executor=None (default)
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post("/api/strategies/cand-c/positions/NEARUSDT/close")
    assert resp.status_code == 503
    assert "manual_close_executor" in resp.json()["detail"]


def test_manual_close_404_when_no_position() -> None:
    state = DashboardState()
    state.position_provider = lambda sid: []
    state.manual_close_executor = lambda intents: None  # would be async but never reached
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post("/api/strategies/cand-c/positions/NEARUSDT/close")
    assert resp.status_code == 404


def test_manual_close_long_submits_sell_intent() -> None:
    state = DashboardState()
    state.position_provider = lambda sid: [("NEARUSDT", 10.0)]
    submitted_intents = []

    async def fake_executor(intents):
        submitted_intents.extend(intents)
        return {"submitted": len(intents)}

    state.manual_close_executor = fake_executor
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post(
            "/api/strategies/cand-c/positions/NEARUSDT/close",
            json={"qty": "all"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["side"] == "sell"
    assert body["submitted_qty"] == 10.0
    assert len(submitted_intents) == 1
    intent = submitted_intents[0]
    assert intent.strategy_id == "cand-c"
    assert intent.symbol == "NEARUSDT"
    assert intent.side == "sell"
    assert intent.qty == 10.0
    assert intent.reason == "manual_close_from_dashboard"
    assert intent.reduce_only is True


def test_manual_close_short_submits_buy_intent() -> None:
    """NET SHORT (held<0) closes via BUY (cover)."""
    state = DashboardState()
    state.position_provider = lambda sid: [("BTCUSDT", -0.5)]
    submitted = []

    async def fake_executor(intents):
        submitted.extend(intents)

    state.manual_close_executor = fake_executor
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post(
            "/api/strategies/momo/positions/BTCUSDT/close",
            json={"qty": "all"},
        )
    assert resp.status_code == 200
    assert resp.json()["side"] == "buy"
    assert submitted[0].side == "buy"
    assert submitted[0].qty == 0.5
    assert submitted[0].reduce_only is True


def test_manual_close_partial_qty() -> None:
    state = DashboardState()
    state.position_provider = lambda sid: [("NEARUSDT", 10.0)]
    submitted = []

    async def fake_executor(intents):
        submitted.extend(intents)

    state.manual_close_executor = fake_executor
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post(
            "/api/strategies/cand-c/positions/NEARUSDT/close",
            json={"qty": 3.5},
        )
    assert resp.status_code == 200
    assert resp.json()["submitted_qty"] == 3.5
    assert submitted[0].qty == 3.5


def test_manual_close_rejects_overflow_qty() -> None:
    state = DashboardState()
    state.position_provider = lambda sid: [("NEARUSDT", 10.0)]
    state.manual_close_executor = lambda intents: None
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post(
            "/api/strategies/cand-c/positions/NEARUSDT/close",
            json={"qty": 999.0},
        )
    assert resp.status_code == 400
    assert "exceeds" in resp.json()["detail"]


def test_manual_close_rejects_bad_qty_payload() -> None:
    state = DashboardState()
    state.position_provider = lambda sid: [("NEARUSDT", 10.0)]
    state.manual_close_executor = lambda intents: None
    app = create_app(state)
    with TestClient(app) as client:
        resp = client.post(
            "/api/strategies/cand-c/positions/NEARUSDT/close",
            json={"qty": "not a number"},
        )
    assert resp.status_code == 400
