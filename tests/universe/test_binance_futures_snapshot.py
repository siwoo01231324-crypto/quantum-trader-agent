"""Unit tests for binance_futures_snapshot — pure mapping + thin httpx fetcher."""
from __future__ import annotations

import httpx
import pandas as pd
import pytest
import respx

from universe.binance_futures_snapshot import (
    REST_BASE_LIVE,
    fetch_futures_24h_snapshot,
    map_ticker24h_to_snapshot,
)
from universe.binance_top import top_n_by_volume


def test_map_basic_payload():
    raw = [
        {
            "symbol": "BTCUSDT", "lastPrice": "55000.5",
            "priceChangePercent": "1.23", "quoteVolume": "9.8e10",
        },
        {
            "symbol": "ETHUSDT", "lastPrice": "3500.0",
            "priceChangePercent": "-0.50", "quoteVolume": "3.5e10",
        },
    ]
    df = map_ticker24h_to_snapshot(raw)
    assert list(df.columns) == ["symbol", "last_price", "change_24h_pct", "quote_volume_24h"]
    assert df.loc[0, "symbol"] == "BTCUSDT"
    assert df.loc[0, "last_price"] == pytest.approx(55000.5)
    assert df.loc[0, "change_24h_pct"] == pytest.approx(1.23)
    assert df.loc[0, "quote_volume_24h"] == pytest.approx(9.8e10)
    assert len(df) == 2


def test_map_skips_malformed_entries():
    raw = [
        {"symbol": "BTCUSDT", "lastPrice": "55000", "priceChangePercent": "1.0", "quoteVolume": "1e10"},
        {"symbol": "BAD1", "lastPrice": "not-a-number", "priceChangePercent": "1.0", "quoteVolume": "1e10"},
        {"symbol": "BAD2"},  # missing fields
        {"symbol": "ETHUSDT", "lastPrice": "3500", "priceChangePercent": "0.0", "quoteVolume": "5e9"},
    ]
    df = map_ticker24h_to_snapshot(raw)
    assert df["symbol"].tolist() == ["BTCUSDT", "ETHUSDT"]


def test_map_empty_input_returns_empty_df_with_schema():
    df = map_ticker24h_to_snapshot([])
    assert df.empty
    assert list(df.columns) == ["symbol", "last_price", "change_24h_pct", "quote_volume_24h"]


def test_map_output_composable_with_top_n_by_volume():
    """Schema check: fetcher output must drop straight into top_n_by_volume."""
    raw = [
        {"symbol": "BTCUSDT", "lastPrice": "55000", "priceChangePercent": "1.0", "quoteVolume": "9e10"},
        {"symbol": "ETHUSDT", "lastPrice": "3500", "priceChangePercent": "0.0", "quoteVolume": "5e10"},
        # Stablecoin (should be filtered by top_n_by_volume default exclusions)
        {"symbol": "USDCUSDT", "lastPrice": "1.0001", "priceChangePercent": "0.01", "quoteVolume": "1e11"},
    ]
    df = map_ticker24h_to_snapshot(raw)
    top = top_n_by_volume(df, n=10)
    assert "BTCUSDT" in top
    assert "ETHUSDT" in top
    assert "USDCUSDT" not in top  # USDC excluded


@pytest.mark.asyncio
@respx.mock
async def test_fetch_uses_correct_endpoint_and_parses():
    route = respx.get(f"{REST_BASE_LIVE}/fapi/v1/ticker/24hr").mock(
        return_value=httpx.Response(
            200,
            json=[{
                "symbol": "BTCUSDT", "lastPrice": "55000",
                "priceChangePercent": "1.0", "quoteVolume": "9e10",
            }],
        )
    )
    df = await fetch_futures_24h_snapshot()
    assert route.called
    assert df["symbol"].tolist() == ["BTCUSDT"]


@pytest.mark.asyncio
@respx.mock
async def test_fetch_propagates_http_error():
    respx.get(f"{REST_BASE_LIVE}/fapi/v1/ticker/24hr").mock(
        return_value=httpx.Response(500, text="server error"),
    )
    with pytest.raises(httpx.HTTPStatusError):
        await fetch_futures_24h_snapshot()
