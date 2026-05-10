"""Integration smoke for live-scanner stop/TP wiring in run_shadow_loop (#227 S3).

These tests don't drive a real ``LivePositionRiskManager`` — that is unit-tested
in ``tests/portfolio/test_live_position_risk.py``. Here we verify the loop
*plumbing*: when ``ShadowConfig.position_risk_manager`` is set, every tick the
consumer calls ``evaluate(symbol, last_price, ts)`` and routes the returned
intents through ``execute_intents`` (broker + WAL). We also assert the
absent-manager case is a complete no-op (zero impact on legacy paths).
"""
from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.live.loop import ShadowConfig, run_shadow_loop
from src.live.types import Tick
from src.live.wal import replay
from src.portfolio.order_intent import OrderIntent


class _CountingRiskManager:
    """Minimal stand-in for LivePositionRiskManager to verify wiring.

    Records every ``evaluate`` call and optionally returns SELL intents on the
    Nth call so we can assert the consumer routes them through the broker.
    """

    def __init__(self, *, sell_on_call: int | None = None) -> None:
        self.calls: list[tuple[str, Decimal]] = []
        self._sell_on_call = sell_on_call

    def evaluate(self, symbol, last_price, ts):
        self.calls.append((symbol, last_price))
        if self._sell_on_call is not None and len(self.calls) == self._sell_on_call:
            return [OrderIntent(
                strategy_id="test_scanner",
                symbol=symbol,
                side="sell",
                qty=10.0,
                reason=f"live_stop_loss:test:last={last_price}",
            )]
        return []


def _ticks(symbol: str, n: int = 3) -> list[Tick]:
    """Synthesize *n* deterministic 1-minute ticks at falling prices."""
    base_ts = "2026-05-11T05:00:00+00:00"
    out: list[Tick] = []
    for i in range(n):
        # Increment minute portion so each tick has a distinct ts.
        ts = f"2026-05-11T05:{i:02d}:00+00:00"
        out.append(Tick(
            symbol=symbol,
            price=Decimal(str(80_000 - 1_000 * i)),  # 80000 → 79000 → 78000
            qty=Decimal("1"),
            ts=ts,
            server_ts=ts,
        ))
    return out


def _config(tmp_path: Path, *, mock_ticks, max_iterations=3) -> ShadowConfig:
    return ShadowConfig(
        symbols=["005930"],
        wal_path=tmp_path / "wal.jsonl",
        lock_path=tmp_path / ".live_loop.lock",
        initial_balance=Decimal("1000000"),
        production_yaml=tmp_path / "missing.yaml",  # → empty orchestrator fallback
        max_iterations=max_iterations,
        broker_mode="paper-only",
        feed_mode="mock",
        schedule="always",
        mock_ticks=mock_ticks,
    )


class TestPositionRiskManagerWiring:
    """The position_risk_manager is invoked once per processed tick."""

    @pytest.mark.asyncio
    async def test_evaluate_called_per_tick_when_manager_set(self, tmp_path):
        risk_mgr = _CountingRiskManager()
        cfg = _config(tmp_path, mock_ticks=_ticks("005930", 3))
        cfg.position_risk_manager = risk_mgr

        await run_shadow_loop(cfg)

        # max_iterations=3 → exactly 3 evaluate calls, one per tick consumed.
        assert len(risk_mgr.calls) == 3
        symbols = [s for s, _ in risk_mgr.calls]
        assert symbols == ["005930", "005930", "005930"]
        # Last price observed must match the last mock tick.
        assert risk_mgr.calls[-1][1] == Decimal("78000")

    @pytest.mark.asyncio
    async def test_no_calls_when_manager_absent(self, tmp_path):
        """The legacy path has no LivePositionRiskManager — must remain so."""
        cfg = _config(tmp_path, mock_ticks=_ticks("005930", 2))
        # cfg.position_risk_manager defaults to None
        await run_shadow_loop(cfg)
        # No assertion target — just verify the loop runs without error and
        # the WAL was written (i.e. no plumbing crash).
        assert cfg.wal_path.exists()
        events, _ = replay(cfg.wal_path)
        # At minimum we should see run_started.
        assert any(e.event_type == "run_started" for e in events)

    @pytest.mark.asyncio
    async def test_sell_intent_routed_to_broker_and_wal(self, tmp_path):
        """When evaluate returns a SELL intent, it must hit the WAL pipeline."""
        risk_mgr = _CountingRiskManager(sell_on_call=2)  # fire on 2nd tick
        cfg = _config(tmp_path, mock_ticks=_ticks("005930", 3))
        cfg.position_risk_manager = risk_mgr

        await run_shadow_loop(cfg)

        events, _ = replay(cfg.wal_path)
        # `signal_emitted` records every intent the consumer routes — both
        # strategy-emitted and live-scanner exit intents share this event_type.
        sell_signals = [
            e for e in events
            if e.event_type == "signal_emitted"
            and e.payload.get("side") == "sell"
            and "live_stop_loss" in str(e.payload.get("reason", ""))
        ]
        assert len(sell_signals) == 1, (
            f"Expected exactly one live-scanner SELL signal_emitted, "
            f"got events: {[e.event_type for e in events]}"
        )
        assert sell_signals[0].payload["strategy_id"] == "test_scanner"
        assert sell_signals[0].payload["symbol"] == "005930"
