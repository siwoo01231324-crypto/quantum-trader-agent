from __future__ import annotations

import logging

import pytest

from src.brokers.logging_filter import SecretMaskingFilter, install_global

PATTERNS = [
    ("api_key", "my_api_key_12345"),
    ("secret", "supersecretvalue"),
    ("signature", "abc123signature"),
    ("authorization", "Bearer tokenXYZ"),
    ("appkey", "myappkey"),
    ("appsecret", "myappsecret"),
    ("hashkey", "myhashkey"),
    ("cano", "12345678"),
    ("approval_key", "myapprovalkey"),
    ("access_token", "myaccesstoken"),
]


def _make_logger(name: str) -> tuple[logging.Logger, list[logging.LogRecord]]:
    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
    records: list[logging.LogRecord] = []

    class CapHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = CapHandler()
    handler.addFilter(SecretMaskingFilter())
    logger.handlers = [handler]
    logger.propagate = False
    return logger, records


@pytest.mark.parametrize("key,value", PATTERNS)
def test_masks_key_value_pattern(key: str, value: str):
    logger, records = _make_logger(f"test.mask.{key}")
    logger.info(f'Sending request with {key}="{value}" to server')
    assert records, "No log record captured"
    msg = records[0].getMessage()
    assert value not in msg, f"Secret '{value}' not masked in: {msg}"
    assert "***" in msg or "REDACTED" in msg or "[MASKED]" in msg


def test_masks_bearer_token():
    logger, records = _make_logger("test.mask.bearer")
    logger.info("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    msg = records[0].getMessage()
    assert "eyJhbGciOiJIUzI1NiJ9" not in msg


def test_masks_url_query_signature():
    logger, records = _make_logger("test.mask.url")
    logger.info("GET https://api.binance.com/fapi/v1/order?symbol=BTCUSDT&signature=abc123def456")
    msg = records[0].getMessage()
    assert "abc123def456" not in msg


def test_masks_multiple_secrets_in_one_message():
    logger, records = _make_logger("test.mask.multi")
    logger.info("api_key=KEY123 secret=SEC456 signature=SIG789")
    msg = records[0].getMessage()
    assert "KEY123" not in msg
    assert "SEC456" not in msg
    assert "SIG789" not in msg


def test_non_secret_content_preserved():
    logger, records = _make_logger("test.mask.preserve")
    logger.info("Order placed: symbol=BTCUSDT qty=0.001 status=NEW")
    msg = records[0].getMessage()
    assert "BTCUSDT" in msg
    assert "0.001" in msg
    assert "NEW" in msg


def test_install_global_attaches_to_root():
    install_global()
    root = logging.getLogger()
    filter_types = [type(f).__name__ for f in root.filters]
    assert "SecretMaskingFilter" in filter_types


def test_access_token_masked():
    logger, records = _make_logger("test.mask.token")
    logger.info("access_token=eyTokenValue123 used in header")
    msg = records[0].getMessage()
    assert "eyTokenValue123" not in msg
