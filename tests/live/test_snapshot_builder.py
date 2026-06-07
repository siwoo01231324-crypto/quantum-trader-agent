"""Tests for src.live.snapshot_builder (#177)."""
from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from src.live.snapshot_builder import (
    SnapshotBuilder,
    SnapshotBuilderConfig,
    is_krx_symbol,
)
from src.live.types import Tick


def test_is_krx_symbol_recognises_6_digit_codes():
    assert is_krx_symbol("005930") is True
    assert is_krx_symbol("000660") is True
    assert is_krx_symbol("BTCUSDT") is False
    assert is_krx_symbol("12345") is False  # 5 digits
    assert is_krx_symbol("0059300") is False  # 7 digits


def test_append_tick_adds_synthetic_ohlc_row():
    builder = SnapshotBuilder(["005930"], kis_client=None)
    tick = Tick(
        symbol="005930", price=Decimal("80000"), qty=Decimal("1000"),
        ts="2026-05-04T01:00:00+00:00",
        server_ts="2026-05-04T10:00:00+09:00",
    )
    builder.append_tick(tick)
    df = builder.buffers["005930"]
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 1
    row = df.iloc[0]
    assert row["open"] == row["high"] == row["low"] == row["close"] == 80000.0
    assert row["volume"] == 1000.0


def test_append_tick_dedup_same_minute_bar():
    builder = SnapshotBuilder(["005930"], kis_client=None)
    tick_a = Tick(
        symbol="005930", price=Decimal("80000"), qty=Decimal("1000"),
        ts="2026-05-04T01:00:30+00:00",  # same minute
    )
    tick_b = Tick(
        symbol="005930", price=Decimal("80100"), qty=Decimal("1100"),
        ts="2026-05-04T01:00:45+00:00",
    )
    builder.append_tick(tick_a)
    builder.append_tick(tick_b)
    assert len(builder.buffers["005930"]) == 1


def test_build_snapshot_shape_for_strategy_consumption():
    builder = SnapshotBuilder(
        ["005930"], kis_client=None,
        config=SnapshotBuilderConfig(equity_krw=200000.0),
    )
    tick = Tick(
        symbol="005930", price=Decimal("80000"), qty=Decimal("500"),
        ts="2026-05-04T01:00:00+00:00",
    )
    snap = builder.build_snapshot(tick)
    assert snap["symbol"] == "005930"
    assert snap["price"] == 80000.0
    assert snap["equity_krw"] == 200000.0
    assert isinstance(snap["history"], pd.DataFrame)
    assert "005930" in snap["ohlcv_history"]
    assert "rsi" in snap["factors"]


@pytest.mark.asyncio
async def test_warmup_no_kis_client_leaves_buffers_empty():
    """Without a KIS client, KRX symbols should warm to empty rather than raise."""
    builder = SnapshotBuilder(["005930", "BTCUSDT"], kis_client=None)
    await builder.warmup()
    for sym in ("005930", "BTCUSDT"):
        assert sym in builder.buffers
        assert len(builder.buffers[sym]) == 0


@pytest.mark.asyncio
async def test_warmup_invokes_fetch_for_krx(monkeypatch):
    captured = {}

    async def _fake_to_thread(func, *args, **kwargs):
        captured["called_with"] = args
        return func(*args, **kwargs)

    def _fake_fetch(client, symbol, target_date, interval="1"):
        captured["fetch_args"] = (symbol, target_date, interval)
        # Return an empty list — we only assert the call shape
        return []

    monkeypatch.setattr("asyncio.to_thread", _fake_to_thread)
    monkeypatch.setattr(
        "src.brokers.kis.price_client.fetch_intraday_ohlcv_raw",
        _fake_fetch,
    )

    builder = SnapshotBuilder(["005930"], kis_client=object())
    await builder.warmup()
    assert captured["fetch_args"][0] == "005930"
    assert captured["fetch_args"][2] == "1"


class _FakeBalanceProvider:
    """Minimal balance provider exposing AccountInfoProvider.fetch() shape."""

    def __init__(self, bal: dict) -> None:
        self._bal = bal

    def fetch(self) -> dict:
        return self._bal


_BAL = {
    "binance": {"ok": True, "available_usdt": 5596.32},
    "bitget": {"ok": True, "available_usdt": 483.63},
    "kis": {"ok": True, "cash_balance": 9734720.0},
}


def test_usdt_equity_venue_bitget_sizes_against_bitget_balance():
    """bitget 거래는 bitget 잔고로 equity_usdt 를 잡아야 한다.

    회귀: 옛 코드는 항상 binance 잔고($5596)를 equity_usdt 로 써서, bitget
    주문(잔고 $483)이 11.6배 부풀려졌다 (2026-06-08 BEAT 명목 $2810 사고).
    """
    sb = SnapshotBuilder(
        ["BTCUSDT"], balance_provider=_FakeBalanceProvider(_BAL),
        usdt_equity_venue="bitget",
    )
    snap: dict = {}
    sb._inject_real_equity(snap)
    assert snap["equity_usdt"] == pytest.approx(483.63)
    # 0.50 사이징 → 명목 ≈ $242 (의도값), $2810 (버그값) 아님.
    assert 0.50 * snap["equity_usdt"] == pytest.approx(241.8, abs=1.0)


def test_usdt_equity_venue_defaults_to_binance():
    """기본값(미지정)은 binance 잔고 — 기존 동작 byte-호환 보존."""
    sb = SnapshotBuilder(
        ["BTCUSDT"], balance_provider=_FakeBalanceProvider(_BAL),
    )
    snap: dict = {}
    sb._inject_real_equity(snap)
    assert snap["equity_usdt"] == pytest.approx(5596.32)
