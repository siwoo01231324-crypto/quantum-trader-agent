import pytest
from scripts.shadow_run import parse_args, _parse_duration, _build_config


def test_parse_duration_hours():
    assert _parse_duration("8h") == 8 * 3600


def test_parse_duration_minutes():
    assert _parse_duration("30m") == 30 * 60


def test_parse_duration_seconds():
    assert _parse_duration("15s") == 15


def test_parse_duration_zero():
    assert _parse_duration("0") == 0.0


def test_parse_args_required_symbols():
    args = parse_args(["--symbols", "BTCUSDT,ETHUSDT"])
    assert args.symbols == ["BTCUSDT", "ETHUSDT"]
    assert args.duration == "0"
    assert args.max_iterations is None


def test_parse_args_max_iterations():
    args = parse_args(["--symbols", "BTCUSDT", "--max-iterations", "100"])
    assert args.max_iterations == 100


def test_build_config():
    args = parse_args(["--symbols", "BTCUSDT", "--run-id", "test_run", "--log-dir", "logs/test"])
    config = _build_config(args)
    assert "BTCUSDT" in config.symbols
    assert "test_run" in str(config.wal_path)
    assert config.wal_path.name == "wal.jsonl"
