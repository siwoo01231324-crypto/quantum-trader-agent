from __future__ import annotations

import re

import pytest

from src.brokers.client_id import generate, BINANCE_CLIENT_ID_PATTERN
from src.brokers.errors import ValidationError

PATTERN = re.compile(BINANCE_CLIENT_ID_PATTERN)


def test_generated_id_matches_regex():
    cid = generate("momo", "BTCUSDT", "BUY", ts_ms=1713600000000)
    assert PATTERN.match(cid), f"ID '{cid}' does not match pattern"


def test_generated_id_max_length():
    cid = generate("momo", "BTCUSDT", "BUY", ts_ms=1713600000000)
    assert len(cid) <= 36


def test_generated_id_at_least_one_char():
    cid = generate("momo", "BTCUSDT", "BUY", ts_ms=1713600000000)
    assert len(cid) >= 1


def test_same_inputs_produce_same_id():
    cid1 = generate("strategy_a", "ETHUSDT", "SELL", ts_ms=9999999999)
    cid2 = generate("strategy_a", "ETHUSDT", "SELL", ts_ms=9999999999)
    assert cid1 == cid2


def test_different_inputs_produce_different_ids():
    cid1 = generate("strategy_a", "BTCUSDT", "BUY", ts_ms=1000)
    cid2 = generate("strategy_a", "BTCUSDT", "BUY", ts_ms=2000)
    assert cid1 != cid2


def test_length_boundary_36():
    cid = generate("a" * 30, "BTCUSDT", "BUY", ts_ms=1713600000000)
    assert len(cid) <= 36


def test_valid_pattern_chars_only():
    cid = generate("myStrat", "BTCUSDT", "BUY", ts_ms=1713600000000)
    for ch in cid:
        assert ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-:/", \
            f"Invalid char '{ch}' in ID '{cid}'"


def test_pattern_rejects_invalid_chars():
    # space is not allowed
    assert not PATTERN.match("hello world")
    # empty string not allowed
    assert not PATTERN.match("")
    # too long
    assert not PATTERN.match("a" * 37)


def test_pattern_accepts_all_valid_chars():
    valid = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-:/"
    assert PATTERN.match(valid[:36])


def test_strategy_symbol_side_all_vary():
    ids = {
        generate("s1", "BTCUSDT", "BUY", ts_ms=1000),
        generate("s2", "BTCUSDT", "BUY", ts_ms=1000),
        generate("s1", "ETHUSDT", "BUY", ts_ms=1000),
        generate("s1", "BTCUSDT", "SELL", ts_ms=1000),
    }
    assert len(ids) == 4
