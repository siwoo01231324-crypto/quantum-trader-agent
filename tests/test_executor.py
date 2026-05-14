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
    # EURUSD 는 USDT pair 도 KRX 6 자리도 아니어서 conversion 이 ValueError 를 던지고
    # executor 는 REJECTED ack 로 변환. (USDT 로 끝나는 심볼은 #227 fallback 으로 통과)
    intent = OrderIntent(
        strategy_id="strat1",
        symbol="EURUSD",
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


# ---------------------------------------------------------------------------
# #192 — strategy_id propagation
# ---------------------------------------------------------------------------

async def test_order_acked_wal_payload_includes_strategy_id(broker, metrics):
    """#192 AC2: WAL `order_acked` payload must carry strategy_id."""
    from src.live.executor import execute_intents
    from src.live.wal import replay

    pb, ks, wal = broker
    intent = _btc_intent(strategy_id="strat_tag_test")
    acks = await execute_intents([intent], broker=pb, kill_switch=ks, wal=wal, metrics=metrics)
    assert acks[0].status == "FILLED"

    events, _ = replay(wal.path)
    acked = [e for e in events if e.event_type == "order_acked"]
    assert len(acked) == 1
    assert acked[0].payload.get("strategy_id") == "strat_tag_test"


async def test_position_store_register_order_called(broker, metrics):
    """#192: executor optionally registers (client_order_id → strategy_id) so
    the store can attribute fills even when the broker payload omits strategy_id.
    """
    from src.live.executor import execute_intents
    from src.live.strategy_position_store import StrategyPositionStore

    pb, ks, wal = broker
    store = StrategyPositionStore()
    intent = _btc_intent(strategy_id="store_register_test")

    acks = await execute_intents(
        [intent],
        broker=pb,
        kill_switch=ks,
        wal=wal,
        metrics=metrics,
        position_store=store,
    )
    assert acks[0].status == "FILLED"

    coid = acks[0].client_order_id
    assert store._resolve_strategy(coid) == "store_register_test"
