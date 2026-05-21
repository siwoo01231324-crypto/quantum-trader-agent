"""Tests for ``LivePriceCache`` — thread-safe latest-mark-price store
populated by ``BinanceMarkPriceFeed`` and read by the dashboard.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from decimal import Decimal

from src.live.price_cache import LivePriceCache, PriceSnapshot


def _ts() -> datetime:
    return datetime.now(timezone.utc)


def test_get_returns_none_for_unknown_symbol() -> None:
    cache = LivePriceCache()
    assert cache.get_price("BTCUSDT") is None


def test_set_and_get_round_trip() -> None:
    cache = LivePriceCache()
    now = _ts()
    cache.set_price("BTCUSDT", Decimal("30000.5"), now)
    snap = cache.get_price("BTCUSDT")
    assert snap is not None
    assert snap.price == Decimal("30000.5")
    assert snap.ts == now
    assert isinstance(snap, PriceSnapshot)


def test_symbol_lookup_is_case_insensitive() -> None:
    cache = LivePriceCache()
    cache.set_price("ethusdt", Decimal("1800"), _ts())
    assert cache.get_price("ETHUSDT").price == Decimal("1800")
    assert cache.get_price("EthUsdt").price == Decimal("1800")


def test_set_accepts_non_decimal_input() -> None:
    """Defensive: float / str inputs are coerced to Decimal so callers
    can't accidentally store a binary-float representation error."""
    cache = LivePriceCache()
    cache.set_price("BTCUSDT", 30000.5, _ts())
    assert cache.get_price("BTCUSDT").price == Decimal("30000.5")
    cache.set_price("ETHUSDT", "1800.25", _ts())
    assert cache.get_price("ETHUSDT").price == Decimal("1800.25")


def test_latest_write_wins() -> None:
    cache = LivePriceCache()
    cache.set_price("BTCUSDT", Decimal("30000"), _ts())
    cache.set_price("BTCUSDT", Decimal("30100"), _ts())
    assert cache.get_price("BTCUSDT").price == Decimal("30100")


def test_snapshot_returns_independent_copy() -> None:
    cache = LivePriceCache()
    cache.set_price("BTCUSDT", Decimal("30000"), _ts())
    snap = cache.snapshot()
    cache.set_price("ETHUSDT", Decimal("1800"), _ts())
    # snapshot() at first call had only BTCUSDT; later mutation invisible
    assert set(snap.keys()) == {"BTCUSDT"}
    # but the cache itself now has both
    assert set(cache.snapshot().keys()) == {"BTCUSDT", "ETHUSDT"}


def test_concurrent_writes_do_not_corrupt() -> None:
    """500 threads each writing 100 prices must not raise or lose entries
    (only the latest per symbol need persist — we just check no exception)."""
    cache = LivePriceCache()
    errors: list[BaseException] = []

    def writer(start: int) -> None:
        try:
            now = _ts()
            for i in range(100):
                cache.set_price(f"SYM{i % 50}USDT", Decimal(str(start + i)), now)
        except BaseException as err:
            errors.append(err)

    threads = [threading.Thread(target=writer, args=(t,)) for t in range(500)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(cache) == 50  # SYM0..SYM49


def test_len_matches_distinct_symbols() -> None:
    cache = LivePriceCache()
    assert len(cache) == 0
    cache.set_price("BTCUSDT", Decimal("30000"), _ts())
    cache.set_price("ETHUSDT", Decimal("1800"), _ts())
    cache.set_price("BTCUSDT", Decimal("30100"), _ts())  # update, not new
    assert len(cache) == 2
