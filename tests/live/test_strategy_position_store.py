"""Unit tests for StrategyPositionStore (#192).

In-memory store of per-strategy positions, fed by broker fill events.
Backs the dashboard's `position_provider` callback so a strategy ON/OFF
toggle can liquidate only that strategy's holdings.
"""
from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.live.strategy_position_store import StrategyPositionStore
from src.live.types import WALEvent
from src.live.wal import WAL


def test_empty_store_returns_empty_list():
    store = StrategyPositionStore()
    assert store.get_positions("any-strategy") == []


def test_buy_accumulates_positive_qty():
    store = StrategyPositionStore()
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="buy", qty=Decimal("0.5"))
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="buy", qty=Decimal("0.3"))

    positions = store.get_positions("r4-switch")
    assert positions == [("BTCUSDT", 0.8)]


def test_sell_subtracts_qty():
    store = StrategyPositionStore()
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="buy", qty=Decimal("1.0"))
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="sell", qty=Decimal("0.4"))

    assert store.get_positions("r4-switch") == [("BTCUSDT", 0.6)]


def test_zero_qty_position_excluded():
    store = StrategyPositionStore()
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="buy", qty=Decimal("1.0"))
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="sell", qty=Decimal("1.0"))

    assert store.get_positions("r4-switch") == []


def test_different_strategies_isolated():
    store = StrategyPositionStore()
    store.record_fill(strategy_id="r4-switch", symbol="BTCUSDT", side="buy", qty=Decimal("0.5"))
    store.record_fill(strategy_id="momo-kis-v1", symbol="005930", side="buy", qty=Decimal("100"))

    assert store.get_positions("r4-switch") == [("BTCUSDT", 0.5)]
    assert store.get_positions("momo-kis-v1") == [("005930", 100.0)]


def test_register_order_then_fill_by_client_order_id():
    store = StrategyPositionStore()
    store.register_order(client_order_id="r4-switch:BTCUSDT:1700000000000:0", strategy_id="r4-switch")
    store.record_fill_by_client_order_id(
        client_order_id="r4-switch:BTCUSDT:1700000000000:0",
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("0.25"),
    )

    assert store.get_positions("r4-switch") == [("BTCUSDT", 0.25)]


def test_replay_from_wal_reconstructs_positions(tmp_path: Path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    # New-style payloads include strategy_id directly.
    wal.write(WALEvent(
        ts="2026-05-06T00:00:00+00:00",
        event_type="order_filled",
        payload={
            "client_order_id": "r4-switch:BTCUSDT:1700000000000:0",
            "strategy_id": "r4-switch",
            "symbol": "BTCUSDT",
            "side": "buy",
            "fill_qty": "0.5",
        },
    ))
    wal.write(WALEvent(
        ts="2026-05-06T00:01:00+00:00",
        event_type="order_filled",
        payload={
            "client_order_id": "r4-switch:BTCUSDT:1700000060000:1",
            "strategy_id": "r4-switch",
            "symbol": "BTCUSDT",
            "side": "sell",
            "fill_qty": "0.2",
        },
    ))

    store = StrategyPositionStore()
    store.replay_from_wal(wal_path)

    assert store.get_positions("r4-switch") == [("BTCUSDT", 0.3)]


def test_replay_legacy_payload_falls_back_to_client_order_id_parse(tmp_path: Path):
    """Old WAL payloads (pre-#192) lack strategy_id — fallback to client_order_id prefix."""
    wal_path = tmp_path / "wal.jsonl"
    # Hand-craft legacy line without strategy_id field.
    legacy = {
        "ts": "2026-05-06T00:00:00+00:00",
        "event_type": "order_filled",
        "schema_version": 1,
        "payload": {
            "client_order_id": "r4-switch:BTCUSDT:1700000000000:0",
            "symbol": "BTCUSDT",
            "side": "buy",
            "fill_qty": "0.5",
        },
    }
    wal_path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")

    store = StrategyPositionStore()
    store.replay_from_wal(wal_path)

    assert store.get_positions("r4-switch") == [("BTCUSDT", 0.5)]


def test_ingest_fill_event_used_as_wal_observer_hook():
    """live_run wires this method into the WAL observer so every fill flows in."""
    store = StrategyPositionStore()
    store.ingest_fill_event("order_filled", {
        "client_order_id": "alpha:BTCUSDT:1700000000000:0",
        "strategy_id": "alpha",
        "symbol": "BTCUSDT",
        "side": "buy",
        "fill_qty": "0.4",
    })
    # Non-fill events are no-ops.
    store.ingest_fill_event("order_acked", {
        "client_order_id": "alpha:BTCUSDT:1700000000000:0",
        "strategy_id": "alpha",
    })

    assert store.get_positions("alpha") == [("BTCUSDT", 0.4)]


def test_decimal_precision_preserved():
    store = StrategyPositionStore()
    # 0.1 + 0.2 = 0.3 exactly (Decimal), not 0.30000000000000004 (float).
    store.record_fill(strategy_id="x", symbol="BTCUSDT", side="buy", qty=Decimal("0.1"))
    store.record_fill(strategy_id="x", symbol="BTCUSDT", side="buy", qty=Decimal("0.2"))

    positions = store.get_positions("x")
    assert positions == [("BTCUSDT", 0.3)]
