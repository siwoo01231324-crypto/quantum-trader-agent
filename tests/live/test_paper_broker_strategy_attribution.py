"""KIS-paper strategy attribution must survive the strategy-opaque coid (#238 review MEDIUM).

Root incident (post-#238 Bug-B fix): ``client_order_id`` is now a
Binance-valid sha256 with NO ``{strategy}:`` prefix (by design — 36-char
cap). ``PaperBroker`` emits the ``order_filled`` WAL event WITHOUT
``strategy_id``. ``PnLAggregator.ingest_fill_event`` / ``trade_history``
fall back to ``:``-prefix coid parsing, which now always fails → KIS-paper
(``kis-paper-shadow`` / ``paper-only``) fills are dropped from per-strategy
P&L and trade-history (under-counted, logged, no crash).

Fix invariant: thread ``intent.strategy_id`` → ``OrderRequest.strategy_id``
→ persisted into the ``order_filled`` WAL payload at its source so
replay-based + cross-run consumers attribute correctly.

Legacy / absent strategy_id → byte-identical: the payload key is simply
absent (current behavior, no KeyError on replay).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.brokers.base import OrderRequest, OrderType, PositionSide
from src.execution.base import MarketState, Side, Tick, TimeInForce
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.pnl_aggregator import PnLAggregator
from src.live.trade_history import reconstruct_trades
from src.live.wal import WAL, replay
from src.ops.kill_switch import KillSwitch

KIS_SYMBOL = "005930"  # 6-digit KRX → classify_venue == "kis"
KIS_STRATEGY = "kis-paper-shadow"


def _make_market(symbol: str = KIS_SYMBOL, price: float = 100.0) -> MarketState:
    tick = Tick(
        symbol=symbol,
        bid=price - 0.5,
        ask=price + 0.5,
        last=price,
        volume=1000,
        ts=datetime.now(timezone.utc),
    )
    return MarketState(tick=tick)


def _make_req(
    *,
    side: Side = Side.BUY,
    qty: Decimal = Decimal("10"),
    coid: str = "opaque-sha256-no-colon",
    strategy_id: str | None = None,
    symbol: str = KIS_SYMBOL,
) -> OrderRequest:
    return OrderRequest(
        client_order_id=coid,
        symbol=symbol,
        side=side,
        qty=qty,
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
        strategy_id=strategy_id,
    )


def _make_broker(tmp_path: Path) -> PaperBroker:
    wal = WAL(tmp_path / "wal.jsonl")
    broker = PaperBroker(
        wal=wal,
        kill_switch=KillSwitch(),
        matching_engine=MockMatchingEngine(),
        initial_balance=Decimal("1000000"),
    )
    broker.update_market(_make_market())
    return broker


def _order_filled_payloads(wal_path: Path) -> list[dict]:
    events, _ = replay(wal_path)
    return [e.payload for e in events if e.event_type == "order_filled"]


# --- 1. PaperBroker persists strategy_id in order_filled when carried ---


@pytest.mark.asyncio
async def test_order_filled_payload_carries_strategy_id(tmp_path):
    broker = _make_broker(tmp_path)
    await broker.place_order(_make_req(strategy_id=KIS_STRATEGY))
    payloads = _order_filled_payloads(tmp_path / "wal.jsonl")
    assert len(payloads) == 1
    assert payloads[0]["strategy_id"] == KIS_STRATEGY


# --- 1b. absent strategy_id → byte-identical legacy (no key) ---


@pytest.mark.asyncio
async def test_order_filled_payload_legacy_byte_identical_when_absent(tmp_path):
    broker = _make_broker(tmp_path)
    await broker.place_order(_make_req(strategy_id=None))
    payloads = _order_filled_payloads(tmp_path / "wal.jsonl")
    assert len(payloads) == 1
    # Legacy behavior: the order_filled payload simply has no strategy_id key.
    assert "strategy_id" not in payloads[0]
    # Exact legacy key set (no extra fields introduced).
    assert set(payloads[0].keys()) == {
        "client_order_id",
        "broker_order_id",
        "symbol",
        "side",
        "qty",
        "fill_price",
        "fill_qty",
        "fees",
        "fee_asset",
        "ack_latency_ms",
        "trade_id",
        "server_ts",
    }


# --- 2. End-to-end: KIS-paper fill → PnLAggregator + trade_history attribute ---


@pytest.mark.asyncio
async def test_kis_paper_fill_attributed_in_pnl_and_trade_history(tmp_path):
    broker = _make_broker(tmp_path)
    # buy then sell the same strategy → one closed round-trip.
    await broker.place_order(
        _make_req(side=Side.BUY, qty=Decimal("10"), coid="c1", strategy_id=KIS_STRATEGY)
    )
    broker.update_market(_make_market(price=110.0))
    await broker.place_order(
        _make_req(side=Side.SELL, qty=Decimal("10"), coid="c2", strategy_id=KIS_STRATEGY)
    )

    wal_path = tmp_path / "wal.jsonl"

    # PnLAggregator: per-strategy bucket must be POPULATED (was empty/dropped
    # entirely before the fix — that absence is the bug). Exact figure carries
    # MockMatchingEngine fee + spread; the load-bearing fact is that the
    # strategy is attributed at all and the round-trip booked a profit.
    agg = PnLAggregator()
    agg.replay_from_wal(wal_path)
    assert KIS_STRATEGY in agg.by_strategy
    # buy@~100 sell@~110 qty10 → strongly positive (was: KeyError / absent).
    assert agg.by_strategy[KIS_STRATEGY] > 90.0

    # trade_history: the reconstructed round-trip is attributed to the strategy.
    trades = reconstruct_trades([wal_path])
    closed = [t for t in trades if t.status == "closed"]
    assert len(closed) == 1
    assert closed[0].strategy_id == KIS_STRATEGY
    assert closed[0].symbol == KIS_SYMBOL
    assert closed[0].realized_pnl is not None and closed[0].realized_pnl > 90.0


# --- 3. Cross-run: trade_history over two run WALs with persisted strategy_id ---


@pytest.mark.asyncio
async def test_cross_run_trade_history_attribution(tmp_path):
    run_a = tmp_path / "run-a"
    run_b = tmp_path / "run-b"
    run_a.mkdir()
    run_b.mkdir()

    # Run A: open the position.
    broker_a = PaperBroker(
        wal=WAL(run_a / "wal.jsonl"),
        kill_switch=KillSwitch(),
        matching_engine=MockMatchingEngine(),
        initial_balance=Decimal("1000000"),
    )
    broker_a.update_market(_make_market(price=100.0))
    await broker_a.place_order(
        _make_req(side=Side.BUY, qty=Decimal("5"), coid="a1", strategy_id=KIS_STRATEGY)
    )

    # Run B (fresh process — in-memory maps lost): close the position.
    broker_b = PaperBroker(
        wal=WAL(run_b / "wal.jsonl"),
        kill_switch=KillSwitch(),
        matching_engine=MockMatchingEngine(),
        initial_balance=Decimal("1000000"),
    )
    broker_b.update_market(_make_market(price=120.0))
    await broker_b.place_order(
        _make_req(side=Side.SELL, qty=Decimal("5"), coid="b1", strategy_id=KIS_STRATEGY)
    )

    trades = reconstruct_trades([run_a / "wal.jsonl", run_b / "wal.jsonl"])
    closed = [t for t in trades if t.status == "closed"]
    assert len(closed) == 1
    # The whole point: a fill opened in run A and closed in run B (separate
    # processes, in-memory coid→strategy map lost) still attributes because
    # strategy_id is PERSISTED in the order_filled WAL payload.
    assert closed[0].strategy_id == KIS_STRATEGY
    # buy@~100 sell@~120 qty5 → strongly positive.
    assert closed[0].realized_pnl is not None and closed[0].realized_pnl > 90.0


# --- 4. Regression: legacy WAL with no strategy_id still replays (no KeyError) ---


def test_legacy_wal_without_strategy_id_replays_without_crash(tmp_path):
    """Old order_filled events (no strategy_id field, opaque coid) must still
    replay without raising — they are dropped (logged), exactly as before.
    """
    wal_path = tmp_path / "legacy.jsonl"
    wal = WAL(wal_path)
    from src.live.types import WALEvent

    wal.write(
        WALEvent(
            ts="2026-05-01T00:00:00+00:00",
            event_type="order_filled",
            payload={
                "client_order_id": "opaque-no-colon-hash",
                "broker_order_id": "b1",
                "symbol": KIS_SYMBOL,
                "side": "buy",
                "qty": "10",
                "fill_price": "100",
                "fill_qty": "10",
                "fees": "0",
                "fee_asset": "KRW",
                "server_ts": None,
            },
        )
    )

    agg = PnLAggregator()
    agg.replay_from_wal(wal_path)  # must not raise
    assert agg.by_strategy == {}  # dropped — unattributable, as before

    trades = reconstruct_trades([wal_path])  # must not raise
    assert trades == []


# --- 4b. Regression: OrderRequest still constructible without strategy_id ---


def test_order_request_strategy_id_defaults_none():
    req = OrderRequest(
        client_order_id="c",
        symbol=KIS_SYMBOL,
        side=Side.BUY,
        qty=Decimal("1"),
        order_type=OrderType.MARKET,
        price=None,
        tif=TimeInForce.GTC,
    )
    assert req.strategy_id is None
