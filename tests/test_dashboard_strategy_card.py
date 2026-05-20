"""Strategy catalog card — venue badges + truthful enabled state (2026-05-20).

Background: the original card (#178/#180) hardcoded ``enabled = item.get(
"enabled", True)`` so commented-out strategies in production.yaml and
``status: rejected`` specs all showed as ON. Also no visual indicator of
which market (KIS vs Binance) a strategy runs on — only a comma-joined
instruments string.

This module covers the new truthful derivation + venue chip rendering.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.dashboard.app import DashboardState, _classify_venues, create_app


# ---------------------------------------------------------------------------
# _classify_venues — unit
# ---------------------------------------------------------------------------

class TestClassifyVenues:
    def test_empty(self):
        assert _classify_venues([]) == []
        assert _classify_venues(None) == []

    def test_krx_universe(self):
        assert _classify_venues(["KRX_UNIVERSE"]) == ["kis"]

    def test_kis_stock_code_6digit(self):
        assert _classify_venues(["005930"]) == ["kis"]

    def test_kospi_kosdaq(self):
        assert _classify_venues(["KOSPI_200"]) == ["kis"]
        assert _classify_venues(["KOSDAQ_150"]) == ["kis"]

    def test_binance_universe(self):
        assert _classify_venues(["BINANCE_USDT_PERP_UNIVERSE"]) == ["binance"]

    def test_binance_concrete_symbol(self):
        assert _classify_venues(["BTCUSDT"]) == ["binance"]
        assert _classify_venues(["NEARUSDT", "SOLUSDT"]) == ["binance"]

    def test_mixed_dual_market(self):
        # live-breakout-with-atr-stop spec lists both
        out = _classify_venues(["KRX_UNIVERSE", "BINANCE_USDT_PERP_UNIVERSE"])
        assert out == ["binance", "kis"]   # sorted

    def test_unknown_instrument_skipped(self):
        assert _classify_venues(["WTF_FOO"]) == []

    def test_lowercase_input_ok(self):
        assert _classify_venues(["btcusdt"]) == ["binance"]
        assert _classify_venues(["krx_universe"]) == ["kis"]


# ---------------------------------------------------------------------------
# /api/strategies — truthful `enabled` derivation
# ---------------------------------------------------------------------------

def _write_spec(d: Path, sid: str, status: str = "live",
                instruments: list[str] | None = None) -> None:
    d.mkdir(parents=True, exist_ok=True)
    insts = instruments if instruments is not None else ["BTCUSDT"]
    (d / f"{sid}.md").write_text(
        "---\n"
        f"type: strategy\nid: {sid}\nname: {sid.title()}\nstatus: {status}\n"
        f"instruments: {insts}\n"
        "timeframe: 1m\nowner: tester\ncreated: 2026-01-01\n"
        "---\n", encoding="utf-8",
    )


def _write_prod_yaml(p: Path, active_ids: list[str],
                      commented_ids: list[str] | None = None) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["strategies:"]
    for sid in active_ids:
        lines.append(f"  - id: {sid}")
        lines.append(f"    class: dummy.{sid}.Dummy")
        lines.append("    kwargs: {}")
    for sid in (commented_ids or []):
        lines.append(f"  # - id: {sid}")
        lines.append(f"  #   class: dummy.{sid}.Dummy")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture
def catalog_setup(tmp_path: Path):
    specs = tmp_path / "docs" / "specs" / "strategies"
    prod = tmp_path / "configs" / "orchestrator" / "production.yaml"
    return specs, prod


def _make_client(specs_dir: Path, prod_yaml: Path, orch=None) -> TestClient:
    state = DashboardState()
    state.specs_dir = specs_dir
    state.production_yaml_path = prod_yaml
    state.orchestrator = orch
    return TestClient(create_app(state))


class TestEnrichedCatalogEnabled:
    def test_rejected_spec_forces_off(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_rejected", status="rejected")
        _write_prod_yaml(prod, active_ids=["x_rejected"])  # config active, but spec rejected
        items = _make_client(specs, prod).get("/api/strategies").json()
        item = next(it for it in items if it["id"] == "x_rejected")
        assert item["enabled"] is False
        assert item["disabled_reason"] == "rejected"
        assert item["toggle_disabled"] is True

    def test_commented_in_production_yaml_shows_off(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_commented", status="backtest")
        _write_prod_yaml(prod, active_ids=[], commented_ids=["x_commented"])
        items = _make_client(specs, prod).get("/api/strategies").json()
        item = next(it for it in items if it["id"] == "x_commented")
        assert item["enabled"] is False
        assert item["disabled_reason"] == "commented"
        assert item["toggle_disabled"] is True
        assert item["production_status"] == "commented"

    def test_absent_from_production_yaml_shows_off(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_absent", status="backtest")
        _write_prod_yaml(prod, active_ids=[])  # spec exists, not in yaml
        items = _make_client(specs, prod).get("/api/strategies").json()
        item = next(it for it in items if it["id"] == "x_absent")
        assert item["enabled"] is False
        assert item["disabled_reason"] == "absent"
        assert item["toggle_disabled"] is True

    def test_active_in_yaml_no_runtime_shows_on_readonly(self, catalog_setup):
        # 표준 dashboard-only 모드: production.yaml active 라면 ON 표시,
        # orch 미연결이라 토글은 read-only (disabled).
        specs, prod = catalog_setup
        _write_spec(specs, "x_active", status="backtest")
        _write_prod_yaml(prod, active_ids=["x_active"])
        items = _make_client(specs, prod, orch=None).get("/api/strategies").json()
        item = next(it for it in items if it["id"] == "x_active")
        assert item["enabled"] is True
        assert item["toggle_disabled"] is True
        assert item["disabled_reason"] == "no-runtime"

    def test_active_with_runtime_orch_wins(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_active", status="backtest")
        _write_prod_yaml(prod, active_ids=["x_active"])

        class _FakeOrch:
            strategies = {"x_active": object()}
            def is_enabled(self, sid: str) -> bool:
                return False  # runtime disabled, overrides config-active

        items = _make_client(specs, prod, orch=_FakeOrch()).get("/api/strategies").json()
        item = next(it for it in items if it["id"] == "x_active")
        assert item["enabled"] is False        # orch wins
        assert item["toggle_disabled"] is False  # registered → actionable


# ---------------------------------------------------------------------------
# Strategy card HTML — venue chips + disabled toggle
# ---------------------------------------------------------------------------

class TestStrategyCardHTML:
    def test_venue_chip_kis_for_krx_universe(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_kis", instruments=["KRX_UNIVERSE"])
        _write_prod_yaml(prod, active_ids=["x_kis"])
        body = _make_client(specs, prod).get("/strategies").text
        assert 'data-venue="kis"' in body or "venue-kis" in body

    def test_venue_chip_binance_for_usdt_symbol(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_bn", instruments=["BTCUSDT"])
        _write_prod_yaml(prod, active_ids=["x_bn"])
        body = _make_client(specs, prod).get("/strategies").text
        assert 'data-venue="binance"' in body or "venue-binance" in body

    def test_dual_market_renders_both_chips(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_dual",
                    instruments=["KRX_UNIVERSE", "BINANCE_USDT_PERP_UNIVERSE"])
        _write_prod_yaml(prod, active_ids=["x_dual"])
        body = _make_client(specs, prod).get("/strategies").text
        assert ("venue-kis" in body or 'data-venue="kis"' in body)
        assert ("venue-binance" in body or 'data-venue="binance"' in body)

    def test_toggle_disabled_attr_for_rejected(self, catalog_setup):
        specs, prod = catalog_setup
        _write_spec(specs, "x_rej", status="rejected")
        _write_prod_yaml(prod, active_ids=["x_rej"])
        body = _make_client(specs, prod).get("/strategies").text
        # find the input row for x_rej and check it has disabled attribute
        assert 'data-strategy-id="x_rej"' in body
        # Disabled HTML input attribute should be present near the toggle.
        # Strict: the strat-toggle input for x_rej is marked disabled.
        idx = body.find('data-strategy-id="x_rej"')
        # search a window of ±400 chars around the toggle anchor
        window = body[max(0, idx - 200): idx + 400]
        assert "disabled" in window.lower()
