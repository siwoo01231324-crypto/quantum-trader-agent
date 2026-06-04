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
