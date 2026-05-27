"""Tests for ``_run_mark_price_consumer`` — the consumer that pipes every
mark-price update through ``LivePositionRiskManager.evaluate`` and routes
exit intents through the executor.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.live.loop import _run_mark_price_consumer
from src.live.types import WALEvent
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch


@dataclass
class _RecordingWAL:
    """Minimal stand-in for ``src.live.wal.WAL`` capturing only ``write``."""
    events: list[WALEvent]

    def write(self, event: WALEvent) -> None:
        self.events.append(event)


class _FakeMarkPriceFeed:
    def __init__(self, batches: list[list[tuple[str, Decimal, datetime]]]) -> None:
        self._batches = batches
        self.closed = False
        self.connected = False

    async def connect(self) -> None:
        self.connected = True

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for batch in self._batches:
            yield batch

    async def aclose(self) -> None:
        self.closed = True


class _FakeRiskManager:
    """Returns SELL intents only for symbols in ``trigger_for``."""

    def __init__(self, trigger_for: set[str]) -> None:
        self.trigger_for = trigger_for
        self.evaluated: list[tuple[str, Decimal]] = []

    def evaluate(self, symbol: str, price: Decimal, ts: datetime):
        self.evaluated.append((symbol, price))
        if symbol not in self.trigger_for:
            return []
        # Return a stub intent — real OrderIntent requires extra fields but
        # we monkey-patch execute_intents below so we can pass a SimpleNamespace.
        from types import SimpleNamespace
        return [SimpleNamespace(
            strategy_id="test-strategy", symbol=symbol, side="sell",
            qty=Decimal("1"), reason="take_profit", price=None,
        )]


@pytest.mark.asyncio
async def test_consumer_evaluates_every_symbol_in_batch(monkeypatch) -> None:
    """Universe-wide evaluation: every symbol in every batch hits evaluate()."""
    batches = [
        [
            ("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc)),
            ("ETHUSDT", Decimal("1800"), datetime.now(timezone.utc)),
            ("NEARUSDT", Decimal("4.5"), datetime.now(timezone.utc)),
        ],
        [
            ("BTCUSDT", Decimal("30100"), datetime.now(timezone.utc)),
            ("ZECUSDT", Decimal("45.2"), datetime.now(timezone.utc)),
        ],
    ]
    fake_feed = _FakeMarkPriceFeed(batches)
    risk_mgr = _FakeRiskManager(trigger_for=set())  # no exits this run
    executed: list = []

    async def _fake_execute(intents, **kwargs):
        executed.extend(intents)

    monkeypatch.setattr("src.live.loop.execute_intents", _fake_execute)

    stop_event = asyncio.Event()
    await _run_mark_price_consumer(
        position_risk_manager=risk_mgr,
        router=object(),
        kill_switch=KillSwitch(),
        wal=_RecordingWAL(events=[]),
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
    )

    assert [(s, p) for (s, p) in risk_mgr.evaluated] == [
        ("BTCUSDT", Decimal("30000")),
        ("ETHUSDT", Decimal("1800")),
        ("NEARUSDT", Decimal("4.5")),
        ("BTCUSDT", Decimal("30100")),
        ("ZECUSDT", Decimal("45.2")),
    ]
    assert executed == []  # no symbol triggered an exit


@pytest.mark.asyncio
async def test_consumer_routes_exit_intents_through_executor(monkeypatch) -> None:
    """When evaluate() returns intents, executor is called AND signal_emitted
    WAL events are written first."""
    fake_feed = _FakeMarkPriceFeed([
        [
            ("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc)),
            ("NEARUSDT", Decimal("4.5"), datetime.now(timezone.utc)),
        ],
    ])
    risk_mgr = _FakeRiskManager(trigger_for={"NEARUSDT"})
    wal = _RecordingWAL(events=[])
    executor_calls: list[dict] = []

    async def _fake_execute(intents, **kwargs):
        executor_calls.append({"intents": list(intents), **kwargs})

    monkeypatch.setattr("src.live.loop.execute_intents", _fake_execute)

    stop_event = asyncio.Event()
    await _run_mark_price_consumer(
        position_risk_manager=risk_mgr,
        router=object(),
        kill_switch=KillSwitch(),
        wal=wal,
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
    )

    assert len(executor_calls) == 1
    assert executor_calls[0]["intents"][0].symbol == "NEARUSDT"
    assert executor_calls[0]["intents"][0].side == "sell"

    signal_events = [e for e in wal.events if e.event_type == "signal_emitted"]
    assert len(signal_events) == 1
    assert signal_events[0].payload["symbol"] == "NEARUSDT"
    assert signal_events[0].payload["reason"] == "take_profit"


@pytest.mark.asyncio
async def test_consumer_swallows_evaluate_exceptions(monkeypatch) -> None:
    """A buggy evaluate() must not crash the consumer — log + skip + continue."""

    class _ExplodingRiskMgr:
        def __init__(self) -> None:
            self.calls = 0

        def evaluate(self, symbol, price, ts):
            self.calls += 1
            if symbol == "BTCUSDT":
                raise RuntimeError("boom")
            return []

    fake_feed = _FakeMarkPriceFeed([
        [
            ("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc)),
            ("ETHUSDT", Decimal("1800"), datetime.now(timezone.utc)),
        ],
    ])
    risk_mgr = _ExplodingRiskMgr()
    executed: list = []

    async def _fake_execute(intents, **kwargs):
        executed.extend(intents)

    monkeypatch.setattr("src.live.loop.execute_intents", _fake_execute)

    stop_event = asyncio.Event()
    await _run_mark_price_consumer(
        position_risk_manager=risk_mgr,
        router=object(),
        kill_switch=KillSwitch(),
        wal=_RecordingWAL(events=[]),
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
    )

    # Both symbols were evaluated despite BTCUSDT raising
    assert risk_mgr.calls == 2
    assert executed == []


@pytest.mark.asyncio
async def test_consumer_writes_every_price_to_cache(monkeypatch) -> None:
    """Every mark-price update — including symbols with no open position —
    must reach ``live_price_cache.set_price`` so the dashboard's PnL overlay
    has data even before the strategy opens a position there."""
    from src.live.price_cache import LivePriceCache

    cache = LivePriceCache()
    fake_feed = _FakeMarkPriceFeed([
        [
            ("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc)),
            ("ETHUSDT", Decimal("1800"), datetime.now(timezone.utc)),
            ("ZECUSDT", Decimal("45"), datetime.now(timezone.utc)),
        ],
    ])

    async def _noop_execute(intents, **kwargs):
        pass

    monkeypatch.setattr("src.live.loop.execute_intents", _noop_execute)

    stop_event = asyncio.Event()
    await _run_mark_price_consumer(
        position_risk_manager=_FakeRiskManager(trigger_for=set()),
        router=object(),
        kill_switch=KillSwitch(),
        wal=_RecordingWAL(events=[]),
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
        live_price_cache=cache,
    )

    assert cache.get_price("BTCUSDT").price == Decimal("30000")
    assert cache.get_price("ETHUSDT").price == Decimal("1800")
    assert cache.get_price("ZECUSDT").price == Decimal("45")
    assert len(cache) == 3


@pytest.mark.asyncio
async def test_consumer_no_cache_when_disabled(monkeypatch) -> None:
    """``live_price_cache=None`` (default) keeps the legacy code path —
    no AttributeError, no log spam."""
    fake_feed = _FakeMarkPriceFeed([
        [("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc))],
    ])

    async def _noop_execute(intents, **kwargs):
        pass

    monkeypatch.setattr("src.live.loop.execute_intents", _noop_execute)

    stop_event = asyncio.Event()
    await _run_mark_price_consumer(
        position_risk_manager=_FakeRiskManager(trigger_for=set()),
        router=object(),
        kill_switch=KillSwitch(),
        wal=_RecordingWAL(events=[]),
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
        live_price_cache=None,
    )


@pytest.mark.asyncio
async def test_consumer_stops_on_stop_event() -> None:
    """``stop_event.set()`` interrupts the batch loop quickly."""

    async def slow_feed_iter():
        await asyncio.sleep(10)  # would block forever
        yield []

    class _SlowFeed:
        async def connect(self): pass
        def __aiter__(self): return slow_feed_iter()
        async def aclose(self): pass

    stop_event = asyncio.Event()
    stop_event.set()  # pre-set: consumer should exit promptly

    risk_mgr = _FakeRiskManager(trigger_for=set())
    await asyncio.wait_for(
        _run_mark_price_consumer(
            position_risk_manager=risk_mgr,
            router=object(),
            kill_switch=KillSwitch(),
            wal=_RecordingWAL(events=[]),
            metrics=Metrics(),
            position_store=None,
            stop_event=stop_event,
            feed_factory=lambda: _SlowFeed(),
        ),
        timeout=2.0,
    )


@pytest.mark.asyncio
async def test_consumer_puts_synthetic_tick_to_tick_queue(monkeypatch) -> None:
    """#328: tick_queue 제공 시 batch 마다 BTC mark price 의 synthetic Tick 을
    queue 에 put. testnet aggTrade tick 부재로 cs-tsmom 미트리거되던 사고 fix
    회귀 박제. BTC 우선 — batch 에 없으면 첫 종목 fallback.
    """
    batches = [
        [
            ("ETHUSDT", Decimal("1800"), datetime(2026,5,27,0,0,0,tzinfo=timezone.utc)),
            ("BTCUSDT", Decimal("30000"), datetime(2026,5,27,0,0,0,tzinfo=timezone.utc)),
        ],
        [
            ("NEARUSDT", Decimal("5"), datetime(2026,5,27,0,0,1,tzinfo=timezone.utc)),
            ("ETHUSDT", Decimal("1850"), datetime(2026,5,27,0,0,1,tzinfo=timezone.utc)),
        ],
    ]
    fake_feed = _FakeMarkPriceFeed(batches)
    risk_mgr = _FakeRiskManager(trigger_for=set())

    async def _noop_execute(intents, **kwargs):
        pass
    monkeypatch.setattr("src.live.loop.execute_intents", _noop_execute)

    # 무제한 queue — 모든 put 캡쳐 (drop-oldest 는 producer() 와 동일 패턴)
    tick_queue: asyncio.Queue = asyncio.Queue()
    received: list = []

    async def _drain():
        while True:
            try:
                tick = await asyncio.wait_for(tick_queue.get(), timeout=0.5)
                received.append(tick)
            except asyncio.TimeoutError:
                return

    stop_event = asyncio.Event()
    drain_task = asyncio.create_task(_drain())
    await _run_mark_price_consumer(
        position_risk_manager=risk_mgr,
        router=object(),
        kill_switch=KillSwitch(),
        wal=_RecordingWAL(events=[]),
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
        tick_queue=tick_queue,
    )
    await drain_task

    assert len(received) >= 1, (
        f"tick_queue 에 synthetic tick 들어가야 함 — orchestrator 평가 트리거 "
        f"wire 실패. received={received}"
    )
    symbols = [t.symbol for t in received]
    assert "BTCUSDT" in symbols, (
        f"BTC 우선 선택 실패 (batch 1 에 BTC 있음). symbols={symbols}"
    )


@pytest.mark.asyncio
async def test_consumer_works_without_tick_queue(monkeypatch) -> None:
    """tick_queue 미지정 시 기존 동작 byte-identical — 기존 6개 테스트 회귀 0."""
    batches = [
        [("BTCUSDT", Decimal("30000"), datetime.now(timezone.utc))],
    ]
    fake_feed = _FakeMarkPriceFeed(batches)
    risk_mgr = _FakeRiskManager(trigger_for=set())
    monkeypatch.setattr("src.live.loop.execute_intents", lambda *a, **k: None)

    stop_event = asyncio.Event()
    await _run_mark_price_consumer(
        position_risk_manager=risk_mgr,
        router=object(),
        kill_switch=KillSwitch(),
        wal=_RecordingWAL(events=[]),
        metrics=Metrics(),
        position_store=None,
        stop_event=stop_event,
        feed_factory=lambda: fake_feed,
    )
    assert risk_mgr.evaluated == [("BTCUSDT", Decimal("30000"))]
