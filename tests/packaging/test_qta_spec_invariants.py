"""Static asserts that qta.spec ships the wiring needed by #177 + #181.

These tests parse `qta.spec` as text and check that production.yaml is bundled
and that dashboard/KIS feed modules are listed in hiddenimports. They are
intentionally string-level rather than running PyInstaller — that takes minutes
and lives in the EXE smoke step, not unit tests.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = _ROOT / "qta.spec"


@pytest.fixture(scope="module")
def spec_text() -> str:
    assert _SPEC.exists(), f"qta.spec missing at {_SPEC}"
    return _SPEC.read_text(encoding="utf-8")


def test_configs_directory_in_datas(spec_text):
    """The whole configs/ tree must be bundled so production.yaml ships with EXE."""
    assert '"configs", "configs"' in spec_text or '("configs", "configs")' in spec_text


def test_production_yaml_present_on_disk():
    yaml_path = _ROOT / "configs" / "orchestrator" / "production.yaml"
    assert yaml_path.exists(), "configs/orchestrator/production.yaml must exist for the bundler"
    body = yaml_path.read_text(encoding="utf-8")
    for sid in (
        "momo-btc-v2",
        "momo-vol-filtered",
        "meanrev-pairs",
        "breakout-donchian",
        "momo-kis-v1",
    ):
        assert sid in body, f"strategy_id {sid} missing from production.yaml"


@pytest.mark.parametrize("module_name", [
    "src.dashboard",
    "src.dashboard.app",
    "src.dashboard.timeline_broker",
    "src.dashboard.timeline_events",
    "src.live.feed_kis",
    "src.live.snapshot_builder",
    "src.portfolio.config_loader",
    "src.backtest.strategies.momo_btc_v2",
    "src.backtest.strategies.momo_vol_filtered",
    "src.backtest.strategies.meanrev_pairs",
    "src.backtest.strategies.breakout_donchian",
    "src.backtest.strategies.momo_kis_v1",
    "uvicorn",
    "fastapi",
    "starlette.websockets",
])
def test_hiddenimport_present(spec_text, module_name):
    assert f'"{module_name}"' in spec_text, (
        f"qta.spec hiddenimports must include {module_name} so PyInstaller "
        "doesn't strip it (#177 EXE wiring)."
    )
