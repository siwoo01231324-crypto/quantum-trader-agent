"""Unit tests for PnLAggregator (#194).

Aggregates realized PnL from broker fill events into the dashboard's
realtime / daily / monthly figures, plus per-strategy breakdowns. KST 09:00
business-date boundaries (KRX trading day) drive the daily/monthly resets.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from src.live.pnl_aggregator import PnLAggregator
from src.live.types import WALEvent
from src.live.wal import WAL


KST = ZoneInfo("Asia/Seoul")


def _kst(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str).replace(tzinfo=KST)


def _aggregator(now_kst: datetime) -> PnLAggregator:
    return PnLAggregator(kst_now=lambda: now_kst)


def test_empty_aggregator_returns_zeros():
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    assert agg.realtime == 0.0
    assert agg.daily == 0.0
    assert agg.monthly == 0.0
    assert agg.by_strategy == {}


def test_buy_only_realized_equals_negative_fee():
    """A pure buy doesn't realize gain — only the fee shows up as -fee."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="alpha",
        symbol="BTCUSDT",
        side="buy",
        qty=Decimal("1"),
        price=Decimal("50000"),
        fee=Decimal("5"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.realtime == -5.0
    assert agg.by_strategy == {"alpha": -5.0}


def test_buy_then_sell_realized_profit():
    """buy 1 @ 100, sell 1 @ 110 → realized = (110-100)*1 - fees."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T13:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("1"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    # buy realized = 0, sell realized = (110-100)*1 - 1 = 9
    assert agg.realtime == 9.0
    assert agg.by_strategy == {"alpha": 9.0}


def test_average_cost_basis_three_buys():
    """3 buys at different prices → avg_cost = weighted average."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    # buy 1 @ 100, 1 @ 200, 2 @ 300 → avg = (100+200+600)/4 = 225
    for price in [Decimal("100"), Decimal("200")]:
        agg.record_fill(
            strategy_id="alpha", symbol="BTCUSDT", side="buy",
            qty=Decimal("1"), price=price, fee=Decimal("0"),
            ts=_kst("2026-05-06T14:00:00"),
        )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("2"), price=Decimal("300"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    # Now sell 1 @ 250 → realized = (250 - 225) * 1 = 25
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("250"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.realtime == 25.0


def test_strategies_isolated():
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="beta", symbol="005930", side="buy",
        qty=Decimal("10"), price=Decimal("50000"), fee=Decimal("100"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.by_strategy == {"alpha": 10.0, "beta": -100.0}
    assert agg.daily_for("alpha") == 10.0
    assert agg.daily_for("beta") == -100.0


def test_daily_only_includes_today_fills():
    """Yesterday fill must NOT contribute to today's daily."""
    now_kst = _kst("2026-05-06T14:00:00")
    agg = _aggregator(now_kst)
    # Yesterday close-and-realize +20
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-05T11:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("120"), fee=Decimal("0"),
        ts=_kst("2026-05-05T15:00:00"),
    )
    # Today realize +5
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T10:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("105"), fee=Decimal("0"),
        ts=_kst("2026-05-06T13:00:00"),
    )
    assert agg.realtime == 25.0  # 20 + 5
    assert agg.daily == 5.0


def test_kst_0900_business_date_boundary():
    """A fill at KST 08:30 on 2026-05-06 belongs to the *previous* business
    day (2026-05-05). Today (now=2026-05-06 14:00) → should NOT count."""
    now_kst = _kst("2026-05-06T14:00:00")
    agg = _aggregator(now_kst)

    # Pre-position the realised gain at 08:30 KST on 2026-05-06 (= 2026-05-05 BD)
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T08:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"),
        ts=_kst("2026-05-06T08:30:00"),
    )

    assert agg.realtime == 10.0
    assert agg.daily == 0.0  # 08:30 fill belongs to yesterday's BD


def test_replay_from_wal_reconstructs_state(tmp_path: Path):
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)
    wal.write(WALEvent(
        ts="2026-05-06T05:00:00+00:00",  # 14:00 KST
        event_type="order_filled",
        payload={
            "client_order_id": "alpha:BTCUSDT:1700000000000:0",
            "symbol": "BTCUSDT",
            "side": "buy",
            "fill_qty": "1",
            "fill_price": "100",
            "fees": "0",
        },
    ))
    wal.write(WALEvent(
        ts="2026-05-06T06:00:00+00:00",  # 15:00 KST
        event_type="order_filled",
        payload={
            "client_order_id": "alpha:BTCUSDT:1700000060000:1",
            "symbol": "BTCUSDT",
            "side": "sell",
            "fill_qty": "1",
            "fill_price": "115",
            "fees": "0",
        },
    ))

    agg = _aggregator(_kst("2026-05-06T16:00:00"))
    agg.replay_from_wal(wal_path)

    assert agg.realtime == 15.0
    assert agg.daily == 15.0
    assert agg.by_strategy == {"alpha": 15.0}


def test_daily_resets_when_business_date_advances():
    """If now_kst crosses into a new business date, daily auto-resets to 0."""
    fixed_now = [_kst("2026-05-06T14:00:00")]
    agg = PnLAggregator(kst_now=lambda: fixed_now[0])
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.daily == 10.0

    # Advance time past KST 09:00 next day
    fixed_now[0] = _kst("2026-05-07T09:30:00")
    assert agg.daily == 0.0
    assert agg.realtime == 10.0  # cumulative untouched


def test_legacy_payload_falls_back_to_client_order_id_prefix():
    """WAL `order_filled` payload has no strategy_id field today (PaperBroker
    doesn't inject it). The aggregator parses the `{strategy}:...` prefix."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.ingest_fill_event("order_filled", {
        "client_order_id": "alpha:BTCUSDT:1700000000000:0",
        "symbol": "BTCUSDT",
        "side": "buy",
        "fill_qty": "1",
        "fill_price": "100",
        "fees": "5",
    })
    assert agg.by_strategy == {"alpha": -5.0}
