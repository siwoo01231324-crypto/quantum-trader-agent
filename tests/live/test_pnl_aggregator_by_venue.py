"""Per-venue PnL split (currency-mixing bug fix).

`PnLAggregator._cum_realized / _daily / _monthly` are single floats that
naively summed KRW (KIS) and USDT (Binance) realized P&L into one
currency-mixed meaningless total. These tests pin the NEW per-venue
accessors:

  - `realtime_by_venue()  -> dict[str, float]`  (cumulative)
  - `daily_by_venue()     -> dict[str, float]`  (KST-09:00 business day)
  - `monthly_by_venue()   -> dict[str, float]`  (KST-09:00 business month)

Venue predicate (same as `_async_orchestrator` / `conversion.py`):
  symbol endswith "USDT" (len > 4)  -> "binance"
  6-digit numeric KRX code          -> "kis"
  else                              -> "unknown" (defensive, never crash)

The legacy scalar accessors (`realtime/daily/monthly/by_strategy/
daily_for`) MUST stay bit-identical — covered by the existing
`test_pnl_aggregator.py`; here we only assert the new split + that the
per-venue daily/monthly honor the SAME business-window reset.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from src.live.pnl_aggregator import PnLAggregator

KST = ZoneInfo("Asia/Seoul")


def _kst(dt_str: str) -> datetime:
    return datetime.fromisoformat(dt_str).replace(tzinfo=KST)


def _aggregator(now_kst: datetime) -> PnLAggregator:
    return PnLAggregator(kst_now=lambda: now_kst)


def test_empty_by_venue_returns_empty_dicts():
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    assert agg.realtime_by_venue() == {}
    assert agg.daily_by_venue() == {}
    assert agg.monthly_by_venue() == {}


def test_binance_and_kis_kept_in_separate_buckets():
    """A Binance USDT trade and a KIS KRX trade must NOT be summed."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    # Binance: buy 1 @ 100, sell 1 @ 110 → +10 USDT
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T13:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    # KIS: buy 10 @ 50000, sell 10 @ 50100 → +1000 KRW
    agg.record_fill(
        strategy_id="beta", symbol="005930", side="buy",
        qty=Decimal("10"), price=Decimal("50000"), fee=Decimal("0"),
        ts=_kst("2026-05-06T13:00:00"),
    )
    agg.record_fill(
        strategy_id="beta", symbol="005930", side="sell",
        qty=Decimal("10"), price=Decimal("50100"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.realtime_by_venue() == {"binance": 10.0, "kis": 1000.0}
    assert agg.daily_by_venue() == {"binance": 10.0, "kis": 1000.0}
    assert agg.monthly_by_venue() == {"binance": 10.0, "kis": 1000.0}
    # Legacy scalar still returns the (meaningless) mixed sum, unchanged.
    assert agg.realtime == 1010.0


def test_legacy_scalars_remain_bit_identical_with_venue_split():
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="alpha", symbol="ETHUSDT", side="buy",
        qty=Decimal("2"), price=Decimal("2000"), fee=Decimal("3"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    agg.record_fill(
        strategy_id="beta", symbol="000660", side="buy",
        qty=Decimal("5"), price=Decimal("100000"), fee=Decimal("7"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    # buy-only → realized = -fee for each
    assert agg.realtime == -10.0
    assert agg.by_strategy == {"alpha": -3.0, "beta": -7.0}
    assert agg.realtime_by_venue() == {"binance": -3.0, "kis": -7.0}


def test_unknown_venue_bucket_never_crashes():
    """A non-USDT, non-6-digit symbol classifies into 'unknown', no crash."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="weird", symbol="AAPL", side="buy",
        qty=Decimal("1"), price=Decimal("200"), fee=Decimal("1"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert agg.realtime_by_venue() == {"unknown": -1.0}
    assert agg.daily_by_venue() == {"unknown": -1.0}


def test_short_usdt_symbol_is_unknown_not_binance():
    """`len("USDT")` boundary: bare 'USDT' is NOT a tradable pair."""
    agg = _aggregator(_kst("2026-05-06T14:00:00"))
    agg.record_fill(
        strategy_id="x", symbol="USDT", side="buy",
        qty=Decimal("1"), price=Decimal("1"), fee=Decimal("0"),
        ts=_kst("2026-05-06T14:00:00"),
    )
    assert "binance" not in agg.realtime_by_venue()
    assert agg.realtime_by_venue() == {"unknown": 0.0}


def test_per_venue_daily_excludes_yesterday_fills():
    """Per-venue daily honors the SAME KST business-date as the scalar."""
    now_kst = _kst("2026-05-06T14:00:00")
    agg = _aggregator(now_kst)
    # Yesterday Binance close-and-realize +20 (must NOT hit daily)
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
    # Today Binance realize +5
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
    assert agg.realtime_by_venue() == {"binance": 25.0}  # 20 + 5
    assert agg.daily_by_venue() == {"binance": 5.0}


def test_per_venue_daily_resets_when_business_date_advances():
    """2026-05-22 변경: KST 자정 (00:00) 경계로 per-venue daily 리셋."""
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
    assert agg.daily_by_venue() == {"binance": 10.0}
    # Advance past KST 자정
    fixed_now[0] = _kst("2026-05-07T00:30:00")
    assert agg.daily_by_venue() == {}
    assert agg.realtime_by_venue() == {"binance": 10.0}  # cumulative untouched


def test_per_venue_monthly_resets_when_business_month_advances():
    fixed_now = [_kst("2026-05-31T14:00:00")]
    agg = PnLAggregator(kst_now=lambda: fixed_now[0])
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-31T14:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("130"), fee=Decimal("0"),
        ts=_kst("2026-05-31T14:00:00"),
    )
    assert agg.monthly_by_venue() == {"binance": 30.0}
    # Advance into June (past KST 09:00 on a June day)
    fixed_now[0] = _kst("2026-06-01T10:00:00")
    assert agg.monthly_by_venue() == {}
    assert agg.realtime_by_venue() == {"binance": 30.0}


def test_per_venue_midnight_boundary_for_binance():
    """2026-05-22 변경: KST 자정 경계. 어제 23:30 KST 의 binance fill 은 어제
    BD → 오늘 daily 미포함. Crypto 24/7 운영 직관과 일치.
    """
    now_kst = _kst("2026-05-06T14:00:00")
    agg = _aggregator(now_kst)
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="buy",
        qty=Decimal("1"), price=Decimal("100"), fee=Decimal("0"),
        ts=_kst("2026-05-05T23:00:00"),
    )
    agg.record_fill(
        strategy_id="alpha", symbol="BTCUSDT", side="sell",
        qty=Decimal("1"), price=Decimal("110"), fee=Decimal("0"),
        ts=_kst("2026-05-05T23:30:00"),
    )
    assert agg.realtime_by_venue() == {"binance": 10.0}
    assert agg.daily_by_venue() == {}  # 어제 BD — 오늘 daily 미포함
