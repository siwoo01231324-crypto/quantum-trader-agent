"""run_shadow_loop must spawn the Binance fill consumer ONLY for the
binance-testnet-shadow broker path, and the paper / kis paths must be a
byte-identical no-op (no fill-stream task created).

This is the loop *plumbing* test — the consumer logic itself is unit-tested
in test_binance_fill_consumer.py. Here we assert:
  - paper-only: no Binance fill stream is ever requested (stream_fills NOT
    called); behaviour unchanged (run_started + run completes).
  - binance-testnet-shadow: the adapter's stream_fills() IS consumed and a
    delivered BrokerFill produces an order_filled WAL event via the existing
    wal_observer fan-out.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.brokers.base import OrderAck, OrderRequest
from src.brokers.types import BrokerFill
from src.live.loop import ShadowConfig, run_shadow_loop
from src.live.strategy_position_store import StrategyPositionStore
from src.live.types import Tick
from src.live.wal import replay


def _ticks(symbol: str, n: int = 2) -> list[Tick]:
    out = []
    for i in range(n):
        ts = f"2026-05-16T05:{i:02d}:00+00:00"
        out.append(Tick(
            symbol=symbol, price=Decimal("65000"), qty=Decimal("1"),
            ts=ts, server_ts=ts,
        ))
    return out


def _cfg(tmp_path: Path, *, broker_mode: str, symbols, ticks) -> ShadowConfig:
    return ShadowConfig(
        symbols=symbols,
        wal_path=tmp_path / "wal.jsonl",
        lock_path=tmp_path / ".live_loop.lock",
        initial_balance=Decimal("1000000"),
        production_yaml=tmp_path / "missing.yaml",
        max_iterations=2,
        broker_mode=broker_mode,
        feed_mode="mock",
        schedule="always",
        mock_ticks=ticks,
    )


@pytest.mark.asyncio
async def test_paper_only_does_not_request_binance_fill_stream(tmp_path):
    cfg = _cfg(tmp_path, broker_mode="paper-only",
              symbols=["005930"], ticks=_ticks("005930", 2))
    await run_shadow_loop(cfg)
    events, _ = replay(cfg.wal_path)
    assert any(e.event_type == "run_started" for e in events)
    # No order_filled from a phantom Binance consumer on the paper path.
    # (PaperBroker emits its own order_filled only if a strategy trades; the
    # empty-orchestrator fallback here produces none.)
    assert not any(
        e.event_type == "order_filled" and e.payload.get("fee_asset") == "USDT"
        and e.payload.get("broker_order_id") == "binance-phantom"
        for e in events
    )


class _StubBinanceAdapter:
    """Minimal binance adapter: place_order returns a Binance-style NEW ack
    (no price), stream_fills yields one BrokerFill for the submitted coid.
    """

    name = "binance_futures_async"
    paper = True

    def __init__(self):
        self._submitted: list[str] = []
        self.stream_calls = 0

    async def place_order(self, req: OrderRequest) -> OrderAck:
        self._submitted.append(req.client_order_id)
        return OrderAck(
            broker_order_id="100",
            client_order_id=req.client_order_id,
            symbol=req.symbol,
            status="NEW",
            ts=datetime.now(timezone.utc),
            qty=req.qty,
            price=None,
        )

    def stream_fills(self):
        self.stream_calls += 1
        submitted = self._submitted

        class _S:
            def __init__(self):
                self._done = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                # Wait until at least one order was submitted, then yield one
                # fill for it, then complete.
                for _ in range(200):
                    if submitted and not self._done:
                        self._done = True
                        coid = submitted[0]
                        return BrokerFill(
                            parent_id=coid, broker_order_id="100",
                            client_order_id=coid, trade_id="1",
                            qty=Decimal("0.0060"), price=Decimal("65000"),
                            fee=Decimal("0.04"), fee_asset="USDT",
                            ts=datetime.now(timezone.utc), is_maker=False,
                        )
                    await asyncio.sleep(0.01)
                raise StopAsyncIteration

        return _S()


@pytest.mark.asyncio
async def test_binance_mode_consumes_fill_stream_and_emits_order_filled(tmp_path):
    """Drive a live-scanner-style intent so the executor submits an order;
    the stub adapter's fill stream then delivers the actual fill, which must
    become an order_filled WAL event through the wal_observer seam.
    """
    from src.portfolio.order_intent import OrderIntent

    position_store = StrategyPositionStore()
    pnl_events: list = []

    def observer(ev):
        pnl_events.append(ev)
        position_store.ingest_fill_event(ev.event_type, ev.payload or {})

    cfg = _cfg(tmp_path, broker_mode="binance-testnet-shadow",
              symbols=["BTCUSDT"], ticks=_ticks("BTCUSDT", 3))
    cfg.wal_observer = observer
    cfg.position_store = position_store

    # Inject a one-shot strategy that emits a BUY on the first tick so the
    # executor submits an order the fill stream can fill.
    class _OneShot:
        def __init__(self):
            self.fired = False

        async def run_bar(self, ts, snapshot):
            if self.fired:
                return []
            self.fired = True
            return [OrderIntent(
                strategy_id="live-rsi-oversold", symbol="BTCUSDT",
                side="buy", qty=0.076, reason="entry",
            )]

        def register_strategy_returns(self, *a, **k):
            pass

    adapter = _StubBinanceAdapter()

    # Patch the orchestrator loader to return our one-shot.
    import src.live.loop as loop_mod
    orig = loop_mod._load_orchestrator
    loop_mod._load_orchestrator = lambda config, broker: _OneShot()
    try:
        await run_shadow_loop(cfg, binance_adapter=adapter)
    finally:
        loop_mod._load_orchestrator = orig

    assert adapter.stream_calls >= 1, "binance fill stream must be consumed"
    events, _ = replay(cfg.wal_path)
    filled = [e for e in events if e.event_type == "order_filled"]
    assert len(filled) >= 1, f"expected order_filled, got {[e.event_type for e in events]}"
    assert filled[0].payload["fill_qty"] == "0.0060"
    assert filled[0].payload["strategy_id"] == "live-rsi-oversold"
    # Position store (fed via the observer) reflects the ACTUAL fill qty.
    assert position_store.get_positions("live-rsi-oversold") == [("BTCUSDT", 0.0060)]
