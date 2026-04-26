from __future__ import annotations

import re
from datetime import datetime, timezone

import pytest
from prometheus_client import CollectorRegistry

from src.observability.metrics import Metrics
from src.portfolio.order_intent import OrderIntent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def broker(tmp_path):
    from src.execution.paper_broker import PaperBroker
    from src.execution.mock_matching import MockMatchingEngine
    from src.execution.base import MarketState, Tick
    from src.live.wal import WAL
    from src.ops.kill_switch import KillSwitch

    wal = WAL(tmp_path / "wal.jsonl")
    ks = KillSwitch()
    me = MockMatchingEngine()
    pb = PaperBroker(wal=wal, kill_switch=ks, matching_engine=me)
    pb.update_market(MarketState(
        tick=Tick(
            symbol="BTCUSDT",
            bid=50000.0,
            ask=50001.0,
            last=50000.5,
            volume=1000,
            ts=datetime.now(timezone.utc),
        ),
        adv=1_000_000.0,
    ))
    return pb, ks, wal


@pytest.fixture
def metrics():
    return Metrics(registry=CollectorRegistry())


def _btc_intent(strategy_id: str = "strat1") -> OrderIntent:
    return OrderIntent(
        strategy_id=strategy_id,
        symbol="BTCUSDT",
        side="buy",
        qty=0.001,
        reason="test",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_execute_normal_flow(broker, metrics):
    from src.live.executor import execute_intents

    pb, ks, wal = broker
    intent = _btc_intent()
    acks = await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    assert len(acks) == 1
    assert acks[0].status == "FILLED"
    assert acks[0].reject_reason is None


async def test_execute_kill_switch_blocks(broker, metrics):
    from src.live.executor import execute_intents

    pb, ks, wal = broker
    ks.trip(reason="test", source="manual")

    intent = _btc_intent()
    acks = await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    assert len(acks) == 1
    assert acks[0].status == "REJECTED"
    assert acks[0].reject_reason == "KILL_SWITCH"


async def test_execute_unknown_symbol_conversion_error(broker, metrics):
    from src.live.executor import execute_intents

    pb, ks, wal = broker
    intent = OrderIntent(
        strategy_id="strat1",
        symbol="DOGEUSDT",
        side="buy",
        qty=100.0,
        reason="test",
    )
    acks = await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    assert len(acks) == 1
    assert acks[0].status == "REJECTED"
    assert acks[0].reject_reason is not None
    assert acks[0].reject_reason.startswith("CONVERSION:")


async def test_execute_metrics_orders_total_increments(broker, metrics):
    from src.live.executor import execute_intents
    from prometheus_client import REGISTRY

    pb, ks, wal = broker
    intent = _btc_intent(strategy_id="strat_metrics")

    await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    sample = metrics.orders_total.labels(
        strategy="strat_metrics",
        broker="paper",
        side="BUY",
        status="FILLED",
    )._value.get()
    assert sample == 1.0


async def test_execute_metrics_latency_observed(broker, metrics):
    from src.live.executor import execute_intents
    from prometheus_client import generate_latest

    pb, ks, wal = broker
    intent = _btc_intent()

    await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    # Check histogram _count sample via registry
    count_val = None
    for metric in metrics.registry.collect():
        if metric.name == "qta_order_latency_seconds":
            for sample in metric.samples:
                if (
                    sample.name == "qta_order_latency_seconds_count"
                    and sample.labels.get("broker") == "paper"
                    and sample.labels.get("algo") == "execute_intents"
                ):
                    count_val = sample.value
                    break

    assert count_val is not None
    assert count_val >= 1


async def test_execute_idempotency_key_format(broker, metrics):
    from src.live.executor import execute_intents

    pb, ks, wal = broker
    intent = _btc_intent(strategy_id="my_strat")
    acks = await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    assert len(acks) == 1
    key = acks[0].client_order_id
    # format: {strategy_id}:{symbol}:{ts_epoch_ms}:{idx}
    pattern = r"^my_strat:BTCUSDT:\d+:0$"
    assert re.match(pattern, key), f"key {key!r} does not match pattern {pattern}"


async def test_execute_multiple_intents(broker, metrics):
    from src.live.executor import execute_intents

    pb, ks, wal = broker
    intents = [_btc_intent(strategy_id=f"s{i}") for i in range(3)]
    acks = await execute_intents(intents, broker=pb, kill_switch=ks, wal=wal, metrics=metrics)

    assert len(acks) == 3
    for expected_idx, ack in enumerate(acks):
        # last segment of client_order_id is the idx
        actual_idx = int(ack.client_order_id.split(":")[-1])
        assert actual_idx == expected_idx
