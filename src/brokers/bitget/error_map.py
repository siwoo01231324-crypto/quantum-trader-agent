"""Bitget v2 USDT-M Futures error code → BrokerError subclass.

Bitget v2 returns ``code`` as a string ("00000" = success). Non-zero codes
are mapped to ``BrokerError`` subclasses for parity with the Binance adapter.

Reference: https://www.bitget.com/api-doc/contract/error-codes
"""
from __future__ import annotations

from src.brokers.errors import (
    AuthError,
    BrokerError,
    InsufficientFundsError,
    InvalidOrderError,
    RateLimitError,
    TimestampError,
    UnknownError,
    ValidationError,
)

# Bitget error code (str) → BrokerError subclass.
# Note: Bitget codes are *strings* (e.g. "40001") unlike Binance's negative
# ints. Caller stringifies before lookup.
_MAP: dict[str, type[BrokerError]] = {
    "40001": AuthError,                  # ACCESS_KEY does not exist
    "40002": AuthError,                  # signature verification failed
    "40003": AuthError,                  # passphrase incorrect
    "40005": AuthError,                  # invalid ACCESS-TIMESTAMP
    "40006": AuthError,                  # request timestamp expired
    "40008": AuthError,                  # API key locked
    "40009": ValidationError,            # parameter error (signature param)
    "40010": TimestampError,             # ACCESS-TIMESTAMP expired
    "40011": AuthError,                  # ip not allowed
    "40012": AuthError,                  # api key expired
    "40013": AuthError,                  # paptrading header required for demo key
    "40014": AuthError,                  # paptrading not supported for this key
    "40034": ValidationError,            # parameter error
    "40037": ValidationError,            # order does not exist (alt of 43025)
    "40109": InvalidOrderError,          # 거래 비용 부족 / order size invalid
    "40404": ValidationError,            # endpoint not found
    "40725": InvalidOrderError,          # service unavailable for symbol
    "40762": InvalidOrderError,          # order qty exceeds upper limit (= Binance -2027 equivalent)
    "40774": InvalidOrderError,          # order type / position mode mismatch (NOT max notional)
    "40786": InvalidOrderError,          # duplicate clientOid (24h dedup)
    "40808": InvalidOrderError,          # parameter verification exception
    "41114": RateLimitError,             # rate limit exceeded
    "42001": InvalidOrderError,          # cannot place order (general)
    "43001": InvalidOrderError,          # order does not exist
    "43012": InsufficientFundsError,     # insufficient balance
    "43025": InvalidOrderError,          # plan order not found
    "45110": InvalidOrderError,          # less than minimum amount 5 USDT
    "45117": InvalidOrderError,          # min trade amount
    "50067": InvalidOrderError,          # price beyond limit
    "429": RateLimitError,               # HTTP 429
}


def map_error(code: str, msg: str) -> BrokerError:
    """Convert a Bitget error ``code`` + ``msg`` to a BrokerError subclass.

    Unknown codes fall through to ``UnknownError`` — diagnostic message
    preserves both code and message for grep-ability.
    """
    cls = _MAP.get(str(code), UnknownError)
    return cls(f"[{code}] {msg}")
