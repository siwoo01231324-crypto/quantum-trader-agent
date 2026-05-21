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


# ── 2026-05-22 — Binance ground-truth unrealized_pnl per row ──────────────


class _FakeBinanceProvider:
    """fetch_binance() 결과를 고정 반환하는 stub.

    `state.account_info_provider` 가 expose 하는 인터페이스 — sync 메서드.
    endpoint 는 `asyncio.to_thread` 로 호출하므로 sync 그대로 OK.
    """

    def __init__(self, positions: list[dict]) -> None:
        self._positions = positions

    def fetch_binance(self) -> dict:
        return {
            "ok": True,
            "positions": self._positions,
            "total_unrealized_pnl": sum(
                p["unrealized_pnl"] for p in self._positions
            ),
        }


def _fake_fill(strategy_id: str, symbol: str, side: str, qty: str, price: str):
    from src.live.types import WALEvent
    return WALEvent(
        ts="2026-05-22T00:00:00+00:00",
        event_type="order_filled",
        payload={
            "strategy_id": strategy_id, "symbol": symbol, "side": side,
            "qty": qty, "fill_price": price,
        },
    )


def test_unrealized_pnl_from_binance_ground_truth_single_holder(
    tmp_path: Path, monkeypatch,
) -> None:
    """Binance 가 NEARUSDT 에 -14 USDT unrealized 라고 보고하면, 그 symbol 을
    단일 strategy 가 보유 중이라면 그대로 row 의 unrealized_pnl 에 들어가야.
    """
    state = DashboardState()
    state.account_info_provider = _FakeBinanceProvider([
        {"symbol": "NEARUSDT", "amt": 135.0, "unrealized_pnl": -14.0},
    ])
    fake = [_fake_fill("cand-c-bb", "NEARUSDT", "buy", "135", "1.75")]
    monkeypatch.setattr("src.dashboard.app.wal_replay", lambda _p: (fake, []))
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    assert resp.status_code == 200
    row = resp.json()["strategies"][0]
    assert row["symbol"] == "NEARUSDT"
    assert row["unrealized_pnl"] == pytest.approx(-14.0)


def test_unrealized_pnl_prorated_when_multi_strategy_share_symbol(
    tmp_path: Path, monkeypatch,
) -> None:
    """Binance 가 NEARUSDT 에 -10 USDT 보고하고 strategy 2개가 100 / 50 NEAR
    보유 중이면 share = |net_qty|/total → strat_a -6.667, strat_b -3.333.
    """
    state = DashboardState()
    state.account_info_provider = _FakeBinanceProvider([
        {"symbol": "NEARUSDT", "amt": 150.0, "unrealized_pnl": -10.0},
    ])
    fake = [
        _fake_fill("strat_a", "NEARUSDT", "buy", "100", "1.0"),
        _fake_fill("strat_b", "NEARUSDT", "buy", "50", "1.0"),
    ]
    monkeypatch.setattr("src.dashboard.app.wal_replay", lambda _p: (fake, []))
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    rows = {r["strategy_id"]: r for r in resp.json()["strategies"]}
    assert rows["strat_a"]["unrealized_pnl"] == pytest.approx(-6.6667, rel=1e-3)
    assert rows["strat_b"]["unrealized_pnl"] == pytest.approx(-3.3333, rel=1e-3)


def test_unrealized_pnl_none_when_broker_provider_absent(
    tmp_path: Path, monkeypatch,
) -> None:
    """account_info_provider 미주입 (paper 모드 등) → unrealized_pnl=None.

    기존 endpoint 동작 (pnl_pct / realized_pnl / qty 집계) 는 변경 X.
    """
    state = DashboardState()  # no account_info_provider
    fake = [_fake_fill("x", "ABC", "buy", "1", "100")]
    monkeypatch.setattr("src.dashboard.app.wal_replay", lambda _p: (fake, []))
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    row = resp.json()["strategies"][0]
    assert row["unrealized_pnl"] is None


def test_unrealized_pnl_none_when_broker_does_not_have_symbol(
    tmp_path: Path, monkeypatch,
) -> None:
    """Broker positions 에 해당 symbol 이 없으면 (= 실제 broker flat) row 의
    unrealized_pnl=None — phantom store 신호.
    """
    state = DashboardState()
    state.account_info_provider = _FakeBinanceProvider([])  # broker flat
    fake = [_fake_fill("strat_x", "NEARUSDT", "buy", "100", "1.0")]
    monkeypatch.setattr("src.dashboard.app.wal_replay", lambda _p: (fake, []))
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    row = resp.json()["strategies"][0]
    assert row["symbol"] == "NEARUSDT"
    assert row["unrealized_pnl"] is None
    # 기존 필드들 (pnl_pct/realized_pnl/qty) 은 본 fix 와 무관 — 변경 X.
    assert "net_qty" in row
    assert "pnl_pct" in row


def test_unrealized_pnl_provider_failure_does_not_500(
    tmp_path: Path, monkeypatch,
) -> None:
    """Broker fetch 예외 시 unrealized_pnl=None 으로 fallback. endpoint 는
    200 유지 — never 500 the dashboard.
    """
    class _RaisingProvider:
        def fetch_binance(self):
            raise RuntimeError("simulated broker error")

    state = DashboardState()
    state.account_info_provider = _RaisingProvider()
    fake = [_fake_fill("s", "NEARUSDT", "buy", "10", "1.0")]
    monkeypatch.setattr("src.dashboard.app.wal_replay", lambda _p: (fake, []))
    wal_path = tmp_path / "wal.jsonl"
    wal_path.write_text("")
    state.wal_path = wal_path

    app = create_app(state)
    with TestClient(app) as client:
        resp = client.get("/api/strategy_positions")
    assert resp.status_code == 200
    row = resp.json()["strategies"][0]
    assert row["unrealized_pnl"] is None


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
