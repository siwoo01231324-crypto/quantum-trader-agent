"""Universe-scale smoke for paper broker pipeline (#227 S5).

Submits 380 OrderIntents (350 KRX + 30 Binance shapes) through
``execute_intents`` against a PaperBroker in a single call to ensure the
broker + WAL absorb a universe-wide live-scanner burst without rejects or
runaway latency. Not a stress benchmark — a regression guardrail.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.execution.base import MarketState, Tick
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.executor import execute_intents
from src.live.wal import WAL
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio.order_intent import OrderIntent


@pytest.mark.asyncio
async def test_execute_intents_handles_380_orders_in_one_burst(
    tmp_path: Path,
):
    # No monkeypatch needed — `get_step_size` (#227 conversion fallback)
    # resolves KRX 6-digit codes (step=1) and Binance USDT pairs
    # (step=0.001) without an explicit registry entry per symbol.

    wal = WAL(tmp_path / "wal.jsonl")
    metrics = Metrics()
    kill_switch = KillSwitch()
    matching = MockMatchingEngine()
    broker = PaperBroker(
        wal=wal, kill_switch=kill_switch,
        matching_engine=matching, initial_balance=Decimal("100000000"),
    )

    symbols = [f"{i:06d}" for i in range(350)] + [f"SYM{i}USDT" for i in range(30)]
    # Set a market state per symbol so the matching engine has bid/ask.
    for sym in symbols:
        broker.update_market(MarketState(
            tick=Tick(
                symbol=sym, bid=99.5, ask=100.5, last=100.0,
                volume=1000, ts=datetime.now(timezone.utc),
            ),
            adv=1_000_000.0,
        ))

    intents = [
        OrderIntent(
            strategy_id="live_universe_scan",
            symbol=sym,
            side="buy",
            qty=1.0,
            reason=f"scale_test:{sym}",
        )
        for sym in symbols
    ]

    t0 = time.monotonic()
    acks = await execute_intents(
        intents,
        broker=broker, kill_switch=kill_switch,
        wal=wal, metrics=metrics,
    )
    elapsed = time.monotonic() - t0

    assert len(acks) == 380
    assert elapsed < 10.0, f"380-order burst took {elapsed:.2f}s — perf regression"
    rejected = [a for a in acks if a.status == "REJECTED"]
    assert not rejected, (
        f"unexpected rejects under scale: "
        f"{[(a.client_order_id, a.reject_reason) for a in rejected[:5]]}"
    )
