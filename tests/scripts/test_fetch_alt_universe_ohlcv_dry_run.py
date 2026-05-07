"""Tests for scripts/fetch_alt_universe_ohlcv.py dry-run mode.

No real network calls. Validates argument parsing and dry-run output.
"""
from __future__ import annotations

import re
import sys
from io import StringIO
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.fetch_alt_universe_ohlcv import _parse_args, _estimate_plan, main


# ---------------------------------------------------------------------------
# Argument parsing tests
# ---------------------------------------------------------------------------


def test_parse_defaults():
    args = _parse_args([])
    assert args.freq == "5m"
    assert args.start == "2020-01-01"
    assert args.end == "2025-12-31"
    assert args.dry_run is False
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    assert len(symbols) == 10


def test_parse_custom_symbols():
    args = _parse_args(["--symbols", "BTCUSDT,ETHUSDT", "--freq", "5m"])
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    assert symbols == ["BTCUSDT", "ETHUSDT"]
    assert args.freq == "5m"


def test_parse_dry_run_flag():
    args = _parse_args(["--dry-run"])
    assert args.dry_run is True


def test_parse_out_path(tmp_path):
    args = _parse_args(["--out", str(tmp_path)])
    assert args.out == tmp_path


def test_parse_date_range():
    args = _parse_args(["--start", "2021-06-01", "--end", "2024-12-31"])
    assert args.start == "2021-06-01"
    assert args.end == "2024-12-31"


# ---------------------------------------------------------------------------
# Dry-run output tests
# ---------------------------------------------------------------------------


def test_dry_run_prints_plan(tmp_path, capsys):
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    _estimate_plan(symbols, "2020-01-01", "2025-12-31", tmp_path / "ohlcv" / "freq=5m")
    captured = capsys.readouterr()
    out = captured.out
    assert "DRY-RUN" in out
    assert "BTCUSDT" in out
    assert "Est time" in out
    assert "Est disk" in out


def test_dry_run_shows_partition_paths(tmp_path, capsys):
    symbols = ["BTCUSDT", "ETHUSDT"]
    _estimate_plan(symbols, "2020-01-01", "2022-12-31", tmp_path / "ohlcv" / "freq=5m")
    captured = capsys.readouterr()
    assert "year=2020" in captured.out
    assert "symbol=BTCUSDT" in captured.out


def test_main_dry_run_exits_zero(tmp_path, capsys):
    rc = main([
        "--symbols", "BTCUSDT,ETHUSDT",
        "--dry-run",
        "--out", str(tmp_path),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    assert "DRY-RUN" in captured.out


def test_main_dry_run_contains_symbol_count(tmp_path, capsys):
    rc = main([
        "--symbols", "BTCUSDT,ETHUSDT,SOLUSDT",
        "--start", "2020-01-01",
        "--end", "2025-12-31",
        "--dry-run",
        "--out", str(tmp_path),
    ])
    assert rc == 0
    captured = capsys.readouterr()
    # 3 symbols should appear in output
    assert "SOLUSDT" in captured.out


def test_main_empty_symbols_returns_error(tmp_path, capsys):
    rc = main(["--symbols", "", "--dry-run", "--out", str(tmp_path)])
    assert rc == 1
