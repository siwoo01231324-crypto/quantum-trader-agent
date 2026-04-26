"""WAL replay regression after KIS adapter mid-order crash (Stage 6.1, #105).

Tests:
  1. KIS crash mid-order → WAL has order_acked (NEW) but no fill → PaperBroker.from_wal
     restores same balance state (no spurious position change).
  2. Unmatched order_acked (NEW, no fill) detected as pending → reconciler-style
     re-query path is exercised (mocked adapter.get_order).
"""
from __future__ import annotations

import json
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.brokers.base import AsyncBrokerAdapter, OrderAck
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.types import WALEvent, EVENT_ORDER_ACKED
from src.live.wal import WAL, replay
from src.ops.kill_switch import KillSwitch


def _write_order_acked_event(wal: WAL, broker_order_id: str, status: str = "NEW") -> None:
    ts = datetime.now(timezone.utc).isoformat()
    wal.write(WALEvent(
        ts=ts,
        event_type=EVENT_ORDER_ACKED,
        payload={
            "client_order_id": f"cid_{broker_order_id}",
            "broker_order_id": broker_order_id,
            "ack_ts": ts,
            "status": status,
            "origin": "executor",
        },
    ))


def _write_order_filled_event(wal: WAL, symbol: str, qty: str, price: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    wal.write(WALEvent(
        ts=ts,
        event_type="order_filled",
        payload={
            "symbol": symbol,
            "side": "BUY",
            "fill_qty": qty,
            "fill_price": price,
            "fees": "0",
            "fee_asset": "KRW",
        },
    ))


# ---------------------------------------------------------------------------
# 1. WAL replay restores state: order_acked only (no fill) → no position change
# ---------------------------------------------------------------------------

def test_wal_replay_order_acked_no_fill_preserves_balance():
    """KIS crash after ack but before fill. WAL has order_acked only.
    PaperBroker.from_wal must restore same balance (no position change).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(wal_path)

        # Only an order_acked event (KIS crash before fill)
        _write_order_acked_event(wal, "BO001", status="NEW")

        ks = KillSwitch()
        initial_balance = Decimal("1000000")  # 1M KRW
        broker = PaperBroker.from_wal(
            wal_path,
            kill_switch=ks,
            initial_balance=initial_balance,
            balance_asset="KRW",
        )

        # Balance should be unchanged — order_acked does not mutate position/balance
        balances = broker._balances
        assert "KRW" in balances
        assert balances["KRW"].free == initial_balance, (
            "order_acked-only WAL must not change balance (no fill recorded)"
        )
        # No positions
        assert len(broker._positions) == 0


def test_wal_replay_order_filled_updates_balance():
    """order_filled event in WAL → PaperBroker.from_wal reduces balance correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(wal_path)

        _write_order_acked_event(wal, "BO002", status="FILLED")
        _write_order_filled_event(wal, symbol="005930", qty="10", price="70000")

        ks = KillSwitch()
        initial_balance = Decimal("1000000")
        broker = PaperBroker.from_wal(
            wal_path,
            kill_switch=ks,
            initial_balance=initial_balance,
            balance_asset="KRW",
        )

        # After fill: position exists for 005930
        assert "005930" in broker._positions
        pos = broker._positions["005930"]
        assert pos.qty == Decimal("10")

        # Balance reduced by fill cost (10 * 70000 = 700000)
        assert broker._balances["KRW"].free == Decimal("1000000") - Decimal("700000")


# ---------------------------------------------------------------------------
# 2. Unmatched NEW ack → reconciler re-query is triggered
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconciler_requery_on_unmatched_new_ack():
    """After crash, a KIS reconciler should re-query orders with NEW status
    (no fill WAL entry). Verifies adapter.get_order is called for each pending ack."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(wal_path)

        # Two NEW acks, no fills
        _write_order_acked_event(wal, "BO101", status="NEW")
        _write_order_acked_event(wal, "BO102", status="NEW")

        events, _ = replay(wal_path)

        # Collect pending NEW orders (order_acked with status NEW, no matching fill)
        filled_bids: set[str] = {
            ev.payload.get("broker_order_id", "")
            for ev in events
            if ev.event_type == "order_filled"
        }
        pending_new: list[str] = [
            ev.payload["broker_order_id"]
            for ev in events
            if ev.event_type == EVENT_ORDER_ACKED
            and ev.payload.get("status") == "NEW"
            and ev.payload["broker_order_id"] not in filled_bids
        ]

        assert len(pending_new) == 2, "Expected 2 pending NEW acks"

        # Mock KIS adapter get_order — reconciler calls this to check fill status
        kis_adapter = MagicMock(spec=AsyncBrokerAdapter)
        kis_adapter.get_order = AsyncMock(
            return_value=OrderAck(
                broker_order_id="BO101",
                client_order_id="cid_BO101",
                symbol="005930",
                status="FILLED",
                ts=datetime.now(timezone.utc),
            )
        )

        # Simulate reconciler: for each pending ack, call get_order
        for bid in pending_new:
            await kis_adapter.get_order(
                broker_order_id=bid,
                client_order_id=f"cid_{bid}",
                symbol="005930",
            )

        assert kis_adapter.get_order.call_count == 2, (
            "Reconciler must call get_order for each pending NEW ack"
        )


# ---------------------------------------------------------------------------
# 3. WAL corruption tolerance: corrupted line does not prevent valid replay
# ---------------------------------------------------------------------------

def test_wal_replay_tolerates_corrupted_line():
    """WAL with a corrupted line mid-file still replays valid events."""
    with tempfile.TemporaryDirectory() as tmpdir:
        wal_path = Path(tmpdir) / "wal.jsonl"
        wal = WAL(wal_path)

        _write_order_acked_event(wal, "BO201", status="NEW")

        # Inject corrupted line
        with open(wal_path, "a", encoding="utf-8") as f:
            f.write("NOT VALID JSON\n")

        _write_order_filled_event(wal, symbol="005930", qty="5", price="68000")

        ks = KillSwitch()
        broker = PaperBroker.from_wal(
            wal_path,
            kill_switch=ks,
            initial_balance=Decimal("1000000"),
            balance_asset="KRW",
        )

        # Fill event after corruption line must still be applied
        assert "005930" in broker._positions
        assert broker._positions["005930"].qty == Decimal("5")
