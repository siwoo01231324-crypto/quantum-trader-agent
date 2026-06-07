"""Unit tests for src.brokers.bitget.async_ws._parse_fill_from_order.

Pure-Python parse function — no WS connection needed.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.brokers.bitget.async_ws import _parse_fill_from_order


def _fill_row(**overrides) -> dict:
    base = {
        "instId": "BTCUSDT",
        "orderId": "ord-1",
        "clientOid": "cid-1",
        "side": "buy",
        "status": "filled",
        "fillSize": "0.005",
        "fillPrice": "50100",
        "fee": "-0.05",
        "feeCcy": "USDT",
        "uTime": "1700000000000",
        "tradeId": "trade-1",
        "execType": "T",
    }
    base.update(overrides)
    return base


def test_filled_status_emits_fill():
    seen: set[tuple[str, str]] = set()
    fill = _parse_fill_from_order(_fill_row(), seen)
    assert fill is not None
    assert fill.broker_order_id == "ord-1"
    assert fill.client_order_id == "cid-1"
    assert fill.trade_id == "trade-1"
    assert fill.qty == Decimal("0.005")
    assert fill.price == Decimal("50100")
    assert fill.fee == Decimal("-0.05")
    assert fill.fee_asset == "USDT"
    assert fill.is_maker is False  # execType=T


def test_partially_filled_emits_fill():
    fill = _parse_fill_from_order(_fill_row(status="partially_filled"), set())
    assert fill is not None


@pytest.mark.parametrize("status", ["live", "canceled", "new", "cancelled"])
def test_non_fill_status_skipped(status: str):
    fill = _parse_fill_from_order(_fill_row(status=status), set())
    assert fill is None


def test_dedup_repeats_returns_none():
    seen: set[tuple[str, str]] = set()
    row = _fill_row()
    first = _parse_fill_from_order(row, seen)
    second = _parse_fill_from_order(row, seen)
    assert first is not None
    assert second is None
    assert ("ord-1", "trade-1") in seen


def test_missing_tradeid_falls_back_to_final_marker():
    row = _fill_row()
    row.pop("tradeId")
    fill = _parse_fill_from_order(row, set())
    assert fill is not None
    assert fill.trade_id.startswith("final-")


def test_maker_via_exectype_m():
    fill = _parse_fill_from_order(_fill_row(execType="M"), set())
    assert fill is not None
    assert fill.is_maker is True


def test_fallback_to_priceavg_when_fillprice_missing():
    row = _fill_row()
    row.pop("fillPrice")
    row["priceAvg"] = "49999"
    fill = _parse_fill_from_order(row, set())
    assert fill is not None
    assert fill.price == Decimal("49999")


def test_partial_fills_use_cumulative_delta_not_full_size():
    """#중복회계 회귀 (2026-06-08 BSBUSDT 8× 사고).

    Bitget orders 채널은 fill 마다 *누적* accBaseVolume 를 push 한다. 옛 코드는
    누락된 ``fillSize`` 대신 주문 전체 ``size`` 로 fallback → partial fill N 회면
    qty 가 N × size 로 부풀려졌다 (logical -63856 vs broker -7982). acc dict 로
    누적의 *증분* 만 기록해야 합산이 주문수량과 일치한다.
    """
    seen: set = set()
    acc: dict = {}
    pushes = [
        {"orderId": "O1", "tradeId": "t1", "status": "partially_filled",
         "accBaseVolume": "1000", "size": "7982", "side": "sell", "fillPrice": "0.35"},
        {"orderId": "O1", "tradeId": "t2", "status": "partially_filled",
         "accBaseVolume": "3000", "size": "7982", "side": "sell", "fillPrice": "0.35"},
        {"orderId": "O1", "tradeId": "t3", "status": "filled",
         "accBaseVolume": "7982", "size": "7982", "side": "sell", "fillPrice": "0.35"},
        # 중복 push (같은 tradeId) — dedup 돼야 함.
        {"orderId": "O1", "tradeId": "t3", "status": "filled",
         "accBaseVolume": "7982", "size": "7982", "side": "sell", "fillPrice": "0.35"},
    ]
    total = Decimal("0")
    fills = 0
    for p in pushes:
        f = _parse_fill_from_order(p, seen, acc)
        if f is not None:
            total += f.qty
            fills += 1
    assert total == Decimal("7982"), f"expected 7982, got {total}"
    assert fills == 3, f"expected 3 distinct fills, got {fills}"


def test_acc_none_preserves_legacy_single_fill():
    """acc 미전달(레거시/테스트) 시엔 누적값을 그대로 qty 로 — 단일 fill 가정."""
    seen: set = set()
    f = _parse_fill_from_order(
        {"orderId": "O2", "tradeId": "t1", "status": "filled",
         "accBaseVolume": "500", "size": "500", "side": "buy", "fillPrice": "1.0"},
        seen,
    )
    assert f is not None and f.qty == Decimal("500")
