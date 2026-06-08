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


class _FakeBinancePnLProvider:
    """``fetch_binance_pnl`` 만 구현한 account_info_provider 테스트 더블.

    ``result`` 가 Exception 이면 호출 시 raise — `/api/pnl` 의 예외 흡수 검증용.
    """

    def __init__(self, result):
        self._result = result

    def fetch_binance_pnl(self):
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


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


def test_api_pnl_binance_from_exchange_income(specs_dir, monkeypatch):
    """2026-05-23: Binance venue = account_info_provider.fetch_binance_pnl
    (거래소 income 원장). top-level 스칼라 = Binance venue 값.
    """
    monkeypatch.setattr("src.dashboard.app.discover_wal_files", lambda _d: [])
    s = DashboardState()
    s.specs_dir = specs_dir
    # #395 — Bitget = 주 운영 venue (top-level 스칼라). Binance 는 by_venue 보존.
    class _FakeBnBgPnLProvider:
        def fetch_binance_pnl(self):
            return {"ok": True, "daily": 9.3, "monthly": 14.3, "asset": "USDT"}

        def fetch_bitget_pnl(self):
            return {"ok": True, "daily": -5.0, "monthly": -8.0, "asset": "USDT"}

    s.account_info_provider = _FakeBnBgPnLProvider()
    body = TestClient(create_app(s)).get("/api/pnl").json()
    # top-level 스칼라 = Bitget(청산 netProfit 원장)
    assert body["daily"] == pytest.approx(-5.0)
    assert body["monthly"] == pytest.approx(-8.0)
    assert body["daily_by_venue"]["bitget"] == pytest.approx(-5.0)
    assert body["monthly_by_venue"]["bitget"] == pytest.approx(-8.0)
    assert body["bitget_source"] == "history_position_netprofit"
    # Binance 도 by_venue 에 보존 (참고용)
    assert body["daily_by_venue"]["binance"] == pytest.approx(9.3)
    assert body["binance_source"] == "exchange_income"


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


def test_api_pnl_binance_null_without_provider(specs_dir: Path, monkeypatch):
    """account_info_provider 미연결 → Binance venue null, 스칼라 0.0 (no 500)."""
    monkeypatch.setattr("src.dashboard.app.discover_wal_files", lambda _d: [])
    s = DashboardState()
    s.specs_dir = specs_dir
    body = TestClient(create_app(s)).get("/api/pnl").json()
    assert body["daily_by_venue"]["binance"] is None
    assert body["monthly_by_venue"]["binance"] is None
    assert body["daily"] == 0.0
    assert body["monthly"] == 0.0


def test_api_pnl_survives_provider_error(specs_dir: Path, monkeypatch):
    """fetch_binance_pnl 가 raise 하거나 ok=False → Binance null, 절대 500 아님."""
    monkeypatch.setattr("src.dashboard.app.discover_wal_files", lambda _d: [])
    for result in ({"ok": False, "error": "creds 누락"}, RuntimeError("boom")):
        s = DashboardState()
        s.specs_dir = specs_dir
        s.account_info_provider = _FakeBinancePnLProvider(result)
        resp = TestClient(create_app(s)).get("/api/pnl")
        assert resp.status_code == 200
        assert resp.json()["daily_by_venue"]["binance"] is None


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


# ── 거래소 income 원장 집계 (aggregate_income_pnl) ──────────────────────────

def test_aggregate_income_pnl_net_includes_fees_and_funding():
    """NET = REALIZED_PNL + COMMISSION + FUNDING_FEE. TRANSFER 등은 제외."""
    from src.brokers.binance.schemas import IncomeItem
    from src.dashboard.account_info import aggregate_income_pnl

    def _inc(itype, income, time):
        return IncomeItem(
            symbol="BTCUSDT", incomeType=itype, income=income,
            asset="USDT", time=time,
        )

    incomes = [
        _inc("REALIZED_PNL", "10.0", 2500),   # 오늘
        _inc("COMMISSION", "-0.5", 2500),     # 오늘
        _inc("FUNDING_FEE", "-0.2", 2500),    # 오늘
        _inc("REALIZED_PNL", "5.0", 1000),    # 이전 (월간만)
        _inc("TRANSFER", "100.0", 2500),      # PnL 아님 — 제외돼야 함
    ]
    daily, monthly = aggregate_income_pnl(incomes, today_start_ms=2000)
    assert daily == pytest.approx(9.3)     # 10 - 0.5 - 0.2
    assert monthly == pytest.approx(14.3)  # 9.3 + 5.0


def test_aggregate_income_pnl_empty():
    from src.dashboard.account_info import aggregate_income_pnl

    assert aggregate_income_pnl([], today_start_ms=0) == (0.0, 0.0)


def test_aggregate_income_pnl_daily_boundary():
    """today_start_ms 정각 레코드는 '오늘' 에 포함 (>= 경계)."""
    from src.brokers.binance.schemas import IncomeItem
    from src.dashboard.account_info import aggregate_income_pnl

    incomes = [
        IncomeItem(incomeType="REALIZED_PNL", income="1.0", time=999),
        IncomeItem(incomeType="REALIZED_PNL", income="2.0", time=1000),
        IncomeItem(incomeType="REALIZED_PNL", income="3.0", time=1001),
    ]
    daily, monthly = aggregate_income_pnl(incomes, today_start_ms=1000)
    assert daily == pytest.approx(5.0)    # 2 + 3 (999 는 어제)
    assert monthly == pytest.approx(6.0)  # 1 + 2 + 3
