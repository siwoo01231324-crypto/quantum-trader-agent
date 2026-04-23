"""Integration test fixtures.

Run with: pytest -m integration
Default pytest config skips integration tests (addopts = "-m 'not integration'").
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest


def _is_krx_open() -> bool:
    now = datetime.now(ZoneInfo("Asia/Seoul"))
    if now.weekday() >= 5:
        return False
    open_time = now.replace(hour=9, minute=0, second=0, microsecond=0)
    close_time = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return open_time <= now <= close_time


@pytest.fixture(scope="session")
def binance_creds() -> tuple[str, str, str, str]:
    api_key = os.environ.get("BINANCE_DEMO_API_KEY", "")
    secret = os.environ.get("BINANCE_DEMO_SECRET_API_KEY", "")
    if not api_key or not secret:
        pytest.skip("no binance testnet creds")
    return (
        api_key,
        secret,
        "https://testnet.binancefuture.com",
        "wss://fstream.binancefuture.com/ws",
    )


@pytest.fixture(scope="session")
def kis_paper_creds() -> tuple[str, str, str]:
    app_key = os.environ.get("HANTOO_FAKE_API_KEY", "")
    app_secret = os.environ.get("HANTOO_FAKE_SECRET_API_KEY", "")
    credit_number = os.environ.get("HANTOO_CREDIT_NUMBER", "")
    if not app_key or not app_secret or not credit_number:
        pytest.skip("no KIS paper creds")
    return (app_key, app_secret, credit_number)
