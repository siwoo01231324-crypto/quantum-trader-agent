"""Unit tests for src/universe/kospi200.py (T2 Red phase)."""
from __future__ import annotations

import re

import pytest

from src.universe.kospi200 import KOSPI200_CONSTITUENTS, get_codes

CODE_RE = re.compile(r"^\d{6}$")


class TestKospi200Constituents:
    def test_length_in_valid_range(self):
        assert 180 <= len(KOSPI200_CONSTITUENTS) <= 210

    def test_all_codes_are_six_digits(self):
        codes = get_codes()
        for code in codes:
            assert CODE_RE.match(code), f"Bad code format: {code!r}"

    def test_no_duplicate_codes(self):
        codes = get_codes()
        assert len(codes) == len(set(codes)), "Duplicate codes found"

    def test_required_symbols_present(self):
        codes = set(get_codes())
        required = {
            "005930",  # 삼성전자
            "000660",  # SK하이닉스
            "005380",  # 현대차
        }
        for code in required:
            assert code in codes, f"Required symbol {code} not found"

    def test_each_entry_has_required_keys(self):
        for entry in KOSPI200_CONSTITUENTS:
            assert "code" in entry, f"Missing 'code' in entry: {entry}"
            assert "name" in entry, f"Missing 'name' in entry: {entry}"
            assert "sector" in entry, f"Missing 'sector' in entry: {entry}"

    def test_get_codes_returns_list_of_strings(self):
        codes = get_codes()
        assert isinstance(codes, list)
        assert all(isinstance(c, str) for c in codes)
