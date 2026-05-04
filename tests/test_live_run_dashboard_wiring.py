"""Tests for #181 dashboard wiring in scripts/live_run.py (#177)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ is not a package — load via sys.path.
_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def test_parse_args_exposes_dashboard_and_feed_flags():
    from scripts.live_run import parse_args
    args = parse_args([
        "--symbols", "005930",
        "--broker", "paper-only",
        "--feed", "mock",
        "--dashboard-port", "0",
        "--mock-bars", "5",
        "--max-iterations", "5",
    ])
    assert args.feed == "mock"
    assert args.dashboard_port == 0
    assert args.mock_bars == 5


def test_build_config_propagates_feed_mode():
    from scripts.live_run import _build_config, parse_args
    args = parse_args([
        "--symbols", "005930",
        "--broker", "paper-only",
        "--feed", "binance",
        "--dashboard-port", "0",
    ])
    cfg = _build_config(args)
    assert cfg.feed_mode == "binance"
    assert cfg.symbols == ["005930"]


def test_build_mock_ticks_returns_n_bars_per_symbol():
    from scripts.live_run import _build_mock_ticks
    ticks = _build_mock_ticks(["005930", "035720"], n_bars=4)
    assert len(ticks) == 8  # 4 bars × 2 symbols
    assert {t.symbol for t in ticks} == {"005930", "035720"}


def test_build_kis_client_returns_none_for_non_krx():
    from scripts.live_run import _build_kis_client
    assert _build_kis_client("binance", ["BTCUSDT"]) is None
    assert _build_kis_client("mock", ["005930"]) is None


def test_build_kis_client_raises_without_env(monkeypatch):
    """auto mode + KRX symbol + no env vars → SystemExit (loud failure)."""
    monkeypatch.delenv("HANTOO_FAKE_API_KEY", raising=False)
    monkeypatch.delenv("HANTOO_FAKE_SECRET_API_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    from scripts.live_run import _build_kis_client
    with pytest.raises(SystemExit, match="HANTOO_FAKE_API_KEY"):
        _build_kis_client("auto", ["005930"])
