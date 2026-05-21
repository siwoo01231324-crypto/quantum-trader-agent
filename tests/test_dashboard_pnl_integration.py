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
    client: TestClient, aggregator: PnLAggregator,
):
    """alpha realises +10, beta loses -5 → /api/pnl realtime = 5."""
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
    assert body["realtime"] == 5.0
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


class _FakeBinanceProvider:
    """fetch_binance() stub for unrealized augment tests."""

    def __init__(self, total_unrealized: float, ok: bool = True) -> None:
        self._upnl = total_unrealized
        self._ok = ok

    def fetch_binance(self) -> dict:
        return {"ok": self._ok, "total_unrealized_pnl": self._upnl, "positions": []}


def test_api_pnl_augments_realtime_with_binance_unrealized() -> None:
    """2026-05-22: /api/pnl 의 realtime = aggregator 의 realized cum + Binance
    broker unrealized. 사용자 보고 "실시간이 월간과 같다" fix.
    """
    state = DashboardState()
    agg = PnLAggregator()
    # 어제 실현 -96.45 시뮬.
    agg._cum_realized = -96.45
    agg._cum_by_venue = {"binance": -96.45}
    state.pnl_aggregator = agg
    state.account_info_provider = _FakeBinanceProvider(total_unrealized=-14.0)

    app = create_app(state)
    with TestClient(app) as test_client:
        d = test_client.get("/api/pnl").json()
    # 실시간 = -96.45 + (-14) = -110.45.
    assert d["realtime"] == pytest.approx(-110.45)
    # Venue 분리도 binance row 가 합산.
    assert d["realtime_by_venue"]["binance"] == pytest.approx(-110.45)
    # 별도 expose 필드.
    assert d["unrealized_by_venue"] == {"binance": -14.0}
    # 일간/월간은 영향 X (window-bounded realized only).
    assert d["daily"] == pytest.approx(agg.daily)
    assert d["monthly"] == pytest.approx(agg.monthly)


def test_api_pnl_without_provider_realtime_is_realized_only() -> None:
    """Provider 미주입 (paper 모드) → unrealized 0 → realtime 변동 X (기존 동작)."""
    state = DashboardState()
    agg = PnLAggregator()
    agg._cum_realized = -50.0
    agg._cum_by_venue = {"binance": -50.0}
    state.pnl_aggregator = agg
    # account_info_provider 미주입.

    app = create_app(state)
    with TestClient(app) as test_client:
        d = test_client.get("/api/pnl").json()
    assert d["realtime"] == pytest.approx(-50.0)
    assert d["unrealized_by_venue"] == {}
    assert d["realtime_by_venue"]["binance"] == pytest.approx(-50.0)


def test_api_pnl_provider_failure_does_not_500() -> None:
    """Broker 호출 실패 시 augment skip — endpoint 는 realized only 로 200 응답."""

    class _RaisingProvider:
        def fetch_binance(self):
            raise RuntimeError("simulated broker error")

    state = DashboardState()
    agg = PnLAggregator()
    agg._cum_realized = -10.0
    state.pnl_aggregator = agg
    state.account_info_provider = _RaisingProvider()

    app = create_app(state)
    with TestClient(app) as test_client:
        resp = test_client.get("/api/pnl")
    assert resp.status_code == 200
    assert resp.json()["realtime"] == pytest.approx(-10.0)


def test_empty_aggregator_returns_zeros(client: TestClient):
    body = client.get("/api/pnl").json()
    assert body["realtime"] == 0.0
    assert body["daily"] == 0.0
    assert body["monthly"] == 0.0
    assert body["by_strategy"] == {}


def test_no_aggregator_falls_back_to_state_defaults(specs_dir: Path):
    """Without `pnl_aggregator` injected, /api/pnl reads state.pnl_* (defaults
    or whatever was set externally). Backwards-compat path."""
    s = DashboardState()
    s.specs_dir = specs_dir
    s.pnl_realtime = 1234.5
    s.pnl_daily = 50.0
    s.pnl_monthly = 200.0
    c = TestClient(create_app(s))

    body = c.get("/api/pnl").json()
    assert body["realtime"] == 1234.5
    assert body["daily"] == 50.0
    assert body["monthly"] == 200.0
    assert body["by_strategy"] == {}


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
