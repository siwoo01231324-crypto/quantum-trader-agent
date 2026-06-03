from __future__ import annotations

from src.brokers.errors import (
    BrokerError,
    InsufficientFundsError,
    InvalidOrderError,
    TimestampError,
    UnknownError,
    ValidationError,
)

# Binance USDS-M Futures error code → BrokerError subclass
_MAP: dict[int, type[BrokerError]] = {
    -1021: TimestampError,       # INVALID_TIMESTAMP — clock drift
    -1102: InvalidOrderError,    # MANDATORY_PARAM_EMPTY_OR_MALFORMED
    -1111: InvalidOrderError,    # BAD_PRECISION
    -2010: InvalidOrderError,    # NEW_ORDER_REJECTED
    -2011: InvalidOrderError,    # CANCEL_REJECTED
    -2013: InvalidOrderError,    # NO_SUCH_ORDER
    -2019: InsufficientFundsError,  # MARGIN_NOT_SUFFICIENT
    -2020: InvalidOrderError,    # UNABLE_TO_FILL (FOK)
    -2027: InvalidOrderError,    # MAX_NOTIONAL_EXCEEDED — symbol position cap hit
    -4061: ValidationError,      # POSITION_SIDE_NOT_MATCH
    -4164: InvalidOrderError,    # MIN_NOTIONAL
}


def map_error(code: int, msg: str) -> BrokerError:
    """Convert a Binance error code + message to a BrokerError subclass."""
    cls = _MAP.get(code, UnknownError)
    return cls(f"[{code}] {msg}")
