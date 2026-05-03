"""Cross-process KISAuth token lock tests.

Verifies that when two processes simultaneously call _issue_token:
- Only one makes the HTTP request
- The other falls back to _load_cache and returns the same token
- paper=True vs paper=False use separate lock files
"""
from __future__ import annotations

import json
import multiprocessing
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.brokers.kis.auth import KISAuth


def _make_token_response(token: str = "test-token-abc", expires_in: int = 86400) -> dict:
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": expires_in,
        "access_token_token_expired": "2099-01-01 00:00:00",
    }


def _worker_issue_token(
    cache_path: str,
    paper: bool,
    lock_dir: str,
    result_queue: multiprocessing.Queue,
    http_call_counter: multiprocessing.Value,
    start_barrier: multiprocessing.Barrier,
) -> None:
    """Worker function: tries to issue token, records HTTP call count."""
    import time
    from unittest.mock import MagicMock, patch

    from src.brokers.kis.auth import KISAuth

    def fake_post(*args, **kwargs):
        with http_call_counter.get_lock():
            http_call_counter.value += 1
        # Simulate slight network delay so other process hits lock
        time.sleep(0.05)
        mock_resp = MagicMock()
        mock_resp.json.return_value = _make_token_response()
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    auth = KISAuth(
        app_key="fake-key",
        app_secret="fake-secret",
        paper=paper,
        cache_path=cache_path,
        lock_dir=lock_dir,
    )

    # Both processes start simultaneously
    start_barrier.wait(timeout=5)

    try:
        with patch("requests.post", side_effect=fake_post):
            token = auth.get_token()
        result_queue.put({"token": token, "error": None})
    except Exception as exc:
        result_queue.put({"token": None, "error": str(exc)})


class TestCrossProcessLock:
    def test_only_one_http_call_when_two_processes_race(self, tmp_path):
        """Two processes race to issue token — only 1 HTTP call, both get same token."""
        cache_path = str(tmp_path / "kis_token_paper.json")
        lock_dir = str(tmp_path)

        http_call_counter = multiprocessing.Value("i", 0)
        result_queue = multiprocessing.Queue()
        start_barrier = multiprocessing.Barrier(2)

        processes = [
            multiprocessing.Process(
                target=_worker_issue_token,
                args=(cache_path, True, lock_dir, result_queue, http_call_counter, start_barrier),
            )
            for _ in range(2)
        ]

        for p in processes:
            p.start()
        for p in processes:
            p.join(timeout=10)

        results = [result_queue.get_nowait() for _ in range(2)]

        # Both processes must succeed without error
        assert all(r["error"] is None for r in results), f"Errors: {[r['error'] for r in results]}"
        # Both must return the same token
        tokens = {r["token"] for r in results}
        assert len(tokens) == 1, f"Expected same token from both processes, got: {tokens}"
        # Only 1 HTTP call should have been made
        assert http_call_counter.value == 1, (
            f"Expected 1 HTTP call, got {http_call_counter.value}"
        )

    def test_paper_and_live_use_separate_lock_files(self, tmp_path):
        """paper=True and paper=False use different lock files."""
        paper_auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=True,
            cache_path=str(tmp_path / "paper.json"),
            lock_dir=str(tmp_path),
        )
        live_auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=False,
            cache_path=str(tmp_path / "live.json"),
            lock_dir=str(tmp_path),
        )

        assert paper_auth._token_lock_path != live_auth._token_lock_path
        assert "paper" in str(paper_auth._token_lock_path)
        assert "live" in str(live_auth._token_lock_path)

    def test_paper_lock_file_path_matches_spec(self, tmp_path):
        """paper=True lock file ends with kis_token_paper.lock."""
        auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=True,
            cache_path=str(tmp_path / "token.json"),
            lock_dir=str(tmp_path),
        )
        assert str(auth._token_lock_path).endswith("kis_token_paper.lock")

    def test_live_lock_file_path_matches_spec(self, tmp_path):
        """paper=False lock file ends with kis_token_live.lock."""
        auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=False,
            cache_path=str(tmp_path / "token.json"),
            lock_dir=str(tmp_path),
        )
        assert str(auth._token_lock_path).endswith("kis_token_live.lock")

    def test_lock_fallback_to_cache_when_lock_busy(self, tmp_path):
        """When file lock is busy, _issue_token falls back to _load_cache."""
        cache_path = tmp_path / "kis_token_paper.json"
        future = datetime.now(tz=timezone.utc) + timedelta(hours=23)
        cache_path.write_text(
            json.dumps(
                {
                    "access_token": "cached-token-xyz",
                    "expires_at": future.isoformat(),
                }
            )
        )

        auth = KISAuth(
            app_key="k",
            app_secret="s",
            paper=True,
            cache_path=str(cache_path),
            lock_dir=str(tmp_path),
        )

        # Manually acquire the lock to simulate another process holding it
        from filelock import FileLock

        with FileLock(str(auth._token_lock_path), timeout=0):
            # Force renewal so _issue_token is called
            auth._access_token = None
            auth._expires_at = None
            auth._last_issued_at = 0.0

            with patch("requests.post") as mock_post:
                # Should not raise — falls back to cache
                token = auth.get_token()

            mock_post.assert_not_called()

        assert token == "cached-token-xyz"
