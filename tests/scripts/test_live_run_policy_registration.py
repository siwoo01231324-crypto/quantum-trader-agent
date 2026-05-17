"""#238 — `_register_exit_policies` covers single-ticker strategies too.

Root incident: momo-btc-v2 (single-ticker, NOT a LiveScannerMixin) opened a
naked -1 BTC short with ZERO auto-stop because the policy-registration loop
in scripts/live_run.py skipped every strategy where
`is_live_scanner` was falsy. The helper now ALSO registers a stop/TP policy
for non-live-scanner strategies that declare `stop_loss_pct` /
`take_profit_pct` instance/class attrs (source: production.yaml kwargs).

A strategy that is neither a live-scanner nor declares stop/TP is still
skipped (we don't invent thresholds).
"""
from __future__ import annotations

import importlib
import logging
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))


@pytest.fixture
def live_run():
    if "live_run" in sys.modules:
        return importlib.reload(sys.modules["live_run"])
    return importlib.import_module("live_run")


class _LiveScanner:
    is_live_scanner = True
    stop_loss_pct = 0.02
    take_profit_pct = 0.05
    trailing_stop_pct = 0.01


class _MomoLike:
    """Single-ticker (NOT live-scanner) declaring stop/TP via __init__ kwargs."""

    is_live_scanner = False

    def __init__(self) -> None:
        self.stop_loss_pct = 0.03
        self.take_profit_pct = 0.06
        self.trailing_stop_pct = None


class _PlainStrategy:
    """Neither live-scanner nor stop/TP declaring → must be skipped."""

    is_live_scanner = False


class _FakeRiskMgr:
    def __init__(self) -> None:
        self.registered: dict[str, dict] = {}

    def register_strategy_policy(
        self, sid, *, stop_loss_pct, take_profit_pct, trailing_stop_pct=None
    ) -> None:
        self.registered[sid] = {
            "stop_loss_pct": stop_loss_pct,
            "take_profit_pct": take_profit_pct,
            "trailing_stop_pct": trailing_stop_pct,
        }


class _FakeOrch:
    def __init__(self, strategies: dict) -> None:
        self.strategies = strategies


def test_helper_registers_live_scanner_and_single_ticker(live_run):
    risk = _FakeRiskMgr()
    orch = _FakeOrch({
        "live_rsi": _LiveScanner(),
        "momo-btc-v2": _MomoLike(),
        "breakout-donchian": _PlainStrategy(),
    })
    count = live_run._register_exit_policies(
        orch, risk, logging.getLogger("test")
    )
    # live-scanner + momo registered, plain skipped.
    assert count == 2
    assert set(risk.registered) == {"live_rsi", "momo-btc-v2"}
    assert risk.registered["live_rsi"]["stop_loss_pct"] == 0.02
    assert risk.registered["live_rsi"]["trailing_stop_pct"] == 0.01
    assert risk.registered["momo-btc-v2"]["stop_loss_pct"] == 0.03
    assert risk.registered["momo-btc-v2"]["take_profit_pct"] == 0.06
    assert risk.registered["momo-btc-v2"]["trailing_stop_pct"] is None


def test_plain_strategy_without_thresholds_skipped(live_run):
    risk = _FakeRiskMgr()
    orch = _FakeOrch({"breakout-donchian": _PlainStrategy()})
    count = live_run._register_exit_policies(
        orch, risk, logging.getLogger("test")
    )
    assert count == 0
    assert risk.registered == {}


def test_partial_thresholds_single_ticker_skipped(live_run):
    """Only stop_loss but no take_profit → cannot build a StopTpPolicy → skip."""
    class _HalfMomo:
        is_live_scanner = False
        stop_loss_pct = 0.03  # take_profit_pct absent

    risk = _FakeRiskMgr()
    orch = _FakeOrch({"half": _HalfMomo()})
    count = live_run._register_exit_policies(
        orch, risk, logging.getLogger("test")
    )
    assert count == 0
    assert risk.registered == {}
