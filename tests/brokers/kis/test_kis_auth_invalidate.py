"""Tests for KISAuth.invalidate() + KISClient 5xx auto-invalidate (#127 후속).

Background:
- KIS server-side 가 토큰을 무효화했을 때 daemon 의 disk-cached 토큰이 stale.
- 14h 동안 1287/0 success rate 발생 (실측 2026-05-06).
- Fix: 5xx 응답 받으면 1회 invalidate + retry → 새 토큰으로 다시 시도.

Covers:
1. invalidate() clears in-memory + disk cache
2. invalidate() is safe when cache file missing
3. _request_with_retry calls invalidate on first 5xx
4. _request_with_retry only invalidates ONCE per request (no infinite loop)
5. _request_with_retry calls invalidate on 401 too
6. Successful retry after invalidate returns data
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.brokers.kis.auth import KISAuth
from src.brokers.kis.rest import KISClient


# ---------------------------------------------------------------------------
# KISAuth.invalidate()
# ---------------------------------------------------------------------------

class TestAuthInvalidate:
    def _make_auth(self, tmp_path: Path) -> KISAuth:
        return KISAuth(
            app_key="test-key",
            app_secret="test-secret",
            paper=True,
            cache_path=str(tmp_path / "kis_token_paper.json"),
            lock_dir=str(tmp_path),
        )

    def test_clears_in_memory_token(self, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        auth._access_token = "stale-token"
        auth._expires_at = datetime.now(timezone.utc) + timedelta(hours=12)
        auth.invalidate()
        assert auth._access_token is None
        assert auth._expires_at is None

    def test_deletes_disk_cache(self, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        cache = Path(auth._cache_path)
        cache.write_text(json.dumps({
            "access_token": "stale",
            "expires_at": "2026-05-06T12:00:00+00:00",
        }))
        assert cache.exists()
        auth.invalidate()
        assert not cache.exists()

    def test_safe_when_cache_missing(self, tmp_path: Path):
        auth = self._make_auth(tmp_path)
        # Cache never created — invalidate must not raise.
        auth.invalidate()
        assert auth._access_token is None

    def test_swallows_unlink_error(self, tmp_path: Path, monkeypatch):
        auth = self._make_auth(tmp_path)
        cache = Path(auth._cache_path)
        cache.write_text("{}")
        # Force unlink failure
        original_unlink = Path.unlink
        def raising_unlink(self, *a, **kw):  # noqa: ARG001
            raise PermissionError("simulated")
        monkeypatch.setattr(Path, "unlink", raising_unlink)
        # Must not raise
        auth.invalidate()
        # In-memory still cleared even if disk cleanup failed
        assert auth._access_token is None


# ---------------------------------------------------------------------------
# KISClient._request_with_retry — auto-invalidate on 5xx
# ---------------------------------------------------------------------------

def _make_client(auth: KISAuth | MagicMock) -> KISClient:
    return KISClient(
        auth=auth,
        app_key="test-key",
        app_secret="test-secret",
        cano="50128735",
        acnt_prdt_cd="01",
        paper=True,
    )


def _mock_response(status: int, json_body: dict | None = None, text: str = "") -> MagicMock:
    resp = MagicMock(spec=requests.Response)
    resp.status_code = status
    resp.text = text or (json.dumps(json_body) if json_body else "")
    resp.json.return_value = json_body or {}
    if status >= 400:
        err = requests.HTTPError(f"HTTP {status}", response=resp)
        resp.raise_for_status.side_effect = err
    else:
        resp.raise_for_status.return_value = None
    return resp


class TestRetryAutoInvalidate:
    def test_5xx_triggers_invalidate(self, tmp_path: Path):
        """First 5xx response → auth.invalidate() called → retry succeeds."""
        auth = MagicMock(spec=KISAuth)
        auth.get_token.return_value = "tok"
        client = _make_client(auth)
        ok_body = {"rt_cd": "0", "output1": {"price": "100"}}
        responses = [
            _mock_response(500, text="server boom"),
            _mock_response(200, ok_body),
        ]
        with patch("src.brokers.kis.rest.requests.request", side_effect=responses):
            data = client._get(
                "/uapi/test/v1/quotations/inquire-test", "FHKST00000000",
                {"FID_INPUT": "X"},
            )
        assert data == ok_body
        # invalidate called exactly once
        assert auth.invalidate.call_count == 1

    def test_401_triggers_invalidate(self, tmp_path: Path):
        """401 unauthorized → auth.invalidate() called → retry."""
        auth = MagicMock(spec=KISAuth)
        auth.get_token.return_value = "tok"
        client = _make_client(auth)
        ok_body = {"rt_cd": "0", "output1": {}}
        responses = [
            _mock_response(401, text="unauthorized"),
        ]
        # 401 raises immediately (no retry on 4xx)
        with patch("src.brokers.kis.rest.requests.request", side_effect=responses):
            with pytest.raises(requests.HTTPError):
                client._get(
                    "/uapi/test", "FHKST00000000", {"FID_INPUT": "X"},
                )
        # invalidate still called for 401 (token revoke 의심)
        assert auth.invalidate.call_count == 1

    def test_invalidate_only_once_per_request(self, tmp_path: Path):
        """Repeated 5xx within single request: invalidate called only once."""
        auth = MagicMock(spec=KISAuth)
        auth.get_token.return_value = "tok"
        client = _make_client(auth)
        # 3 retries, all 500 → final raises
        responses = [
            _mock_response(500, text="boom-1"),
            _mock_response(500, text="boom-2"),
            _mock_response(500, text="boom-3"),
        ]
        with patch("src.brokers.kis.rest.requests.request", side_effect=responses):
            with pytest.raises(requests.HTTPError):
                client._get("/uapi/test", "FHKST00000000", {"FID_INPUT": "X"})
        # Only 1 invalidate even though 3 5xx responses
        assert auth.invalidate.call_count == 1

    def test_2xx_does_not_invalidate(self, tmp_path: Path):
        """Successful 200 response: invalidate not called."""
        auth = MagicMock(spec=KISAuth)
        auth.get_token.return_value = "tok"
        client = _make_client(auth)
        ok_body = {"rt_cd": "0", "output1": {"price": "100"}}
        responses = [_mock_response(200, ok_body)]
        with patch("src.brokers.kis.rest.requests.request", side_effect=responses):
            data = client._get("/uapi/test", "FHKST00000000", {"FID_INPUT": "X"})
        assert data == ok_body
        assert auth.invalidate.call_count == 0
