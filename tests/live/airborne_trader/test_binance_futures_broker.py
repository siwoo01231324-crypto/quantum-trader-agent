"""Unit tests for BinanceFuturesBroker — mock AsyncBinanceFuturesClient."""
from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from live.airborne_trader.brokers.binance_futures import BinanceFuturesBroker


@pytest.fixture
def mock_client():
    client = MagicMock()
    client.place_order = AsyncMock()
    client._get = AsyncMock()
    client.get_position_risk = AsyncMock()
    return client


@pytest.fixture
def broker(mock_client):
    return BinanceFuturesBroker(mock_client)


class TestPlaceMarketOrder:
    @pytest.mark.asyncio
    async def test_long_entry_sets_reduce_only_false(self, broker, mock_client):
        ack = MagicMock(avg_price=100.5, filled_qty=2.0)
        mock_client.place_order.return_value = ack

        result = await broker.place_market_order(
            symbol="BTCUSDT", side="BUY", qty=2.0,
        )

        assert result.symbol == "BTCUSDT"
        assert result.side == "BUY"
        assert result.filled_qty == 2.0
        assert result.avg_price == 100.5
        # Inspect OrderRequest passed
        call_args = mock_client.place_order.call_args
        req = call_args.args[0]
        assert req.symbol == "BTCUSDT"
        assert req.side.value == "BUY"
        assert req.qty == Decimal("2.0")
        assert req.reduce_only is False
        assert req.client_order_id.startswith("airb-")
        assert req.strategy_id == "airborne_trader_daemon"

    @pytest.mark.asyncio
    async def test_short_entry_sell(self, broker, mock_client):
        ack = MagicMock(avg_price=100.5, filled_qty=2.0)
        mock_client.place_order.return_value = ack
        await broker.place_market_order(symbol="BTCUSDT", side="SELL", qty=2.0)
        req = mock_client.place_order.call_args.args[0]
        assert req.side.value == "SELL"

    @pytest.mark.asyncio
    async def test_invalid_side_raises(self, broker):
        with pytest.raises(ValueError, match="side must be BUY/SELL"):
            await broker.place_market_order(symbol="X", side="LONG", qty=1)

    @pytest.mark.asyncio
    async def test_client_order_id_unique(self, broker, mock_client):
        mock_client.place_order.return_value = MagicMock(avg_price=1, filled_qty=1)
        await broker.place_market_order(symbol="X", side="BUY", qty=1)
        await broker.place_market_order(symbol="X", side="BUY", qty=1)
        id1 = mock_client.place_order.call_args_list[0].args[0].client_order_id
        id2 = mock_client.place_order.call_args_list[1].args[0].client_order_id
        assert id1 != id2
        assert id1.startswith("airb-") and id2.startswith("airb-")


class TestClosePosition:
    @pytest.mark.asyncio
    async def test_reduce_only_true_forced(self, broker, mock_client):
        ack = MagicMock(avg_price=106.0, filled_qty=2.0)
        mock_client.place_order.return_value = ack
        result = await broker.close_position(
            symbol="BTCUSDT", side="SELL", qty=2.0,
        )
        req = mock_client.place_order.call_args.args[0]
        assert req.reduce_only is True
        assert req.side.value == "SELL"
        assert result.raw_response["reduce_only"] is True


class TestGetMarkPrice:
    @pytest.mark.asyncio
    async def test_dict_response(self, broker, mock_client):
        mock_client._get.return_value = {"symbol": "BTCUSDT", "markPrice": "65432.10"}
        price = await broker.get_mark_price("BTCUSDT")
        assert price == pytest.approx(65432.10)
        mock_client._get.assert_called_once_with(
            "/fapi/v1/premiumIndex",
            params={"symbol": "BTCUSDT"},
            signed=False,
        )

    @pytest.mark.asyncio
    async def test_list_response_filters_symbol(self, broker, mock_client):
        mock_client._get.return_value = [
            {"symbol": "ETHUSDT", "markPrice": "3500"},
            {"symbol": "BTCUSDT", "markPrice": "65000"},
        ]
        price = await broker.get_mark_price("BTCUSDT")
        assert price == pytest.approx(65000)

    @pytest.mark.asyncio
    async def test_missing_symbol_returns_zero(self, broker, mock_client):
        mock_client._get.return_value = [{"symbol": "ETHUSDT", "markPrice": "3500"}]
        price = await broker.get_mark_price("BTCUSDT")
        assert price == 0.0

    @pytest.mark.asyncio
    async def test_api_error_returns_zero(self, broker, mock_client):
        mock_client._get.side_effect = RuntimeError("network down")
        price = await broker.get_mark_price("BTCUSDT")
        assert price == 0.0

    @pytest.mark.asyncio
    async def test_malformed_response(self, broker, mock_client):
        mock_client._get.return_value = {"symbol": "BTCUSDT", "markPrice": "not-a-number"}
        price = await broker.get_mark_price("BTCUSDT")
        assert price == 0.0


class TestGetOpenPositionQty:
    @pytest.mark.asyncio
    async def test_sums_positions(self, broker, mock_client):
        p1 = MagicMock(position_amt="2.5")
        p2 = MagicMock(position_amt="-1.0")
        mock_client.get_position_risk.return_value = [p1, p2]
        qty = await broker.get_open_position_qty("BTCUSDT")
        assert qty == pytest.approx(1.5)

    @pytest.mark.asyncio
    async def test_empty_returns_zero(self, broker, mock_client):
        mock_client.get_position_risk.return_value = []
        assert await broker.get_open_position_qty("BTCUSDT") == 0.0

    @pytest.mark.asyncio
    async def test_api_error_returns_zero(self, broker, mock_client):
        mock_client.get_position_risk.side_effect = ConnectionError("no internet")
        assert await broker.get_open_position_qty("BTCUSDT") == 0.0
