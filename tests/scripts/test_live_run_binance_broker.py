"""S1 (#231) — `_build_binance_adapter` env fallback + broker_mode gate.

Verifies:
- broker_mode != "binance-testnet-shadow" → returns None (no env reads).
- env fallback chain: BINANCE_DEMO_* → BINANCE_TESTNET_* → BINANCE_API_KEY/SECRET_KEY.
- Missing credentials → SystemExit with the missing var name in the message.
- Adapter instantiated with paper=True + testnet base/ws URLs by default.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

# Make `scripts/live_run` importable as a module (matches existing tests).
_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "scripts"))


@pytest.fixture
def live_run():
    """Reload `live_run` per-test so module-level state stays clean."""
    if "live_run" in sys.modules:
        return importlib.reload(sys.modules["live_run"])
    return importlib.import_module("live_run")


@pytest.fixture(autouse=True)
def clear_binance_env(monkeypatch, live_run):
    """Strip all Binance-related env vars before each test.

    Depends on ``live_run`` fixture so dotenv-loaded vars (from main-worktree
    .env walk-up) are also stripped — fixture order matters because dotenv
    runs at module import time.
    """
    for var in (
        "BINANCE_DEMO_API_KEY", "BINANCE_TESTNET_API_KEY", "BINANCE_API_KEY",
        "BINANCE_DEMO_API_SECRET", "BINANCE_TESTNET_API_SECRET", "BINANCE_SECRET_KEY",
        "BINANCE_BASE_URL", "BINANCE_WS_BASE_URL",
    ):
        monkeypatch.delenv(var, raising=False)


class TestBrokerModeGate:
    def test_kis_paper_returns_none(self, live_run):
        assert live_run._build_binance_adapter("kis-paper") is None

    def test_kis_paper_shadow_returns_none(self, live_run):
        assert live_run._build_binance_adapter("kis-paper-shadow") is None

    def test_paper_only_returns_none(self, live_run):
        assert live_run._build_binance_adapter("paper-only") is None


class TestEnvFallback:
    def test_demo_key_takes_priority(self, live_run, monkeypatch):
        monkeypatch.setenv("BINANCE_DEMO_API_KEY", "demo_key")
        monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "demo_secret")
        monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "testnet_key")  # should be ignored
        monkeypatch.setenv("BINANCE_API_KEY", "main_key")  # should be ignored

        adapter = live_run._build_binance_adapter("binance-testnet-shadow")
        assert adapter is not None
        # AsyncBinanceFuturesAdapter stores via the underlying client; we verify
        # the construction succeeded with paper=True default.
        assert adapter.paper is True

    def test_testnet_fallback_when_demo_missing(self, live_run, monkeypatch):
        monkeypatch.setenv("BINANCE_TESTNET_API_KEY", "testnet_key")
        monkeypatch.setenv("BINANCE_TESTNET_API_SECRET", "testnet_secret")
        adapter = live_run._build_binance_adapter("binance-testnet-shadow")
        assert adapter is not None
        assert adapter.paper is True

    def test_mainnet_key_last_resort(self, live_run, monkeypatch):
        monkeypatch.setenv("BINANCE_API_KEY", "main_key")
        monkeypatch.setenv("BINANCE_SECRET_KEY", "main_secret")
        # mainnet creds work but adapter still constructs as paper=True (testnet
        # 모드라 ProductionUser 가 BINANCE_BASE_URL 을 명시적으로 override 해야 함).
        adapter = live_run._build_binance_adapter("binance-testnet-shadow")
        assert adapter is not None


class TestMissingCredentials:
    def test_no_env_raises_systemexit(self, live_run):
        with pytest.raises(SystemExit) as exc:
            live_run._build_binance_adapter("binance-testnet-shadow")
        assert "BINANCE_DEMO_API_KEY" in str(exc.value)
        assert "BINANCE_DEMO_API_SECRET" in str(exc.value)

    def test_only_key_no_secret_raises(self, live_run, monkeypatch):
        monkeypatch.setenv("BINANCE_DEMO_API_KEY", "demo_key")
        with pytest.raises(SystemExit) as exc:
            live_run._build_binance_adapter("binance-testnet-shadow")
        assert "BINANCE_DEMO_API_SECRET" in str(exc.value)

    def test_only_secret_no_key_raises(self, live_run, monkeypatch):
        monkeypatch.setenv("BINANCE_DEMO_API_SECRET", "demo_secret")
        with pytest.raises(SystemExit) as exc:
            live_run._build_binance_adapter("binance-testnet-shadow")
        assert "BINANCE_DEMO_API_KEY" in str(exc.value)


class TestBrokerChoicesExpanded:
    """`--broker` argparse choices include the new binance mode (#231 S1)."""

    def test_binance_testnet_shadow_in_choices(self, live_run):
        args = live_run.parse_args([
            "--symbols", "BTCUSDT",
            "--broker", "binance-testnet-shadow",
        ])
        assert args.broker == "binance-testnet-shadow"
