"""Unit tests for src.brokers.bitget.error_map.

Bitget v2 returns code as *string* — verify map lookups work with both
ergonomic inputs and the actual wire format.
"""
from __future__ import annotations

import pytest

from src.brokers.bitget.error_map import map_error
from src.brokers.errors import (
    AuthError,
    InsufficientFundsError,
    InvalidOrderError,
    RateLimitError,
    TimestampError,
    UnknownError,
    ValidationError,
)


@pytest.mark.parametrize("code,expected", [
    ("40001", AuthError),
    ("40003", AuthError),
    ("40010", TimestampError),
    ("40013", AuthError),
    ("40034", ValidationError),
    ("40762", InvalidOrderError),  # max position notional / qty upper limit
    ("40774", InvalidOrderError),  # position mode mismatch
    ("41114", RateLimitError),
    ("43012", InsufficientFundsError),
    ("99999", UnknownError),       # unmapped → fallthrough
    ("429", RateLimitError),       # HTTP-level rate limit alias
])
def test_map_error_code_classification(code: str, expected: type):
    err = map_error(code, f"test msg for {code}")
    assert isinstance(err, expected)
    # Diagnostic message preserves code for grep-ability.
    assert f"[{code}]" in str(err)


def test_map_error_preserves_message():
    err = map_error("40774", "The order type for unilateral position must be unilateral")
    assert "unilateral" in str(err)


def test_map_error_accepts_int_code_via_str_coercion():
    # Caller passes string per docs; if someone forgets, str() coercion handles it.
    err = map_error(str(40001), "key invalid")
    assert isinstance(err, AuthError)
