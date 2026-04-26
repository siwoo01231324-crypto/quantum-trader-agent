"""Unit tests for KISIntradayBar schema and TR_ID_INTRADAY_PRICE constant."""
from __future__ import annotations

import pytest

from src.brokers.kis.tr_ids import TR_ID_INTRADAY_PRICE
from src.brokers.kis.schemas import KISIntradayBar


class TestTrIdConstant:
    def test_tr_id_intraday_price_value(self):
        assert TR_ID_INTRADAY_PRICE == "FHKST03010200"


class TestKISIntradayBarFloatCoerce:
    def test_string_fields_coerced_to_float(self):
        bar = KISIntradayBar(
            date="20260425",
            time="093000",
            open="70000",
            high="71000",
            low="69500",
            close="70500",
            volume="1234567",
            trade_amt="86500000000",
        )
        assert isinstance(bar.open, float)
        assert isinstance(bar.high, float)
        assert isinstance(bar.low, float)
        assert isinstance(bar.close, float)
        assert isinstance(bar.volume, float)
        assert isinstance(bar.trade_amt, float)
        assert bar.open == pytest.approx(70000.0)
        assert bar.close == pytest.approx(70500.0)

    def test_empty_string_coerced_to_zero(self):
        bar = KISIntradayBar(
            date="20260425",
            time="093000",
            open="",
            high="",
            low="",
            close="",
            volume="",
            trade_amt="",
        )
        assert bar.open == 0.0
        assert bar.volume == 0.0
        assert bar.trade_amt == 0.0


class TestKISIntradayBarTimePreservation:
    def test_time_hhmmss_preserved_as_string(self):
        bar = KISIntradayBar(
            date="20260425",
            time="093000",
            open="70000",
            high="71000",
            low="69500",
            close="70500",
            volume="100",
            trade_amt="7000000",
        )
        assert bar.time == "093000"
        assert bar.date == "20260425"

    def test_time_with_leading_zero_preserved(self):
        bar = KISIntradayBar(
            date="20260425",
            time="090000",
            open="70000",
            high="71000",
            low="69500",
            close="70500",
            volume="100",
            trade_amt="7000000",
        )
        assert bar.time == "090000"
