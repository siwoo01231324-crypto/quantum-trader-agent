"""Integration: PnLAggregator + DashboardState + /api/pnl + per-card pnl_today (#194)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, create_app
from src.live.pnl_aggregator import PnLAggregator
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 5, 6, 14, 0, tzinfo=KST)


class _StubStrategy:
    def __init__(self, sid: str):
        self.strategy_id = sid

    def on_bar(self, ctx):
        return None


@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    d = tmp_path / "docs" / "specs" / "strategies"
    d.mkdir(parents=True)
    (d / "alpha.md").write_text(
        """---
type: strategy
id: alpha
name: Alpha
status: paper
instruments: [BTCUSDT]
timeframe: 4h
owner: tester
created: 2026-01-01
---
""",
        encoding="utf-8",
    )
    (d / "beta.md").write_text(
        """---
type: strategy
id: beta
name: Beta
status: paper
instruments: [005930]
timeframe: 1d
owner: tester
created: 2026-01-01
---
""",
        encoding="utf-8",
    )
    return d


@pytest.fixture
def aggregator() -> PnLAggregator:
    return PnLAggregator(kst_now=lambda: NOW)


@pytest.fixture
def state(specs_dir: Path, aggregator: PnLAggregator) -> DashboardState:
    s = DashboardState()
    s.specs_dir = specs_dir
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("alpha", _StubStrategy("alpha"))
    orch.register_strategy("beta", _StubStrategy("beta"))
    s.orchestrator = orch
    s.pnl_aggregator = aggregator
    return s


@pytest.fixture
def client(state: DashboardState) -> TestClient:
    return TestClient(create_app(state))


def test_api_pnl_reflects_aggregator(
    client: TestClient, aggregator: PnLAggregator, monkeypatch,
):
    """alpha realises +10, beta loses -5 → /api/pnl daily = 5.

    log_dir 격리 — endpoint 의 reconstruct 경로 대신 _pnl_view(aggregator)
    fallback 을 검증 (이 테스트는 aggregator 반영 확인용).
    """
    monkeypatch.setattr("src.dashboard.app.discover_wal_files", lambda _d: [])
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), ts=NOW,
    )
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"), ts=NOW,
    )
    aggregator.record_fill(
        strategy_id="beta", symbol="005930", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("5"), ts=NOW,
    )

    body = client.get("/api/pnl").json()
    assert body["daily"] == 5.0
    assert body["monthly"] == 5.0
    assert body["by_strategy"] == {"alpha": 10.0, "beta": -5.0}


def test_per_strategy_card_shows_pnl_today(
    client: TestClient, aggregator: PnLAggregator,
):
    """Each strategy card carries its own pnl_today."""
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"), ts=NOW,
    )
    aggregator.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("130"), fee=Decimal("0"), ts=NOW,
    )

    items = client.get("/api/strategies").json()
    alpha = next(it for it in items if it["id"] == "alpha")
    beta = next(it for it in items if it["id"] == "beta")
    assert alpha["pnl_today"] == 30.0
    assert beta["pnl_today"] == 0.0


def test_empty_aggregator_returns_zeros(client: TestClient, monkeypatch):
    monkeypatch.setattr("src.dashboard.app.discover_wal_files", lambda _d: [])
    body = client.get("/api/pnl").json()
    assert body["daily"] == 0.0
    assert body["monthly"] == 0.0
    assert body["by_strategy"] == {}


def test_no_aggregator_falls_back_to_state_defaults(specs_dir: Path, monkeypatch):
    """Without `pnl_aggregator`, /api/pnl reads state.pnl_* via _pnl_view
    fallback (log_dir 격리 시). Backwards-compat path."""
    monkeypatch.setattr("src.dashboard.app.discover_wal_files", lambda _d: [])
    s = DashboardState()
    s.specs_dir = specs_dir
    s.pnl_daily = 50.0
    s.pnl_monthly = 200.0
    c = TestClient(create_app(s))

    body = c.get("/api/pnl").json()
    assert body["daily"] == 50.0
    assert body["monthly"] == 200.0
    assert body["by_strategy"] == {}


def test_pnl_from_trades_aggregates_closed_by_exit_date():
    """★ 2026-05-22: _pnl_from_trades 가 closed round-trip 의 realized_pnl 을
    청산시각(KST) 기준 일/월 집계 — 거래내역과 동일 source 로 PnL 카드 통일.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from src.dashboard.app import _pnl_from_trades
    from src.live.trade_history import Trade

    now = datetime.now(ZoneInfo("Asia/Seoul"))

    def _trade(realized, exit_ts, status="closed", venue="binance"):
        return Trade(
            strategy_id="s", symbol="BTCUSDT", venue=venue, side="long",
            qty=1.0, entry_ts=now.isoformat(), entry_price=100.0,
            exit_ts=exit_ts, exit_price=110.0, realized_pnl=realized,
            holding_seconds=60.0, status=status,
        )

    trades = [
        _trade(48.0, now.isoformat()),                 # 오늘 closed (binance)
        _trade(7.0, now.isoformat(), venue="kis"),     # 오늘 closed (kis)
        _trade(99.0, None, status="open"),             # open — 제외
    ]
    v = _pnl_from_trades(trades)
    assert v["daily"] == pytest.approx(55.0)           # 48 + 7
    assert v["monthly"] == pytest.approx(55.0)         # 오늘 = 이번 달
    assert v["daily_by_venue"]["binance"] == pytest.approx(48.0)
    assert v["daily_by_venue"]["kis"] == pytest.approx(7.0)


def test_api_strategies_pnl_today_zero_when_aggregator_missing(
    specs_dir: Path,
):
    """No aggregator → cards still render, pnl_today = 0.0."""
    s = DashboardState()
    s.specs_dir = specs_dir
    c = TestClient(create_app(s))

    items = c.get("/api/strategies").json()
    for it in items:
        assert it["pnl_today"] == 0.0
