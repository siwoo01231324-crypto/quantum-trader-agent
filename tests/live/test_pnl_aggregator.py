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


# ---------------------------------------------------------------------------
# #238 — SHORT cost-basis tracking
#
# Opening a naked short (sell with no prior long) previously left avg=0 in
# _cost_basis, so LivePositionRiskManager had no entry price to gate against
# (root incident: momo-btc-v2 -1 BTC naked short with ZERO auto-stop). The
# sell branch now records the short's average entry price (mirrors the buy
# averaging on the negative side). LONG behavior stays bit-identical.
# ---------------------------------------------------------------------------

def test_short_open_records_entry_avg_cost():
    """sell with no prior position → short opened, _cost_basis carries avg."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("60000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    held, avg = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("-1")
    assert avg == Decimal("60000")
    # Opening a short realizes nothing (only -fee).
    assert agg.realtime == 0.0


def test_short_add_averages_entry_price():
    """Two shorts at different prices → weighted average entry on the short."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("60000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("62000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    held, avg = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("-2")
    assert avg == Decimal("61000")  # (60000 + 62000) / 2


def test_short_cover_realizes_profit_on_price_drop():
    """Short @ 60000, cover (buy) @ 57000 → realized = (60000-57000)*1."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("60000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("57000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.realtime == 3000.0
    held, _ = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("0")


# #238 review pass — the buy-side cover-then-flip-to-long is handled, but the
# symmetric sell-side flip (long → short via an oversized sell) fell through
# to the legacy long branch: it over-realized P&L on the FULL qty (not just
# the long portion) and left the new short carrying the OLD LONG avg. A live
# P&L mis-accounting bug.

def test_long_to_short_flip_realizes_only_long_portion():
    """Long 1 @ 100, sell 3 @ 110 → realize only the 1 long (=10), open short 2 @ 110."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("3"), price=Decimal("110"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    # Only the 1-unit long realizes (110-100)*1; NOT (110-100)*3.
    assert agg.realtime == 10.0
    held, avg = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("-2"), "residual 2 units must be net short"
    assert avg == Decimal("110"), "new short basis = flip price, not stale long avg"


def test_zero_qty_sell_does_not_crash_short_open():
    """#238 review (MEDIUM): a broker zero-qty correction/liquidation-ack
    `sell` with no position must NOT ZeroDivisionError (would kill the
    aggregator → silently halt all P&L / risk gating for the session).
    """
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("0"), price=Decimal("60000"), fee=Decimal("2"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.realtime == -2.0  # only the fee
    held, avg = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("0")
    assert avg == Decimal("0")


def test_zero_qty_sell_on_existing_short_keeps_avg():
    """Zero-qty sell while holding a short leaves the entry avg untouched."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("2"), price=Decimal("60000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("0"), price=Decimal("99999"), fee=Decimal("1"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    held, avg = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("-2")
    assert avg == Decimal("60000"), "zero-qty fill must not move short entry"


def test_partial_long_sell_below_held_is_bit_identical():
    """qty < held → no flip; legacy long behavior must be byte-identical."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="buy",
        qty=Decimal("2"), price=Decimal("100"), fee=Decimal("1"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="momo", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("1"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    # buy: -1 fee; sell: (110-100)*1 - 1 = 9 → total 8.
    assert agg.realtime == 8.0
    held, avg = agg._cost_basis[("momo", "BTCUSDT")]
    assert held == Decimal("1")
    assert avg == Decimal("100"), "remaining long keeps original avg"
