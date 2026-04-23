from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.brokers.errors import AuthError, RateLimitError
from src.brokers.kis.auth import KISAuth, _MIN_REISSUE_INTERVAL_SEC


def _make_token_response(expires_in: int = 86400) -> dict:
    return {
        "access_token": "test-token-abc",
        "token_type": "Bearer",
        "expires_in": expires_in,
        "access_token_token_expired": "2099-01-01 00:00:00",
    }


@pytest.fixture
def cache_path(tmp_path):
    return str(tmp_path / "kis_token_paper.json")


@pytest.fixture
def auth(cache_path):
    return KISAuth(
        app_key="fake-key",
        app_secret="fake-secret",
        paper=True,
        cache_path=cache_path,
    )


class TestKISAuthTokenIssuance:
    def test_issues_token_on_first_call(self, auth):
        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            token = auth.get_token()
        assert token == "test-token-abc"

    def test_token_cached_in_memory(self, auth):
        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            auth.get_token()
            auth.get_token()  # second call should not re-issue
        assert mock_post.call_count == 1

    def test_token_persisted_to_disk(self, auth, cache_path):
        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            auth.get_token()
        data = json.loads(Path(cache_path).read_text())
        assert data["access_token"] == "test-token-abc"
        assert data["expires_at"] is not None

    def test_token_loaded_from_disk_on_restart(self, cache_path):
        future = datetime.now(tz=timezone.utc) + timedelta(hours=23)
        Path(cache_path).write_text(
            json.dumps({"access_token": "cached-token", "expires_at": future.isoformat()})
        )
        auth2 = KISAuth("k", "s", paper=True, cache_path=cache_path)
        with patch("requests.post") as mock_post:
            token = auth2.get_token()
        assert token == "cached-token"
        mock_post.assert_not_called()


class TestKISAuthRenewal:
    def test_renews_5min_before_expiry(self, auth):
        # Set token expiring in 4 minutes (within 5-min renewal window)
        auth._access_token = "old-token"
        auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(minutes=4)

        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            token = auth.get_token()
        assert token == "test-token-abc"
        mock_post.assert_called_once()

    def test_no_renewal_when_plenty_of_time(self, auth):
        auth._access_token = "still-valid"
        auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=20)

        with patch("requests.post") as mock_post:
            token = auth.get_token()
        assert token == "still-valid"
        mock_post.assert_not_called()


class TestKISAuthRateLimit:
    def test_rate_limit_1min_enforced(self, auth):
        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            auth.get_token()
            # Force renewal by expiring token
            auth._expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
            with pytest.raises(RateLimitError):
                auth._issue_token()

    def test_rate_limit_passes_after_interval(self, auth):
        with patch("requests.post") as mock_post:
            mock_post.return_value.json.return_value = _make_token_response()
            mock_post.return_value.raise_for_status = MagicMock()
            auth.get_token()
            # Simulate passage of time
            auth._last_issued_at -= _MIN_REISSUE_INTERVAL_SEC + 1
            auth._expires_at = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
            token = auth.get_token()
        assert token == "test-token-abc"


class TestKISAuthFallback:
    def test_fallback_to_existing_valid_token_on_failure(self, auth):
        auth._access_token = "existing-valid"
        auth._expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=10)
        # Force renewal window: set last_issued_at far in past
        auth._last_issued_at = 0.0

        with patch("requests.post") as mock_post:
            mock_post.side_effect = Exception("network error")
            # Should not raise — falls back to existing token
            token = auth.get_token()
        assert token == "existing-valid"

    def test_raises_auth_error_when_no_fallback(self, auth):
        auth._access_token = None
        auth._expires_at = None
        auth._last_issued_at = 0.0

        with patch("requests.post") as mock_post:
            mock_post.side_effect = Exception("network error")
            with pytest.raises(AuthError):
                auth.get_token()
