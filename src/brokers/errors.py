from __future__ import annotations


class BrokerError(Exception):
    """Base class for all broker errors."""


class AuthError(BrokerError):
    """Authentication/authorization failure."""


class RateLimitError(BrokerError):
    """Rate limit exceeded."""


class NetworkError(BrokerError):
    """Network-level failure (connection refused, timeout, etc.)."""


class InsufficientFundsError(BrokerError):
    """Insufficient margin or funds to place the order."""


class InvalidOrderError(BrokerError):
    """Order rejected by the exchange (bad params, below min notional, etc.)."""


class ValidationError(BrokerError):
    """Local validation failure before sending to exchange."""


class ConfigurationError(BrokerError):
    """Missing or malformed configuration / environment variable."""


class UnsupportedOperationError(BrokerError):
    """Operation not supported by this broker (e.g. hedge mode on KIS)."""


class BrokerStartupError(BrokerError):
    """Fatal error during broker initialization (e.g. position mode mismatch)."""


class TimestampError(BrokerError):
    """Request timestamp outside recvWindow (clock drift). Auto-retried once."""


class UnknownError(BrokerError):
    """Unexpected response or unknown error code from the exchange."""
