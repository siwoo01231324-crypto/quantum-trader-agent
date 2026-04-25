"""KIS auth async 단위 테스트 (AC8).

- concurrent 2 coroutine 이 동일 토큰 공유, 1회만 refresh
- get_token_async 는 sync get_token 과 동일한 lazy refresh 구조
- asyncio.Lock 으로 concurrent refresh 직렬화 확인
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from src.brokers.kis.auth import KISAuth


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_auth(tmp_path):
    auth = KISAuth(
        app_key="test_key",
        app_secret="test_secret",
        paper=True,
        cache_path=str(tmp_path / "tok.json"),
    )
    return auth


@pytest.fixture
def valid_auth(tmp_path):
    auth = KISAuth(
        app_key="test_key",
        app_secret="test_secret",
        paper=True,
        cache_path=str(tmp_path / "tok.json"),
    )
    auth._access_token = "valid_token"
    auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
    return auth


# ---------------------------------------------------------------------------
# Test 1: 유효 토큰 → refresh 없이 즉시 반환
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_token_async_returns_valid_token(valid_auth):
    token = await valid_auth.get_token_async()
    assert token == "valid_token"


# ---------------------------------------------------------------------------
# Test 2: concurrent 2 coroutine → 1회만 _issue_token 호출
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_concurrent_refresh_only_once(fresh_auth):
    """두 코루틴이 동시에 get_token_async → _issue_token 은 1회만 호출."""
    issue_count = 0

    original_issue = fresh_auth._issue_token

    def fake_issue():
        nonlocal issue_count
        issue_count += 1
        fresh_auth._access_token = "issued_token"
        fresh_auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        fresh_auth._last_issued_at = 9999.0  # rate limit 우회

    fresh_auth._issue_token = fake_issue

    results = await asyncio.gather(
        fresh_auth.get_token_async(),
        fresh_auth.get_token_async(),
    )

    assert all(t == "issued_token" for t in results)
    assert issue_count == 1, f"_issue_token 이 {issue_count}회 호출됨 (1회 기대)"


# ---------------------------------------------------------------------------
# Test 3: sync get_token 과 async get_token_async 가 동일 토큰 공유
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_and_async_share_same_token(valid_auth):
    sync_token = valid_auth.get_token()
    async_token = await valid_auth.get_token_async()
    assert sync_token == async_token == "valid_token"


# ---------------------------------------------------------------------------
# Test 4: asyncio.Lock 이 지연 초기화됨 (이벤트 루프 없을 때 None)
# ---------------------------------------------------------------------------

def test_async_lock_initially_none(fresh_auth):
    assert fresh_auth._async_lock is None


@pytest.mark.asyncio
async def test_async_lock_created_on_first_call(valid_auth):
    assert valid_auth._async_lock is None
    await valid_auth.get_token_async()
    assert valid_auth._async_lock is not None
    assert isinstance(valid_auth._async_lock, asyncio.Lock)


# ---------------------------------------------------------------------------
# Test 5: 만료된 토큰 → refresh 후 반환
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_expired_token_triggers_refresh(fresh_auth):
    fresh_auth._access_token = "old_token"
    fresh_auth._expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)

    def fake_issue():
        fresh_auth._access_token = "new_token"
        fresh_auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        fresh_auth._last_issued_at = 9999.0

    fresh_auth._issue_token = fake_issue

    token = await fresh_auth.get_token_async()
    assert token == "new_token"
