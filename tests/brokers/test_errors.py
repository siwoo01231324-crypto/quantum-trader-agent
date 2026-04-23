from __future__ import annotations

import pytest

from src.brokers.errors import (
    BrokerError,
    AuthError,
    RateLimitError,
    NetworkError,
    InsufficientFundsError,
    InvalidOrderError,
    ValidationError,
    ConfigurationError,
    UnsupportedOperationError,
    BrokerStartupError,
    UnknownError,
)


def test_all_errors_subclass_broker_error():
    subclasses = [
        AuthError, RateLimitError, NetworkError, InsufficientFundsError,
        InvalidOrderError, ValidationError, ConfigurationError,
        UnsupportedOperationError, BrokerStartupError, UnknownError,
    ]
    for cls in subclasses:
        assert issubclass(cls, BrokerError), f"{cls.__name__} must subclass BrokerError"


def test_broker_error_subclasses_exception():
    assert issubclass(BrokerError, Exception)


def test_auth_error_raise():
    with pytest.raises(BrokerError):
        raise AuthError("invalid key")


def test_rate_limit_error():
    with pytest.raises(RateLimitError):
        raise RateLimitError("too many requests")


def test_network_error():
    with pytest.raises(NetworkError):
        raise NetworkError("connection refused")


def test_insufficient_funds_error():
    with pytest.raises(InsufficientFundsError):
        raise InsufficientFundsError("not enough USDT")


def test_invalid_order_error():
    with pytest.raises(InvalidOrderError):
        raise InvalidOrderError("below min notional")


def test_validation_error():
    with pytest.raises(ValidationError):
        raise ValidationError("bad field")


def test_configuration_error():
    with pytest.raises(ConfigurationError):
        raise ConfigurationError("missing env var HANTOO_CREDIT_NUMBER")


def test_unsupported_operation_error():
    with pytest.raises(UnsupportedOperationError):
        raise UnsupportedOperationError("KIS does not support hedge mode")


def test_broker_startup_error():
    with pytest.raises(BrokerStartupError):
        raise BrokerStartupError("position mode mismatch")


def test_unknown_error():
    with pytest.raises(UnknownError):
        raise UnknownError("unexpected response")


def test_catch_by_base_class():
    errors = [
        AuthError("a"), RateLimitError("b"), NetworkError("c"),
        ConfigurationError("d"), BrokerStartupError("e"),
    ]
    for err in errors:
        try:
            raise err
        except BrokerError:
            pass
        else:
            pytest.fail(f"{type(err).__name__} not caught as BrokerError")
