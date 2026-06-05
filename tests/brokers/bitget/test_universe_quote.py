"""Unit tests for src.brokers.bitget.universe_quote.

REST sync fetcher used by SnapshotBuilder. Mocked httpx to avoid network.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from src.brokers.bitget.universe_quote import (
    _bars_to_df,
    _interval_to_granularity,
    fetch_universe_klines,
)


def test_interval_to_granularity_uppercase_for_hour_day():
    assert _interval_to_granularity("1h") == "1H"
    assert _interval_to_granularity("1d") == "1D"
    assert _interval_to_granularity("5m") == "5m"


def test_interval_to_granularity_raises_for_unknown():
    with pytest.raises(ValueError, match=r"unsupported interval"):
        _interval_to_granularity("7m")


def test_bars_to_df_empty():
    df = _bars_to_df([])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.empty


def test_bars_to_df_sorts_index_and_casts_floats():
    rows = [
        ["1700003600000", "50100", "50200", "50000", "50150", "10", "501500"],
        ["1700000000000", "50000", "50500", "49800", "50200", "20", "1004000"],
    ]
    df = _bars_to_df(rows)
    assert len(df) == 2
    # Index ascending after sort
    assert df.index[0] < df.index[1]
    assert df["open"].dtype.kind == "f"
    assert df["close"].iloc[0] == 50200.0


def _mock_resp(code: str, data: list | None = None, status: int = 200):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = {"code": code, "msg": "ok", "data": data or []}
    return r


def test_fetch_universe_klines_skips_non_200():
    with patch("src.brokers.bitget.universe_quote.httpx.Client") as cli_cls:
        ctx = cli_cls.return_value.__enter__.return_value
        ctx.get.return_value = _mock_resp("00000", [], status=500)
        out = fetch_universe_klines(["BTCUSDT"], interval="1h")
        assert out == {}


def test_fetch_universe_klines_skips_bitget_error_code():
    with patch("src.brokers.bitget.universe_quote.httpx.Client") as cli_cls:
        ctx = cli_cls.return_value.__enter__.return_value
        ctx.get.return_value = _mock_resp("40001", [])
        assert fetch_universe_klines(["BTCUSDT"], interval="1h") == {}


def test_fetch_universe_klines_returns_per_symbol_dataframe():
    rows = [
        ["1700000000000", "50000", "50500", "49800", "50200", "100", "5000000"],
    ]
    with patch("src.brokers.bitget.universe_quote.httpx.Client") as cli_cls:
        ctx = cli_cls.return_value.__enter__.return_value
        ctx.get.return_value = _mock_resp("00000", rows)
        out = fetch_universe_klines(["BTCUSDT", "ETHUSDT"], interval="1h")
    assert set(out.keys()) == {"BTCUSDT", "ETHUSDT"}
    assert isinstance(out["BTCUSDT"], pd.DataFrame)
    assert out["BTCUSDT"]["close"].iloc[0] == 50200.0


def test_fetch_universe_klines_empty_input_no_http_call():
    with patch("src.brokers.bitget.universe_quote.httpx.Client") as cli_cls:
        out = fetch_universe_klines([], interval="1h")
    assert out == {}
    cli_cls.assert_not_called()
