"""Tests for ``scripts/fetch_futures_candles.py`` (#106).

Validates the multi-symbol Futures CLI:
- argparse contract (``--symbols`` comma-separated, default interval=1m)
- per-symbol fetch + save fan-out
- BTCUSDT/ETHUSDT/SOLUSDT default symbol set
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "fetch_futures_candles.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("fetch_futures_candles_mod", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def test_cli_default_symbols_match_ac():
    """AC requires BTCUSDT/ETHUSDT/SOLUSDT — default symbol set must match."""
    mod = _load_module()
    parser = mod.build_parser()
    args = parser.parse_args([])
    assert args.symbols == "BTCUSDT,ETHUSDT,SOLUSDT"
    assert args.interval == "1m"


def test_cli_custom_symbols_parsed():
    mod = _load_module()
    parser = mod.build_parser()
    args = parser.parse_args([
        "--symbols", "BTCUSDT,ETHUSDT",
        "--interval", "15m",
        "--start", "2026-04-01",
        "--end", "2026-04-26",
        "--output-dir", "/tmp/lake_futures",
    ])
    assert args.symbols == "BTCUSDT,ETHUSDT"
    assert args.interval == "15m"
    assert args.start == "2026-04-01"
    assert args.end == "2026-04-26"
    assert args.output_dir == "/tmp/lake_futures"


# ---------------------------------------------------------------------------
# fan-out
# ---------------------------------------------------------------------------

def test_main_fans_out_per_symbol(tmp_path, monkeypatch):
    """``main`` must call fetch + save once per symbol."""
    mod = _load_module()

    fetch_calls: list[dict] = []
    save_calls: list[dict] = []

    def _fake_fetch(symbol, interval, start, end):
        fetch_calls.append({
            "symbol": symbol, "interval": interval, "start": start, "end": end,
        })
        return pd.DataFrame([{
            "symbol": symbol, "ts": pd.Timestamp("2026-04-01", tz="UTC"),
            "freq": interval, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0,
            "volume": 1.0, "vwap": 1.0, "trade_count": 1,
            "source": "binance_futures", "ingested_at": pd.Timestamp.utcnow(),
        }])

    def _fake_save(df, output_dir, *, symbol, freq):
        save_calls.append({"symbol": symbol, "freq": freq, "rows": len(df)})
        return [Path(output_dir) / f"{symbol}.parquet"]

    monkeypatch.setattr(mod, "fetch_binance_futures_klines", _fake_fetch)
    monkeypatch.setattr(mod, "save_ohlcv_parquet", _fake_save)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)

    mod.main([
        "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT",
        "--interval", "1m",
        "--start", "2026-04-01",
        "--end", "2026-04-02",
        "--output-dir", str(tmp_path),
    ])

    assert [c["symbol"] for c in fetch_calls] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert [c["interval"] for c in fetch_calls] == ["1m"] * 3
    assert [c["symbol"] for c in save_calls] == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    assert all(c["freq"] == "1m" for c in save_calls)


def test_main_skips_save_for_empty_response(tmp_path, monkeypatch):
    """Empty fetch result → save skipped (avoid writing empty parquet)."""
    mod = _load_module()

    save_calls: list[dict] = []

    def _empty_fetch(symbol, interval, start, end):
        return pd.DataFrame()

    def _record_save(df, output_dir, *, symbol, freq):
        save_calls.append({"symbol": symbol})
        return []

    monkeypatch.setattr(mod, "fetch_binance_futures_klines", _empty_fetch)
    monkeypatch.setattr(mod, "save_ohlcv_parquet", _record_save)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)

    mod.main([
        "--symbols", "BTCUSDT",
        "--interval", "1m",
        "--start", "2026-04-01",
        "--end", "2026-04-02",
        "--output-dir", str(tmp_path),
    ])

    assert save_calls == []
