"""Root-level pytest configuration.

Windows asyncio compatibility: forces SelectorEventLoopPolicy so that
websockets and httpx async tests work correctly on Windows 11 (R12 in plan).
Without this, ProactorEventLoop causes WS close hang on Windows.
"""
from __future__ import annotations

import asyncio
import sys

import pytest


def pytest_configure(config):
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@pytest.fixture(autouse=True)
def _isolate_manual_trade_store(tmp_path, monkeypatch):
    """모든 테스트를 실 ``logs/manual_trade.jsonl`` 오염으로부터 격리.

    수동거래 WRITE 핸들러가 ``state.log_dir`` 을 무시하고 실 ``logs/`` 에 써서,
    ``test_dashboard_manual_trade`` 가 실행마다 실파일에 샘플 8건을 주입하던
    사고 (2026-06-09: 사용자 대시보드 "오늘 입력" 에 안 한 거래가 뜸). WRITE
    경로는 ``QTA_MANUAL_TRADE_DIR`` env 로 base 오버라이드되므로
    (``src/dashboard/app.py``) 테스트마다 고유 tmp 로 지정 → 완전 격리 +
    미래 테스트 자동 보호. autouse 라 opt-in 불필요.
    """
    monkeypatch.setenv("QTA_MANUAL_TRADE_DIR", str(tmp_path / "manual_trade_isolated"))
