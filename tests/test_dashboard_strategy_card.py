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

from src.dashboard.app import (
    DashboardState, _classify_venues, _fmt_exit_pct, _fmt_timeframe, create_app,
)


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

    def test_enabled_strategies_sorted_above_disabled(self, catalog_setup):
        """카탈로그 응답 순서: 켜진 전략(enabled=True) 이 꺼진 전략보다 항상 앞."""
        specs, prod = catalog_setup
        _write_spec(specs, "a_off_rejected", status="rejected")
        _write_spec(specs, "b_off_absent", status="backtest")
        _write_spec(specs, "c_on_active", status="backtest")
        _write_spec(specs, "d_on_active", status="backtest")
        _write_prod_yaml(prod, active_ids=["a_off_rejected", "c_on_active", "d_on_active"])
        items = _make_client(specs, prod).get("/api/strategies").json()
        ids = [it["id"] for it in items]
        # a_off_rejected: rejected → OFF. b_off_absent: not in yaml → OFF.
        # c_on_active + d_on_active: yaml active + spec not rejected → ON (no-runtime).
        i_c = ids.index("c_on_active")
        i_d = ids.index("d_on_active")
        i_a = ids.index("a_off_rejected")
        i_b = ids.index("b_off_absent")
        assert max(i_c, i_d) < min(i_a, i_b), (
            "ON strategies must be sorted above OFF; got order: " + str(ids)
        )

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


# ---------------------------------------------------------------------------
# Timeframe Korean label + exit % chips (2026-05-20)
# ---------------------------------------------------------------------------

class TestFmtTimeframe:
    def test_known_labels(self):
        assert _fmt_timeframe("1m") == "1분봉"
        assert _fmt_timeframe("15m") == "15분봉"
        assert _fmt_timeframe("4h") == "4시간봉"
        assert _fmt_timeframe("1d") == "일봉"

    def test_unknown_falls_through_to_raw(self):
        assert _fmt_timeframe("3d") == "3d"  # 모르는 값은 원본

    def test_empty_or_none(self):
        assert _fmt_timeframe(None) == "—"
        assert _fmt_timeframe("") == "—"


class TestFmtExitPct:
    def test_stop_loss_minus_sign(self):
        assert _fmt_exit_pct(0.05, sign="-") == "-5.0%"
        assert _fmt_exit_pct(0.025, sign="-") == "-2.5%"

    def test_take_profit_plus_sign(self):
        assert _fmt_exit_pct(0.20, sign="+") == "+20.0%"
        assert _fmt_exit_pct(0.06, sign="+") == "+6.0%"

    def test_none_is_dash(self):
        assert _fmt_exit_pct(None, sign="-") == "—"
        assert _fmt_exit_pct(None, sign="+") == "—"


class TestStrategyCardExitRow:
    """카드에 봉/손절/익절/트레일링 % 가 시각화되는지."""

    def _write_spec_with_exits(self, d: Path, sid: str, *,
                                timeframe: str = "1m",
                                stop_loss_pct: float | None = None,
                                take_profit_pct: float | None = None,
                                trailing_stop_pct: float | None = None) -> None:
        d.mkdir(parents=True, exist_ok=True)
        lines = [
            "---",
            "type: strategy",
            f"id: {sid}",
            f"name: {sid.title()}",
            "status: backtest",
            "instruments: [BTCUSDT]",
            f"timeframe: {timeframe}",
            "owner: tester",
            "created: 2026-01-01",
        ]
        if stop_loss_pct is not None:
            lines.append(f"stop_loss_pct: {stop_loss_pct}")
        if take_profit_pct is not None:
            lines.append(f"take_profit_pct: {take_profit_pct}")
        if trailing_stop_pct is not None:
            lines.append(f"trailing_stop_pct: {trailing_stop_pct}")
        lines.append("---\n")
        (d / f"{sid}.md").write_text("\n".join(lines), encoding="utf-8")

    def test_card_shows_korean_timeframe_label(self, catalog_setup):
        specs, prod = catalog_setup
        self._write_spec_with_exits(specs, "x_15m", timeframe="15m")
        _write_prod_yaml(prod, active_ids=["x_15m"])
        body = _make_client(specs, prod).get("/strategies").text
        assert "15분봉" in body

    def test_card_shows_exit_pcts_when_set(self, catalog_setup):
        specs, prod = catalog_setup
        self._write_spec_with_exits(
            specs, "x_full", timeframe="1m",
            stop_loss_pct=0.05, take_profit_pct=0.20, trailing_stop_pct=0.04,
        )
        _write_prod_yaml(prod, active_ids=["x_full"])
        body = _make_client(specs, prod).get("/strategies").text
        assert "손절" in body and "-5.0%" in body
        assert "익절" in body and "+20.0%" in body
        assert "트레일링" in body and "-4.0%" in body

    def test_card_shows_dash_when_no_exit_rules(self, catalog_setup):
        # universe-scan / paradigm 외 전략은 fixed exit pct 가 null
        specs, prod = catalog_setup
        self._write_spec_with_exits(specs, "x_none", timeframe="1d")
        _write_prod_yaml(prod, active_ids=["x_none"])
        body = _make_client(specs, prod).get("/strategies").text
        # exit chips always render (labels + em-dash placeholders for null pcts)
        assert "일봉" in body
        assert "손절" in body and "익절" in body and "트레일링" in body
        # 3 exit chips × "—" placeholder (sl, tp, trail) — at minimum.
        assert body.count("strat-exit-chip") >= 4  # tf + sl + tp + trail
        assert "—" in body
