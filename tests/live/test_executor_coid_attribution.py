"""Bug B — strategy attribution must survive the Binance client_order_id
length cap (#238 follow-up).

Root incident: executor's idempotency key was
``{strategy_id}:{symbol}:{ts_ms}:{idx}``. For a real long strategy
(``live-breakout-with-atr-stop``=27 chars) that string is > 36 chars → fails
``BINANCE_CLIENT_ID_PATTERN`` → the adapter regenerated a strategy-OPAQUE
sha256 coid and submitted THAT. The fill returned with the submitted (opaque)
coid; ``_resolve_strategy`` could no longer map it → per-strategy positions /
trade-history / pnl attribution silently lost.

Fix invariant: generate the Binance-valid coid ONCE upstream (executor),
register THAT exact coid → strategy_id in StrategyPositionStore BEFORE
place_order, and the adapter keeps an already-valid coid as-is. So:

    coid registered == coid submitted == coid on the returned fill

and StrategyPositionStore's explicit map resolves the strategy regardless of
whether the coid carries a ``{strategy}:`` prefix.
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

from src.brokers.base import OrderAck, OrderRequest
from src.brokers.client_id import BINANCE_CLIENT_ID_PATTERN
from src.live.executor import execute_intents
from src.live.strategy_position_store import StrategyPositionStore
from src.live.wal import WAL
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio.order_intent import OrderIntent

_PATTERN = re.compile(BINANCE_CLIENT_ID_PATTERN)

LONG_STRATEGY = "live-breakout-with-atr-stop"  # 27 chars — the real culprit


class _RecordingBroker:
    """AsyncBrokerAdapter stub that echoes back the SUBMITTED coid.

    Mirrors the real Binance adapter contract: it keeps an already-valid coid
    as-is and the returned ack/fill carries the coid it actually submitted.
    """

    name = "binance_futures_async"
    paper = False

    def __init__(self) -> None:
        self.submitted: list[str] = []

    async def place_order(self, req: OrderRequest) -> OrderAck:
        # Real adapter only re-generates when the coid fails the regex.
        assert _PATTERN.match(req.client_order_id), (
            f"executor handed the adapter an INVALID coid {req.client_order_id!r} "
            f"(len={len(req.client_order_id)}); the adapter would discard the "
            f"strategy and submit an opaque sha256 — attribution lost"
        )
        cid = req.client_order_id
        self.submitted.append(cid)
        from datetime import datetime, timezone

        return OrderAck(
            broker_order_id="1",
            client_order_id=cid,  # fill echoes the SUBMITTED coid
            symbol=req.symbol,
            status="FILLED",
            ts=datetime.now(timezone.utc),
            qty=req.qty,
            price=Decimal("1"),
        )


def _intent(strategy_id: str, symbol: str = "KITEUSDT") -> OrderIntent:
    return OrderIntent(
        strategy_id=strategy_id,
        symbol=symbol,
        side="buy",
        qty=10.0,
        reason="test",
    )


@pytest.fixture
def wal(tmp_path):
    return WAL(tmp_path / "wal.jsonl")


@pytest.mark.asyncio
async def test_long_strategy_coid_is_valid_and_attributable_end_to_end(wal):
    """The real failing case: a 27-char strategy. The coid the executor builds
    must (1) pass the Binance regex so the adapter keeps it, and (2) resolve
    back to the real strategy_id via StrategyPositionStore's explicit map.
    """
    broker = _RecordingBroker()
    store = StrategyPositionStore()
    ks = KillSwitch()
    metrics = Metrics()

    acks = await execute_intents(
        [_intent(LONG_STRATEGY)],
        broker=broker,
        kill_switch=ks,
        wal=wal,
        metrics=metrics,
        position_store=store,
    )

    assert len(acks) == 1
    submitted_coid = broker.submitted[0]

    # Invariant: registered == submitted == returned-fill coid.
    assert acks[0].client_order_id == submitted_coid
    assert _PATTERN.match(submitted_coid)

    # The fill (carrying the submitted coid) resolves to the REAL strategy.
    resolved = store._resolve_strategy(submitted_coid)
    assert resolved == LONG_STRATEGY, (
        f"attribution lost: coid {submitted_coid!r} resolved to {resolved!r}"
    )

    # End-to-end: a fill on that coid lands in the right strategy bucket.
    store.record_fill_by_client_order_id(
        client_order_id=submitted_coid,
        symbol="KITEUSDT",
        side="buy",
        qty=Decimal("10"),
    )
    assert store.get_positions(LONG_STRATEGY) == [("KITEUSDT", 10.0)]


@pytest.mark.asyncio
async def test_registered_coid_equals_submitted_coid(wal):
    """StrategyPositionStore.register_order must be called with the SAME coid
    the adapter submits (so the explicit map keys match the fill's coid).
    """
    broker = _RecordingBroker()
    metrics = Metrics()

    class _SpyStore(StrategyPositionStore):
        def __init__(self):
            super().__init__()
            self.registered: list[str] = []

        def register_order(self, *, client_order_id: str, strategy_id: str) -> None:
            self.registered.append(client_order_id)
            super().register_order(
                client_order_id=client_order_id, strategy_id=strategy_id
            )

    store = _SpyStore()
    await execute_intents(
        [_intent(LONG_STRATEGY)],
        broker=broker,
        kill_switch=KillSwitch(),
        wal=wal,
        metrics=metrics,
        position_store=store,
    )

    assert store.registered == broker.submitted
    assert store._resolve_strategy(store.registered[0]) == LONG_STRATEGY


@pytest.mark.asyncio
async def test_already_valid_short_coid_round_trips(wal):
    """A short strategy already yields a valid coid; behaviour must not
    regress — still attributable end-to-end.
    """
    broker = _RecordingBroker()
    store = StrategyPositionStore()
    metrics = Metrics()

    await execute_intents(
        [_intent("momo", symbol="BTCUSDT")],
        broker=broker,
        kill_switch=KillSwitch(),
        wal=wal,
        metrics=metrics,
        position_store=store,
    )

    coid = broker.submitted[0]
    assert _PATTERN.match(coid)
    assert store._resolve_strategy(coid) == "momo"


@pytest.mark.asyncio
async def test_coid_is_deterministic_idempotent(wal, monkeypatch):
    """Same (strategy, symbol, side, ts) → same coid (idempotent retry safety).
    """
    import src.live.executor as executor_mod

    monkeypatch.setattr(executor_mod.time, "time", lambda: 1_700_000_000.0)

    broker1 = _RecordingBroker()
    broker2 = _RecordingBroker()
    metrics = Metrics()

    await execute_intents(
        [_intent(LONG_STRATEGY)],
        broker=broker1,
        kill_switch=KillSwitch(),
        wal=wal,
        metrics=metrics,
    )
    await execute_intents(
        [_intent(LONG_STRATEGY)],
        broker=broker2,
        kill_switch=KillSwitch(),
        wal=wal,
        metrics=metrics,
    )

    assert broker1.submitted[0] == broker2.submitted[0]
