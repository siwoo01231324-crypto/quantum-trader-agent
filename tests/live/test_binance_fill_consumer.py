"""Binance user-data fill stream → order_filled WAL consumer.

Root incident: ``src/brokers/binance/async_ws.py::stream_fills`` has NO
production caller. On the live ``binance-testnet-shadow`` path the executor
only writes ``order_acked`` (intent — Binance MARKET ack is status=NEW, no
price). No ``order_filled`` WAL event is ever emitted, so
``PnLAggregator`` / ``StrategyPositionStore`` / ``trade_history`` (all of
which early-return on ``event_type != "order_filled"``) show ZERO realized
P&L / no positions / no trades for the entire Binance live path — the
dashboard shows the submitted INTENT forever (제출, price "—", intent qty),
never the actual fill.

These tests cover the production consumer that wires
``stream_fills()`` → ``order_filled`` WAL event → existing ``wal_observer``
fan-out (so timeline + StrategyPositionStore + PnLAggregator + trade_history
all update through the established seam — NOT a parallel path).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.brokers.base import OrderAck, OrderRequest
from src.brokers.types import BrokerFill
from src.live.executor import execute_intents
from src.live.fill_consumer import (
    broker_fill_to_order_filled_event,
    run_binance_fill_consumer,
)
from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.live.trade_history import reconstruct_trades
from src.live.wal import WAL, replay
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio.order_intent import OrderIntent


def _fill(
    *,
    client_order_id: str = "coid-1",
    broker_order_id: str = "100",
    trade_id: str = "1",
    qty: str = "0.0060",
    price: str = "65000.0",
    fee: str = "0.039",
) -> BrokerFill:
    return BrokerFill(
        parent_id=client_order_id,
        broker_order_id=broker_order_id,
        client_order_id=client_order_id,
        trade_id=trade_id,
        qty=Decimal(qty),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_asset="USDT",
        ts=datetime(2026, 5, 16, 1, 2, 3, tzinfo=timezone.utc),
        is_maker=False,
    )


# ── pure mapping: BrokerFill → order_filled WAL payload ───────────────────────


class TestBrokerFillToOrderFilledEvent:
    def test_field_mapping_actual_qty_price_fees_ts_strategy(self):
        fill = _fill()
        ev = broker_fill_to_order_filled_event(
            fill, symbol="BTCUSDT", side="buy", strategy_id="live-rsi-oversold",
        )
        assert ev.event_type == "order_filled"
        p = ev.payload
        assert p["symbol"] == "BTCUSDT"
        assert p["side"] == "buy"
        # ACTUAL fill qty/price — NOT the submitted intent qty.
        assert p["fill_qty"] == "0.0060"
        assert p["qty"] == "0.0060"
        assert p["fill_price"] == "65000.0"
        assert p["fees"] == "0.039"
        assert p["fee_asset"] == "USDT"
        assert p["client_order_id"] == "coid-1"
        assert p["broker_order_id"] == "100"
        assert p["trade_id"] == "1"
        assert p["strategy_id"] == "live-rsi-oversold"
        # fill ts persisted so cross-run trade_history sorts deterministically.
        assert p["ts"] == fill.ts.isoformat()

    def test_strategy_id_absent_when_unresolvable(self):
        """An unresolvable coid must STILL emit the fill (real money) — the
        strategy_id key is simply absent (per-strategy won't attribute, but
        totals stay correct). Same documented #18 fail-safe.
        """
        fill = _fill()
        ev = broker_fill_to_order_filled_event(
            fill, symbol="BTCUSDT", side="buy", strategy_id=None,
        )
        assert ev.event_type == "order_filled"
        assert "strategy_id" not in ev.payload
        # Totals still computable: PnLAggregator drops unattributed (logged)
        # but the fill is NOT silently lost from the WAL.
        assert ev.payload["fill_qty"] == "0.0060"


# ── strategy_id resolution from a stubbed StrategyPositionStore ───────────────


class TestStrategyResolution:
    def test_resolved_from_register_order_context_map(self):
        store = StrategyPositionStore()
        store.register_order_context(
            client_order_id="coid-xyz",
            symbol="BTCUSDT",
            side="buy",
            strategy_id="live-macd-bullish-cross",
        )
        ctx = store.resolve_order_context("coid-xyz")
        assert ctx == ("BTCUSDT", "buy", "live-macd-bullish-cross")

    def test_unresolvable_coid_returns_none(self):
        store = StrategyPositionStore()
        assert store.resolve_order_context("never-registered") is None

    def test_register_order_signature_unchanged(self):
        """The legacy register_order(*, client_order_id, strategy_id) must
        keep its exact signature (executor coid-attribution spy depends on it).
        """
        store = StrategyPositionStore()
        store.register_order(client_order_id="c", strategy_id="s")
        assert store._resolve_strategy("c") == "s"


# ── consumer: dedupe + WAL emit + observer fan-out ───────────────────────────


class _ListStream:
    """Yields a fixed list of BrokerFill once, then completes."""

    def __init__(self, fills):
        self._fills = list(fills)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._fills:
            raise StopAsyncIteration
        return self._fills.pop(0)


def _factory(fills):
    def make():
        return _ListStream(fills)
    return make


@pytest.mark.asyncio
async def test_ws_config_error_fails_fast_no_reconnect_storm(tmp_path):
    """#19 follow-up — user-reported 404 storm. A permanent handshake-config
    failure (WSConfigError) must NOT be retried: the consumer returns at once
    (factory invoked exactly ONCE, not up to max_attempts) so there is no
    20×100 reconnect storm; the trading loop is unaffected.
    """
    from src.brokers.errors import WSConfigError

    wal = WAL(tmp_path / "wal.jsonl")
    store = StrategyPositionStore()
    stop = asyncio.Event()
    calls = {"n": 0}

    class _CfgErrStream:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise WSConfigError("WS handshake HTTP 404 at wss://bad/host")

    def factory():
        calls["n"] += 1
        return _CfgErrStream()

    await run_binance_fill_consumer(
        factory, wal=wal, position_store=store, stop_event=stop,
        max_attempts=100,  # would storm 100× if WSConfigError were retried
    )
    assert calls["n"] == 1, "WSConfigError must fail fast — no reconnect retries"


@pytest.mark.asyncio
async def test_consumer_emits_order_filled_through_wal_observer(tmp_path):
    seen_events: list = []

    def observer(ev):
        seen_events.append(ev)

    wal = WAL(tmp_path / "wal.jsonl", observer=observer)
    store = StrategyPositionStore()
    store.register_order_context(
        client_order_id="coid-1", symbol="BTCUSDT", side="buy",
        strategy_id="live-rsi-oversold",
    )
    stop = asyncio.Event()

    await run_binance_fill_consumer(
        _factory([_fill(client_order_id="coid-1")]),
        wal=wal,
        position_store=store,
        stop_event=stop,
        max_attempts=2,
    )

    events, _ = replay(wal.path)
    filled = [e for e in events if e.event_type == "order_filled"]
    assert len(filled) == 1
    assert filled[0].payload["strategy_id"] == "live-rsi-oversold"
    assert filled[0].payload["fill_qty"] == "0.0060"
    # The SAME event went through the observer (single seam, not parallel).
    assert any(e.event_type == "order_filled" for e in seen_events)


@pytest.mark.asyncio
async def test_unresolvable_coid_emitted_with_warning_not_dropped(tmp_path, caplog):
    wal = WAL(tmp_path / "wal.jsonl")
    store = StrategyPositionStore()  # nothing registered → unresolvable
    stop = asyncio.Event()

    with caplog.at_level(logging.WARNING):
        await run_binance_fill_consumer(
            _factory([_fill(client_order_id="orphan-coid")]),
            wal=wal,
            position_store=store,
            stop_event=stop,
            max_attempts=2,
        )

    events, _ = replay(wal.path)
    filled = [e for e in events if e.event_type == "order_filled"]
    # NOT dropped — a real-money fill must always be recorded.
    assert len(filled) == 1
    assert "strategy_id" not in filled[0].payload
    assert any("orphan-coid" in r.message or "resolve" in r.message.lower()
               for r in caplog.records)


@pytest.mark.asyncio
async def test_idempotency_duplicate_fill_counted_once(tmp_path):
    wal = WAL(tmp_path / "wal.jsonl")
    store = StrategyPositionStore()
    store.register_order_context(
        client_order_id="coid-1", symbol="BTCUSDT", side="buy",
        strategy_id="strat",
    )
    stop = asyncio.Event()

    dup = _fill(client_order_id="coid-1", broker_order_id="100", trade_id="1")
    dup2 = _fill(client_order_id="coid-1", broker_order_id="100", trade_id="1")

    await run_binance_fill_consumer(
        _factory([dup, dup2]),
        wal=wal,
        position_store=store,
        stop_event=stop,
        max_attempts=2,
    )

    events, _ = replay(wal.path)
    filled = [e for e in events if e.event_type == "order_filled"]
    assert len(filled) == 1, "duplicate (broker_order_id, trade_id) must dedupe"


@pytest.mark.asyncio
async def test_partial_fills_same_order_distinct_trade_ids_all_counted(tmp_path):
    """#19 review GATE-1 guard (LIVE money): two partial fills of ONE order
    (same broker_order_id, DISTINCT trade_id) must BOTH be emitted and sum —
    a regression broadening the dedup key to broker_order_id alone would
    silently halve filled size and pass every other test.
    """
    wal = WAL(tmp_path / "wal.jsonl")
    store = StrategyPositionStore()
    store.register_order_context(
        client_order_id="coid-1", symbol="BTCUSDT", side="buy",
        strategy_id="strat",
    )
    stop = asyncio.Event()

    p1 = _fill(client_order_id="coid-1", broker_order_id="100",
               trade_id="1", qty="0.003")
    p2 = _fill(client_order_id="coid-1", broker_order_id="100",
               trade_id="2", qty="0.004")

    await run_binance_fill_consumer(
        _factory([p1, p2]),
        wal=wal,
        position_store=store,
        stop_event=stop,
        max_attempts=2,
    )

    events, _ = replay(wal.path)
    filled = [e for e in events if e.event_type == "order_filled"]
    assert len(filled) == 2, "distinct partial fills must NOT be deduped away"
    qtys = sorted(Decimal(str(e.payload["fill_qty"])) for e in filled)
    assert qtys == [Decimal("0.003"), Decimal("0.004")]
    assert sum(qtys) == Decimal("0.007"), "partials must sum to full filled qty"


@pytest.mark.asyncio
async def test_reconnect_on_stream_error_then_resume_bounded(tmp_path):
    """Stream raises once → consumer backs off and resumes (bounded). The
    loop must NOT crash; a clean second attempt delivers the fill.
    """
    wal = WAL(tmp_path / "wal.jsonl")
    store = StrategyPositionStore()
    store.register_order_context(
        client_order_id="coid-1", symbol="BTCUSDT", side="buy",
        strategy_id="strat",
    )
    stop = asyncio.Event()
    attempts = {"n": 0}

    class _RaiseThenYield:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("simulated WS drop")

    def make():
        attempts["n"] += 1
        if attempts["n"] == 1:
            return _RaiseThenYield()
        return _ListStream([_fill(client_order_id="coid-1")])

    sleeps: list[float] = []

    async def fake_sleep(d):
        sleeps.append(d)

    await run_binance_fill_consumer(
        make,
        wal=wal,
        position_store=store,
        stop_event=stop,
        max_attempts=5,
        sleep=fake_sleep,
    )

    assert attempts["n"] >= 2, "must reconnect after a stream error"
    assert sleeps, "must back off (sleep) between attempts"
    events, _ = replay(wal.path)
    assert any(e.event_type == "order_filled" for e in events)


@pytest.mark.asyncio
async def test_cancellation_is_clean(tmp_path):
    """A cancelled consumer task must exit promptly without propagating
    anything that would crash run_shadow_loop's shutdown path.
    """
    wal = WAL(tmp_path / "wal.jsonl")
    store = StrategyPositionStore()
    stop = asyncio.Event()

    class _Hang:
        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.sleep(3600)

    task = asyncio.create_task(
        run_binance_fill_consumer(
            lambda: _Hang(),
            wal=wal,
            position_store=store,
            stop_event=stop,
            max_attempts=100,
        )
    )
    await asyncio.sleep(0.05)
    task.cancel()
    # Must not raise anything other than CancelledError.
    with pytest.raises(asyncio.CancelledError):
        await task


# ── end-to-end: ack → fill → PnL + position + trade_history reflect ACTUAL ───


class _BinanceLikeBroker:
    """Adapter stub mirroring Binance MARKET ack semantics: the ack is
    status=NEW/SUBMITTED with NO price (intent only). The actual fill arrives
    out-of-band via the fill stream — exactly the production gap.
    """

    name = "binance_futures_async"
    paper = False

    def __init__(self):
        self.submitted: list[OrderRequest] = []

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self.submitted.append(req)
        return OrderAck(
            broker_order_id="100",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="NEW",  # Binance MARKET ack — NOT FILLED, no price
            ts=datetime.now(timezone.utc),
            qty=req.qty,
            price=None,
        )


@pytest.mark.asyncio
async def test_end_to_end_fill_reflected_in_pnl_position_history(tmp_path):
    position_store = StrategyPositionStore()
    pnl = PnLAggregator()
    timeline: list = []

    def wal_observer(ev):
        timeline.append(ev)
        position_store.ingest_fill_event(ev.event_type, ev.payload or {})
        pnl.ingest_fill_event(ev.event_type, ev.payload or {})

    wal = WAL(tmp_path / "wal.jsonl", observer=wal_observer)
    broker = _BinanceLikeBroker()
    ks = KillSwitch()
    metrics = Metrics()

    # 1. place → ack (executor registers coid context BEFORE place_order)
    buy = OrderIntent(
        strategy_id="live-rsi-oversold", symbol="BTCUSDT",
        side="buy", qty=0.076, reason="entry",
    )
    acks = await execute_intents(
        [buy], broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
        position_store=position_store,
    )
    assert acks[0].status == "NEW"
    # Pre-fill: only the acked INTENT exists, no realized P&L / position.
    assert pnl.realtime == 0.0
    assert position_store.get_positions("live-rsi-oversold") == []

    submitted_coid = broker.submitted[0].client_order_id

    # 2. the ACTUAL fill arrives — reduce_only-capped 0.0060 @ real price,
    #    NOT the submitted intent (0.076).
    stop = asyncio.Event()
    await run_binance_fill_consumer(
        _factory([_fill(
            client_order_id=submitted_coid,
            qty="0.0060", price="65000.0", fee="0.039",
        )]),
        wal=wal,
        position_store=position_store,
        stop_event=stop,
        max_attempts=2,
    )

    # 3. PnLAggregator + StrategyPositionStore reflect the ACTUAL fill.
    pos = position_store.get_positions("live-rsi-oversold")
    assert pos == [("BTCUSDT", 0.0060)], f"position must reflect actual fill, got {pos}"
    # buy realized = -fee
    assert pnl.realtime == pytest.approx(-0.039)

    # 4. trade_history reconstruction sees the open trade w/ ACTUAL qty/price.
    trades = reconstruct_trades([wal.path])
    assert len(trades) == 1
    t = trades[0]
    assert t.strategy_id == "live-rsi-oversold"
    assert t.symbol == "BTCUSDT"
    assert t.qty == pytest.approx(0.0060)
    assert t.entry_price == pytest.approx(65000.0)
    assert t.status == "open"

    # 5. order_filled flowed through the SAME observer the paper path uses.
    assert any(e.event_type == "order_filled" for e in timeline)
