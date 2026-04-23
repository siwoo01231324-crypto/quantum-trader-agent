from __future__ import annotations

import pytest

from src.brokers.errors import ConfigurationError
from src.brokers.kis.adapter import _parse_credit_number


class TestCreditNumberParsing:
    def test_valid_format(self):
        cano, acnt = _parse_credit_number("12345678-01")
        assert cano == "12345678"
        assert acnt == "01"

    def test_valid_format_different_suffix(self):
        cano, acnt = _parse_credit_number("87654321-99")
        assert cano == "87654321"
        assert acnt == "99"

    def test_missing_hyphen_raises(self):
        with pytest.raises(ConfigurationError, match="포맷 오류"):
            _parse_credit_number("1234567801")

    def test_wrong_account_length_raises(self):
        with pytest.raises(ConfigurationError, match="포맷 오류"):
            _parse_credit_number("12345678-1")

    def test_wrong_cano_length_raises(self):
        with pytest.raises(ConfigurationError, match="포맷 오류"):
            _parse_credit_number("1234567-01")

    def test_non_digit_cano_raises(self):
        with pytest.raises(ConfigurationError, match="포맷 오류"):
            _parse_credit_number("1234567A-01")

    def test_non_digit_suffix_raises(self):
        with pytest.raises(ConfigurationError, match="포맷 오류"):
            _parse_credit_number("12345678-0A")

    def test_empty_raises(self):
        with pytest.raises(ConfigurationError, match="포맷 오류"):
            _parse_credit_number("")
