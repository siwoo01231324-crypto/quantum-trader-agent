from __future__ import annotations

import importlib
import os
import sys

import pytest

from src.brokers.errors import ConfigurationError


def _load_with_env(env: dict[str, str]):
    """Helper: set env vars, reload config module, restore env and module state."""
    old_env = {}
    for k, v in env.items():
        old_env[k] = os.environ.get(k)
        os.environ[k] = v

    # Also clear any keys present in old env but not in new env
    all_keys = {
        "HANTOO_FAKE_API_KEY", "HANTOO_FAKE_SECRET_API_KEY", "HANTOO_CREDIT_NUMBER",
        "BINANCE_DEMO_API_KEY", "BINANCE_DEMO_SECRET_API_KEY",
        "BINANCE_BASE_URL", "BINANCE_WS_URL", "ACTIVE_BROKER",
    }
    cleared = {}
    for k in all_keys - set(env.keys()):
        val = os.environ.pop(k, None)
        if val is not None:
            cleared[k] = val

    try:
        import src.brokers.config as cfg_mod
        importlib.reload(cfg_mod)
        return cfg_mod.load_broker_config()
    finally:
        for k, orig in old_env.items():
            if orig is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = orig
        for k, v in cleared.items():
            os.environ[k] = v
        # Reload module to restore clean state
        import src.brokers.config as cfg_mod
        importlib.reload(cfg_mod)


VALID_ENV = {
    "HANTOO_FAKE_API_KEY": "fake_key",
    "HANTOO_FAKE_SECRET_API_KEY": "fake_secret",
    "HANTOO_CREDIT_NUMBER": "12345678-01",
    "BINANCE_DEMO_API_KEY": "binance_key",
    "BINANCE_DEMO_SECRET_API_KEY": "binance_secret",
    "BINANCE_BASE_URL": "https://testnet.binancefuture.com",
    "BINANCE_WS_URL": "wss://fstream.binancefuture.com/ws",
    "ACTIVE_BROKER": "kis",
}


def test_valid_config_loads():
    cfg = _load_with_env(VALID_ENV)
    assert cfg is not None


def test_hantoo_credit_number_parsed():
    cfg = _load_with_env(VALID_ENV)
    assert cfg.hantoo_cano == "12345678"
    assert cfg.hantoo_acnt_prdt_cd == "01"


def test_hantoo_credit_number_regex_valid_formats():
    for valid in ["12345678-01", "00000000-99", "99999999-00"]:
        env = {**VALID_ENV, "HANTOO_CREDIT_NUMBER": valid}
        cfg = _load_with_env(env)
        assert cfg.hantoo_cano == valid[:8]
        assert cfg.hantoo_acnt_prdt_cd == valid[-2:]


def test_hantoo_credit_number_missing_hyphen():
    env = {**VALID_ENV, "HANTOO_CREDIT_NUMBER": "1234567801"}
    with pytest.raises(ConfigurationError) as exc_info:
        _load_with_env(env)
    assert "HANTOO_CREDIT_NUMBER" in str(exc_info.value)


def test_hantoo_credit_number_wrong_length():
    env = {**VALID_ENV, "HANTOO_CREDIT_NUMBER": "1234-01"}
    with pytest.raises(ConfigurationError) as exc_info:
        _load_with_env(env)
    assert "HANTOO_CREDIT_NUMBER" in str(exc_info.value)


def test_hantoo_credit_number_non_digit():
    env = {**VALID_ENV, "HANTOO_CREDIT_NUMBER": "1234567A-01"}
    with pytest.raises(ConfigurationError) as exc_info:
        _load_with_env(env)
    assert "HANTOO_CREDIT_NUMBER" in str(exc_info.value)


def test_missing_hantoo_fake_api_key_raises():
    env = {k: v for k, v in VALID_ENV.items() if k != "HANTOO_FAKE_API_KEY"}
    # Ensure the key is not set
    old = os.environ.pop("HANTOO_FAKE_API_KEY", None)
    try:
        with pytest.raises(ConfigurationError) as exc_info:
            _load_with_env(env)
        assert "HANTOO_FAKE_API_KEY" in str(exc_info.value)
    finally:
        if old is not None:
            os.environ["HANTOO_FAKE_API_KEY"] = old


def test_missing_binance_key_raises():
    env = {k: v for k, v in VALID_ENV.items() if k != "BINANCE_DEMO_API_KEY"}
    old = os.environ.pop("BINANCE_DEMO_API_KEY", None)
    try:
        with pytest.raises(ConfigurationError) as exc_info:
            _load_with_env(env)
        assert "BINANCE_DEMO_API_KEY" in str(exc_info.value)
    finally:
        if old is not None:
            os.environ["BINANCE_DEMO_API_KEY"] = old


def test_error_message_includes_expected_format():
    env = {**VALID_ENV, "HANTOO_CREDIT_NUMBER": "bad"}
    with pytest.raises(ConfigurationError) as exc_info:
        _load_with_env(env)
    msg = str(exc_info.value)
    # Should mention expected format
    assert "^[0-9]{8}-[0-9]{2}$" in msg or "format" in msg.lower() or "regex" in msg.lower()
