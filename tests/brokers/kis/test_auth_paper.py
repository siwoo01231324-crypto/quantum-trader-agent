"""KISAuth paper=True 단위테스트 (회귀).

4건: base_url 분기, async_lock 직렬화, RateLimitError fallback, cache 파일 분리.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.brokers.errors import RateLimitError
from src.brokers.kis.auth import KISAuth


def _make_token_response(token: str = "paper-token-abc") -> dict:
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 86400,
        "access_token_token_expired": "2099-01-01 00:00:00",
    }


@pytest.fixture
def paper_auth(tmp_path):
    return KISAuth(
        app_key="fake-key",
        app_secret="fake-secret",
        paper=True,
        cache_path=str(tmp_path / "kis_token_paper.json"),
        lock_dir=str(tmp_path),
    )


@pytest.fixture
def live_auth(tmp_path):
    return KISAuth(
        app_key="fake-key",
        app_secret="fake-secret",
        paper=False,
        cache_path=str(tmp_path / "kis_token_live.json"),
        lock_dir=str(tmp_path),
    )


class TestKISAuthPaperBaseUrl:
    def test_paper_true_uses_vts_base_url(self, paper_auth):
        """paper=True → openapivts URL."""
        assert "openapivts" in paper_auth._base_url
        assert "29443" in paper_auth._base_url

    def test_paper_false_uses_live_base_url(self, live_auth):
        """paper=False → openapi (live) URL."""
        assert "openapivts" not in live_auth._base_url
        assert "9443" in live_auth._base_url


class TestKISAuthAsyncLockSerialization:
    @pytest.mark.asyncio
    async def test_concurrent_async_calls_only_issue_once(self, paper_auth):
        """동시 async 호출 시 _async_lock 이 직렬화 — HTTP 1회만 호출."""
        call_count = 0

        def fake_post(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_resp = MagicMock()
            mock_resp.json.return_value = _make_token_response()
            mock_resp.raise_for_status = MagicMock()
            return mock_resp

        with patch("requests.post", side_effect=fake_post):
            # Run two concurrent coroutines
            results = await asyncio.gather(
                paper_auth.get_token_async(),
                paper_auth.get_token_async(),
            )

        assert all(t == "paper-token-abc" for t in results)
        # Only 1 HTTP call (second hits in-memory cache after lock)
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_async_lock_created_lazily(self, paper_auth):
        """_async_lock 은 첫 async 호출 시 생성."""
        assert paper_auth._async_lock is None

        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            await paper_auth.get_token_async()

        assert paper_auth._async_lock is not None


class TestKISAuthRateLimitFallback:
    def test_network_failure_uses_existing_valid_token(self, paper_auth):
        """네트워크 오류 발생 시 유효한 기존 토큰 fallback 사용 (auth.py:138-142)."""
        # Token about to expire → _should_renew() = True → _issue_token() called
        paper_auth._access_token = "existing-valid-paper"
        paper_auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=4)
        paper_auth._last_issued_at = 0.0

        with patch("requests.post") as mock_post:
            mock_post.side_effect = Exception("network error")
            token = paper_auth.get_token()

        assert token == "existing-valid-paper"
        mock_post.assert_called_once()


class TestKISAuthCacheFileSeparation:
    def test_paper_cache_path_contains_paper(self, tmp_path):
        """paper=True 캐시 파일 경로에 'paper' 포함."""
        auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=True,
            cache_path=str(tmp_path / "kis_token_paper.json"),
            lock_dir=str(tmp_path),
        )
        assert "paper" in str(auth._cache_path)

    def test_live_cache_path_does_not_contain_paper(self, tmp_path):
        """paper=False 캐시 파일 경로에 'paper' 미포함."""
        auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=False,
            cache_path=str(tmp_path / "kis_token_live.json"),
            lock_dir=str(tmp_path),
        )
        assert "paper" not in str(auth._cache_path)

    def test_paper_and_live_use_different_cache_files(self, tmp_path):
        """paper=True / paper=False 는 서로 다른 캐시 파일 사용."""
        paper = KISAuth(
            "k", "s", paper=True,
            cache_path=str(tmp_path / "kis_token_paper.json"),
            lock_dir=str(tmp_path),
        )
        live = KISAuth(
            "k", "s", paper=False,
            cache_path=str(tmp_path / "kis_token_live.json"),
            lock_dir=str(tmp_path),
        )
        assert paper._cache_path != live._cache_path
