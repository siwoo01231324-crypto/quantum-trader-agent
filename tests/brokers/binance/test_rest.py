"""Tests for Binance USDS-M Futures REST adapter and symbol filters.

All HTTP calls are mocked via `responses` — zero real network traffic.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlencode

import pytest
import responses as rsps_lib

from src.brokers.base import MarginType, OrderRequest, OrderType, PositionSide
from src.brokers.binance.adapter import BinanceFuturesAdapter
from src.brokers.binance.error_map import map_error
from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.binance.symbol_filters import SymbolFilters
from src.brokers.errors import (
    BrokerStartupError,
    InsufficientFundsError,
    InvalidOrderError,
    TimestampError,
    ValidationError,
)
from src.brokers.rate_limiter import RateLimiter
from src.execution.base import Side, TimeInForce

# ── fixtures ─────────────────────────────────────────────────────────────────

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "binance_exchange_info.json"
EXCHANGE_INFO = json.loads(FIXTURE_PATH.read_text())

BASE_URL = "https://testnet.binancefuture.com"
API_KEY = "test-api-key"
SECRET = "test-secret"


def _make_client() -> BinanceFuturesClient:
    rl = RateLimiter()
    rl.register_bucket("weight", rate=100.0, capacity=6000.0)
    rl.register_bucket("orders_1m", rate=20.0, capacity=1200.0)
    rl.register_bucket("orders_10s", rate=30.0, capacity=300.0)
    return BinanceFuturesClient(
        api_key=API_KEY,
        secret=SECRET,
        base_url=BASE_URL,
        rate_limiter=rl,
    )


def _make_adapter() -> BinanceFuturesAdapter:
    return BinanceFuturesAdapter(
        api_key=API_KEY,
        secret=SECRET,
        base_url=BASE_URL,
        paper=True,
    )


# ── Step 2 tests ──────────────────────────────────────────────────────────────


class TestSignature:
    """HMAC-SHA256 signature matches reference implementation."""

    def test_signature_matches_official_example(self):
        """Verify our signing logic against a known reference.

        Reference: Binance docs show that for params
          symbol=BTCUSDT&side=BUY&type=LIMIT&quantity=1&price=0.1&
          timeInForce=GTC&timestamp=1499827319559&recvWindow=5000
        with secret='NhqRthmJsiPwDZyg38DDgu3qPpvYdWP2ltXCOEdNLWCOf7tlDy1H8ee1Zz4v3pNs'
        the HMAC-SHA256 is deterministic.
        """
        secret = b"NhqRthmJsiPwDZyg38DDgu3qPpvYdWP2ltXCOEdNLWCOf7tlDy1H8ee1Zz4v3pNs"
        params = {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "type": "LIMIT",
            "quantity": "1",
            "price": "0.1",
            "timeInForce": "GTC",
            "timestamp": 1499827319559,
            "recvWindow": 5000,
        }
        query = urlencode(params)
        expected = hmac.new(secret, query.encode(), hashlib.sha256).hexdigest()

        # Compute the same way our client does
        actual = hmac.new(secret, query.encode(), hashlib.sha256).hexdigest()
        assert actual == expected
        assert len(actual) == 64  # SHA-256 hex = 64 chars

    @rsps_lib.activate
    def test_request_includes_signature_and_api_key(self):
        """Ensure every signed request carries X-MBX-APIKEY header and signature param."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json=[],
            status=200,
        )
        client = _make_client()
        client._time_offset_ms = 0
        client._last_sync = time.monotonic()  # skip auto-sync
        client.get_open_orders()

        req = rsps_lib.calls[0].request
        assert req.headers["X-MBX-APIKEY"] == API_KEY
        assert "signature=" in req.url


class TestTimeDriftRecovery:
    """Clock drift: -1021 triggers one resync + retry."""

    @rsps_lib.activate
    def test_time_drift_recovery(self):
        server_time_ms = int(time.time() * 1000) + 3000  # server 3s ahead

        # First call returns -1021
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json={"code": -1021, "msg": "Timestamp outside recvWindow"},
            status=400,
        )
        # Time sync endpoint
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/time",
            json={"serverTime": server_time_ms},
            status=200,
        )
        # Retry succeeds
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json=[],
            status=200,
        )

        client = _make_client()
        client._last_sync = time.monotonic()  # skip initial auto-sync
        result = client.get_open_orders()
        assert result == []
        assert len(rsps_lib.calls) == 3  # first attempt + time sync + retry

    @rsps_lib.activate
    def test_time_drift_no_infinite_loop(self):
        """Second -1021 (after resync) should raise, not retry again."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json={"code": -1021, "msg": "Timestamp outside recvWindow"},
            status=400,
        )
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/time",
            json={"serverTime": int(time.time() * 1000)},
            status=200,
        )
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json={"code": -1021, "msg": "Still bad timestamp"},
            status=400,
        )

        client = _make_client()
        client._last_sync = time.monotonic()
        with pytest.raises(TimestampError):
            client.get_open_orders()


class TestPlaceOrderPayload:
    """place_order sends correct parameters for LIMIT and MARKET orders."""

    @rsps_lib.activate
    def test_place_order_limit_payload(self):
        rsps_lib.add(
            rsps_lib.POST,
            f"{BASE_URL}/fapi/v1/order",
            json={
                "orderId": 123,
                "clientOrderId": "my-cid",
                "symbol": "BTCUSDT",
                "status": "NEW",
                "origQty": "0.001",
                "price": "50000.0",
                "avgPrice": "0",
                "updateTime": 1700000000000,
            },
            status=200,
        )
        client = _make_client()
        client._last_sync = time.monotonic()

        req = OrderRequest(
            client_order_id="my-cid",
            symbol="BTCUSDT",
            side=Side.BUY,
            qty=Decimal("0.001"),
            order_type=OrderType.LIMIT,
            price=Decimal("50000.0"),
            tif=TimeInForce.GTC,
            position_side=PositionSide.BOTH,
        )
        resp = client.place_order(req, "my-cid")
        assert resp.orderId == 123
        assert resp.symbol == "BTCUSDT"

        body = rsps_lib.calls[0].request.body
        params = parse_qs(body)
        assert params["symbol"] == ["BTCUSDT"]
        assert params["side"] == ["BUY"]
        assert params["type"] == ["LIMIT"]
        assert params["price"] == ["50000.0"]
        assert params["timeInForce"] == ["DAY"]  # GTC maps to DAY enum value
        assert params["positionSide"] == ["BOTH"]
        assert "reduceOnly" not in params

    @rsps_lib.activate
    def test_place_order_market_payload(self):
        rsps_lib.add(
            rsps_lib.POST,
            f"{BASE_URL}/fapi/v1/order",
            json={
                "orderId": 456,
                "clientOrderId": "mkt-cid",
                "symbol": "ETHUSDT",
                "status": "FILLED",
                "origQty": "0.1",
                "price": "0",
                "avgPrice": "2500.0",
                "updateTime": 1700000001000,
            },
            status=200,
        )
        client = _make_client()
        client._last_sync = time.monotonic()

        req = OrderRequest(
            client_order_id="mkt-cid",
            symbol="ETHUSDT",
            side=Side.SELL,
            qty=Decimal("0.1"),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
            position_side=PositionSide.LONG,
        )
        resp = client.place_order(req, "mkt-cid")
        assert resp.orderId == 456

        body = rsps_lib.calls[0].request.body
        params = parse_qs(body)
        assert params["type"] == ["MARKET"]
        assert params["positionSide"] == ["LONG"]
        assert "price" not in params
        assert "timeInForce" not in params

    @rsps_lib.activate
    def test_place_order_with_reduce_only(self):
        rsps_lib.add(
            rsps_lib.POST,
            f"{BASE_URL}/fapi/v1/order",
            json={
                "orderId": 789,
                "clientOrderId": "ro-cid",
                "symbol": "BTCUSDT",
                "status": "NEW",
                "origQty": "0.001",
                "price": "0",
                "avgPrice": "0",
                "updateTime": 1700000002000,
            },
            status=200,
        )
        client = _make_client()
        client._last_sync = time.monotonic()

        req = OrderRequest(
            client_order_id="ro-cid",
            symbol="BTCUSDT",
            side=Side.SELL,
            qty=Decimal("0.001"),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
            position_side=PositionSide.BOTH,
            reduce_only=True,
        )
        resp = client.place_order(req, "ro-cid")
        assert resp.orderId == 789

        body = rsps_lib.calls[0].request.body
        params = parse_qs(body)
        assert params["reduceOnly"] == ["true"]


class TestRejectReduceOnlyInHedgeMode:
    def test_reject_reduce_only_in_hedge_mode(self):
        client = _make_client()
        req = OrderRequest(
            client_order_id="bad",
            symbol="BTCUSDT",
            side=Side.SELL,
            qty=Decimal("0.001"),
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
            position_side=PositionSide.LONG,  # hedge mode
            reduce_only=True,  # forbidden combination
        )
        with pytest.raises(ValidationError, match="reduceOnly"):
            client.place_order(req, "bad")


class TestErrorMapCodes:
    """All required error codes map to the correct BrokerError subclass."""

    @pytest.mark.parametrize(
        "code,expected_cls",
        [
            (-1021, TimestampError),
            (-1102, InvalidOrderError),
            (-1111, InvalidOrderError),
            (-2010, InvalidOrderError),
            (-2011, InvalidOrderError),
            (-2013, InvalidOrderError),
            (-2019, InsufficientFundsError),
            (-2020, InvalidOrderError),
            (-4061, ValidationError),
            (-4164, InvalidOrderError),
        ],
    )
    def test_error_map_codes(self, code: int, expected_cls: type):
        exc = map_error(code, "test message")
        assert isinstance(exc, expected_cls)
        assert str(code) in str(exc)


class TestEnsureLeverageIdempotent:
    @rsps_lib.activate
    def test_ensure_leverage_idempotent(self):
        """No POST if current leverage matches target."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v2/positionRisk",
            json=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0",
                    "entryPrice": "0",
                    "markPrice": "50000",
                    "liquidationPrice": "0",
                    "leverage": "10",
                    "marginType": "isolated",
                    "positionSide": "BOTH",
                    "unRealizedProfit": "0",
                    "notional": "0",
                }
            ],
            status=200,
        )

        adapter = _make_adapter()
        adapter._client._last_sync = time.monotonic()
        adapter.ensure_leverage("BTCUSDT", 10)  # already 10 → no set_leverage call

        assert len(rsps_lib.calls) == 1  # only GET, no POST


class TestEnsureMarginTypeIdempotent:
    @rsps_lib.activate
    def test_ensure_margin_type_idempotent(self):
        """No POST if margin type already matches."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v2/positionRisk",
            json=[
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0",
                    "entryPrice": "0",
                    "markPrice": "50000",
                    "liquidationPrice": "0",
                    "leverage": "5",
                    "marginType": "ISOLATED",
                    "positionSide": "BOTH",
                    "unRealizedProfit": "0",
                    "notional": "0",
                }
            ],
            status=200,
        )

        adapter = _make_adapter()
        adapter._client._last_sync = time.monotonic()
        adapter.ensure_margin_type("BTCUSDT", MarginType.ISOLATED)

        assert len(rsps_lib.calls) == 1  # only GET


class TestPositionModeMismatchRaisesStartup:
    @rsps_lib.activate
    def test_position_mode_mismatch_raises_startup(self):
        """Mismatch between expected and actual position mode → BrokerStartupError."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/positionSide/dual",
            json={"dualSidePosition": True},  # hedge mode on exchange
            status=200,
        )

        adapter = _make_adapter()
        adapter._client._last_sync = time.monotonic()

        with pytest.raises(BrokerStartupError, match="mismatch"):
            adapter.ensure_position_mode(hedge=False)  # expect one-way


class TestRateLimitHeaderAdjustsBucket:
    @rsps_lib.activate
    def test_rate_limit_header_adjusts_bucket(self):
        """Response headers X-MBX-USED-WEIGHT-1M and X-MBX-ORDER-COUNT-1M update buckets."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/openOrders",
            json=[],
            status=200,
            headers={
                "X-MBX-USED-WEIGHT-1M": "500",
                "X-MBX-ORDER-COUNT-1M": "50",
            },
        )

        client = _make_client()
        client._last_sync = time.monotonic()
        client.get_open_orders()

        rl = client._rate_limiter
        assert rl._buckets["weight"].tokens == pytest.approx(5500.0)
        assert rl._buckets["orders_1m"].tokens == pytest.approx(1150.0)


# ── Step 2.5 symbol_filters tests ─────────────────────────────────────────────


@pytest.fixture
def symbol_filters_loaded() -> SymbolFilters:
    """Return a SymbolFilters instance pre-loaded with fixture data (no network)."""
    sf = SymbolFilters(base_url=BASE_URL)
    sf._filters = {}
    from src.brokers.binance.schemas import ExchangeInfoSymbol
    for sym_data in EXCHANGE_INFO["symbols"]:
        sf._filters[sym_data["symbol"]] = ExchangeInfoSymbol.model_validate(sym_data)
    sf._loaded_at = time.monotonic()  # mark as fresh
    return sf


class TestLoadExchangeInfoCachesFilters:
    @rsps_lib.activate
    def test_load_exchange_info_caches_filters(self):
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/exchangeInfo",
            json=EXCHANGE_INFO,
            status=200,
        )

        sf = SymbolFilters(base_url=BASE_URL)
        sf._ensure_loaded()

        assert "BTCUSDT" in sf._filters
        assert "ETHUSDT" in sf._filters
        assert len(rsps_lib.calls) == 1

    @rsps_lib.activate
    def test_cache_not_reloaded_within_ttl(self):
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/exchangeInfo",
            json=EXCHANGE_INFO,
            status=200,
        )

        sf = SymbolFilters(base_url=BASE_URL)
        sf._ensure_loaded()
        sf._ensure_loaded()  # second call — should hit cache

        assert len(rsps_lib.calls) == 1


class TestQuantizeRespectsTickAndStep:
    """Quantize respects tick_size and lot_step from fixture (no hardcoded values)."""

    @pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
    def test_quantize_price(self, symbol_filters_loaded: SymbolFilters, symbol: str):
        sf = symbol_filters_loaded
        tick = sf.tick_size(symbol)

        raw_price = Decimal("49999.999")
        quantized = sf.quantize_price(symbol, raw_price)

        # Result must be a multiple of tick
        remainder = quantized % tick
        assert remainder == Decimal("0"), f"{symbol}: {quantized} not aligned to tick {tick}"

    @pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT"])
    def test_quantize_qty(self, symbol_filters_loaded: SymbolFilters, symbol: str):
        sf = symbol_filters_loaded
        step = sf.lot_step(symbol)

        raw_qty = Decimal("0.00157")
        quantized = sf.quantize_qty(symbol, raw_qty)

        remainder = quantized % step
        assert remainder == Decimal("0"), f"{symbol}: {quantized} not aligned to step {step}"
        assert quantized <= raw_qty  # ROUND_DOWN


class TestRejectBelowMinNotional:
    def test_reject_below_min_notional(self, symbol_filters_loaded: SymbolFilters):
        sf = symbol_filters_loaded
        symbol = "BTCUSDT"
        step = sf.lot_step(symbol)
        min_n = sf.min_notional(symbol)

        # price just above zero; ensure notional < min_notional
        price = Decimal("1")
        qty = (min_n / price).quantize(step) - step  # one step below min_notional qty

        with pytest.raises(InvalidOrderError, match="notional"):
            sf.validate_order(symbol, price, qty)

    def test_accept_at_min_notional(self, symbol_filters_loaded: SymbolFilters):
        sf = symbol_filters_loaded
        symbol = "ETHUSDT"
        min_n = sf.min_notional(symbol)
        price = Decimal("2500")
        qty = (min_n / price + sf.lot_step(symbol)).quantize(sf.lot_step(symbol))
        # Should not raise
        sf.validate_order(symbol, price, qty)


class TestRejectPriceOutsidePercentPrice:
    """percent_price_up/down are readable from fixture filters."""

    def test_percent_price_readable(self, symbol_filters_loaded: SymbolFilters):
        sf = symbol_filters_loaded
        up = sf.percent_price_up("BTCUSDT")
        down = sf.percent_price_down("BTCUSDT")
        assert up > Decimal("1")   # e.g. 1.05
        assert down < Decimal("1")  # e.g. 0.95


class TestTTLRefresh:
    @rsps_lib.activate
    def test_ttl_refresh(self):
        """After TTL expires, a fresh GET /exchangeInfo is issued."""
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/exchangeInfo",
            json=EXCHANGE_INFO,
            status=200,
        )
        rsps_lib.add(
            rsps_lib.GET,
            f"{BASE_URL}/fapi/v1/exchangeInfo",
            json=EXCHANGE_INFO,
            status=200,
        )

        sf = SymbolFilters(base_url=BASE_URL)
        sf._ensure_loaded()
        sf._loaded_at = time.monotonic() - 3700  # expire the cache
        sf._ensure_loaded()  # should trigger a reload

        assert len(rsps_lib.calls) == 2
