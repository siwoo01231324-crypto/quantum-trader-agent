from __future__ import annotations

import sys
import asyncio
import logging
from decimal import Decimal
from pathlib import Path

import pytest

from src.live.loop import (
    ShadowConfig,
    _tick_to_market_state,
    _tick_to_market_snapshot,
    _load_orchestrator,
    run_shadow_loop,
)
from src.live.process_lock import ProcessLock, ProcessLockBusy
from src.live.types import Tick


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeFeed:
    def __init__(self, ticks):
        self._ticks = list(ticks)

    async def connect(self): pass
    async def subscribe(self, symbols): pass

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for t in self._ticks:
            yield t

    async def aclose(self): pass


def _make_tick(symbol="BTCUSDT", price="50000", qty="0.1",
               ts="2026-04-26T12:00:00+00:00"):
    return Tick(
        symbol=symbol,
        price=Decimal(price),
        qty=Decimal(qty),
        ts=ts,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
def test_windows_event_loop_policy():
    import src.live.loop as _loop_module  # noqa: F401 — triggers module-level policy set
    assert isinstance(asyncio.get_event_loop_policy(), asyncio.WindowsSelectorEventLoopPolicy)


def test_shadow_config_defaults():
    cfg = ShadowConfig(symbols=["BTCUSDT"])
    assert cfg.wal_path == Path("logs/shadow/wal.jsonl")
    assert cfg.production_yaml == Path("configs/orchestrator/production.yaml")
    assert cfg.max_iterations is None
    assert cfg.policy is None


def test_tick_to_market_state():
    tick = _make_tick(price="50000", qty="0.1")
    state = _tick_to_market_state(tick)
    assert state.tick.last == pytest.approx(50000.0)
    assert state.tick.bid == pytest.approx(50000.0 * 0.9999)
    assert state.tick.ask == pytest.approx(50000.0 * 1.0001)
    assert state.tick.symbol == "BTCUSDT"


def test_tick_to_market_snapshot():
    tick = _make_tick()
    snap = _tick_to_market_snapshot(tick)
    assert "symbol" in snap
    assert "price" in snap
    assert "equity_krw" in snap
    assert snap["symbol"] == "BTCUSDT"
    assert snap["price"] == pytest.approx(50000.0)


def test_load_orchestrator_fallback_warning(tmp_path, caplog):
    """production.yaml 미존재 → warning 로그 + 빈 orchestrator 반환."""
    from src.execution.mock_matching import MockMatchingEngine
    from src.execution.paper_broker import PaperBroker
    from src.live.wal import WAL
    from src.ops.kill_switch import KillSwitch

    wal = WAL(tmp_path / "wal.jsonl")
    ks = KillSwitch()
    broker = PaperBroker(wal=wal, kill_switch=ks, matching_engine=MockMatchingEngine())

    cfg = ShadowConfig(
        symbols=["BTCUSDT"],
        production_yaml=tmp_path / "nonexistent.yaml",
    )

    with caplog.at_level(logging.WARNING, logger="src.live.loop"):
        orch = _load_orchestrator(cfg, broker)

    from src.portfolio._async_orchestrator import AsyncStrategyOrchestrator
    assert isinstance(orch, AsyncStrategyOrchestrator)
    assert any("production.yaml" in msg for msg in caplog.messages), (
        f"Expected warning about production.yaml, got: {caplog.messages}"
    )


@pytest.mark.asyncio
async def test_run_shadow_loop_with_fake_feed(tmp_path):
    """FakeFeed 3 ticks → graceful 종료 → WAL 파일 생성 확인."""
    ticks = [_make_tick() for _ in range(3)]
    fake_feed = FakeFeed(ticks)

    wal_path = tmp_path / "wal.jsonl"
    lock_path = tmp_path / ".live_loop.lock"

    cfg = ShadowConfig(
        symbols=["BTCUSDT"],
        wal_path=wal_path,
        lock_path=lock_path,
        max_iterations=3,
    )

    await run_shadow_loop(cfg, feed=fake_feed)

    # WAL 파일이 생성됐어야 함 (빈 orchestrator 라 intents=[], 파일은 생성됨)
    assert wal_path.parent.exists()


@pytest.mark.asyncio
async def test_on_orchestrator_ready_callback_invoked(tmp_path):
    """#180: ShadowConfig.on_orchestrator_ready receives the live orchestrator instance."""
    from portfolio import AsyncStrategyOrchestrator

    received: list[AsyncStrategyOrchestrator] = []
    fake_feed = FakeFeed([_make_tick()])

    cfg = ShadowConfig(
        symbols=["BTCUSDT"],
        wal_path=tmp_path / "wal.jsonl",
        lock_path=tmp_path / ".live_loop.lock",
        max_iterations=1,
        on_orchestrator_ready=received.append,
    )

    await run_shadow_loop(cfg, feed=fake_feed)

    assert len(received) == 1
    assert isinstance(received[0], AsyncStrategyOrchestrator)


@pytest.mark.asyncio
async def test_on_orchestrator_ready_callback_exception_swallowed(tmp_path, caplog):
    """Callback 예외는 swallow + log warn — loop 자체는 계속 진행."""
    fake_feed = FakeFeed([_make_tick()])

    def raising_cb(orch):
        raise RuntimeError("downstream wiring blew up")

    cfg = ShadowConfig(
        symbols=["BTCUSDT"],
        wal_path=tmp_path / "wal.jsonl",
        lock_path=tmp_path / ".live_loop.lock",
        max_iterations=1,
        on_orchestrator_ready=raising_cb,
    )

    # Loop must still complete despite callback raising.
    await run_shadow_loop(cfg, feed=fake_feed)
    assert any("on_orchestrator_ready_failed" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_lock_busy_raises(tmp_path):
    """동일 lock_path 로 ProcessLock 점유 후 run_shadow_loop 호출 → ProcessLockBusy raise."""
    lock_path = tmp_path / ".live_loop.lock"
    wal_path = tmp_path / "wal.jsonl"

    # 먼저 락 점유
    existing_lock = ProcessLock(lock_path)
    existing_lock.acquire()
    try:
        cfg = ShadowConfig(
            symbols=["BTCUSDT"],
            wal_path=wal_path,
            lock_path=lock_path,
            max_iterations=1,
        )
        with pytest.raises(ProcessLockBusy):
            await run_shadow_loop(cfg, feed=FakeFeed([_make_tick()]))
    finally:
        existing_lock.release()
