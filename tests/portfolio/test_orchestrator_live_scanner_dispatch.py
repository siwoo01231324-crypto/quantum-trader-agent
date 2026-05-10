"""Per-symbol dispatch for live-scanner strategies (#227 S1).

Verifies that ``AsyncStrategyOrchestrator.run_bar`` iterates
``market_snapshot["ohlcv_history"]`` once per symbol when a strategy declares
``is_live_scanner=True`` (via ``LiveScannerMixin``), while keeping the legacy
single-dispatch path unchanged for cs_*/momo_* strategies.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


def _ohlcv(symbol: str, n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {
            "open": close,
            "high": close * 1.001,
            "low": close * 0.999,
            "close": close,
            "volume": np.full(n, 1000.0),
        },
        index=idx,
    )


class _AlwaysBuyLiveScanner(LiveScannerMixin):
    """Live-scanner that returns Signal(action='buy') for every symbol it sees."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def on_bar(self, ctx) -> Signal | None:
        snap = ctx["market_snapshot"]
        self.calls.append(snap["symbol"])
        return Signal(action="buy", size=0.05, reason=f"buy:{snap['symbol']}")


class _LegacyAlwaysBuy:
    """Non-live-scanner strategy — should receive a single dispatch per tick."""

    is_live_scanner: ClassVar[bool] = False  # explicit for clarity

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def on_bar(self, ctx) -> Signal | None:
        snap = ctx["market_snapshot"]
        self.calls.append(snap.get("symbol", "<missing>"))
        return Signal(action="buy", size=0.05, reason="legacy_buy")


class _LegacyAlwaysHold:
    async def on_bar(self, ctx) -> Signal | None:
        return Signal(action="hold", size=0.0, reason="hold")


def _orchestrator() -> AsyncStrategyOrchestrator:
    policy = Policy(policy_version=1, name="test")
    return AsyncStrategyOrchestrator(policy)


class TestLiveScannerDispatch:
    def test_per_symbol_dispatch_emits_one_intent_per_universe_symbol(self):
        orch = _orchestrator()
        scanner = _AlwaysBuyLiveScanner()
        orch.register_strategy("scanner", scanner)

        universe = {
            "005930": _ohlcv("005930"),
            "000660": _ohlcv("000660"),
            "035720": _ohlcv("035720"),
        }
        snapshot = {
            "symbol": None,
            "price": None,
            "equity_krw": 1_000_000.0,
            "ohlcv_history": universe,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snapshot))

        assert sorted(scanner.calls) == sorted(universe.keys())
        assert {i.symbol for i in intents} == set(universe.keys())
        assert all(i.strategy_id == "scanner" for i in intents)
        assert all(i.side == "buy" for i in intents)

    def test_legacy_strategy_keeps_single_dispatch_path(self):
        """Non-live-scanner sees one dispatch per tick even when ohlcv_history present."""
        orch = _orchestrator()
        legacy = _LegacyAlwaysBuy()
        orch.register_strategy("legacy", legacy)

        snapshot = {
            "symbol": "BTCUSDT",
            "price": 50000.0,
            "equity_krw": 1_000_000.0,
            "ohlcv_history": {
                "005930": _ohlcv("005930"),
                "000660": _ohlcv("000660"),
            },
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snapshot))

        assert legacy.calls == ["BTCUSDT"], "legacy strategy must see top-level symbol once"
        assert len(intents) == 1
        assert intents[0].symbol == "BTCUSDT"

    def test_mixed_strategies_in_same_run_bar(self):
        orch = _orchestrator()
        scanner = _AlwaysBuyLiveScanner()
        legacy = _LegacyAlwaysBuy()
        orch.register_strategy("scanner", scanner)
        orch.register_strategy("legacy", legacy)

        universe = {"005930": _ohlcv("005930"), "000660": _ohlcv("000660")}
        snapshot = {
            "symbol": "BTCUSDT",
            "price": 50000.0,
            "equity_krw": 1_000_000.0,
            "ohlcv_history": universe,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snapshot))

        scanner_intents = [i for i in intents if i.strategy_id == "scanner"]
        legacy_intents = [i for i in intents if i.strategy_id == "legacy"]
        assert len(scanner_intents) == 2
        assert {i.symbol for i in scanner_intents} == set(universe.keys())
        assert len(legacy_intents) == 1
        assert legacy_intents[0].symbol == "BTCUSDT"

    def test_empty_ohlcv_falls_through_to_legacy_path(self):
        """Live-scanner with no universe → legacy single-dispatch (with no symbol)."""
        orch = _orchestrator()
        scanner = _AlwaysBuyLiveScanner()
        orch.register_strategy("scanner", scanner)

        snapshot = {
            "symbol": "TEST",
            "price": 100.0,
            "equity_krw": 1_000_000.0,
            # ohlcv_history absent → fall back to legacy single dispatch
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snapshot))
        # Legacy path used — single dispatch with top-level symbol
        assert len(scanner.calls) == 1
        assert scanner.calls[0] == "TEST"
        assert len(intents) == 1
        assert intents[0].symbol == "TEST"

    def test_quarantine_dedup_per_tick(self):
        """If a live-scanner throws across N symbols in one tick, fail_count
        must increment by 1 (not N) — preserving the consecutive-bad-tick
        semantics that #78 quarantine relies on.
        """

        class _AlwaysThrow(LiveScannerMixin):
            async def on_bar(self, ctx):
                raise RuntimeError("boom")

        orch = _orchestrator()
        thrower = _AlwaysThrow()
        orch.register_strategy("thrower", thrower)
        universe = {f"S{i:03d}": _ohlcv(f"S{i}") for i in range(5)}
        snapshot = {"equity_krw": 1_000_000.0, "ohlcv_history": universe}

        # 1st tick — 5 throws, fail_count should be 1, not 5 (no quarantine yet)
        asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snapshot))
        assert "thrower" not in orch.quarantined_strategies
        # 2nd tick — fail_count = 2, still not quarantined
        asyncio.run(orch.run_bar(pd.Timestamp("2026-01-02"), snapshot))
        assert "thrower" not in orch.quarantined_strategies
        # 3rd tick — fail_count = 3, NOW quarantined
        asyncio.run(orch.run_bar(pd.Timestamp("2026-01-03"), snapshot))
        assert "thrower" in orch.quarantined_strategies
