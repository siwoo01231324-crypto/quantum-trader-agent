from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from filelock import FileLock, Timeout

from src.brokers.errors import AuthError, RateLimitError
from src.brokers.kis.schemas import KISTokenResponse

log = logging.getLogger(__name__)

# KIS 토큰 재발급 최소 간격: 1분 1회
_MIN_REISSUE_INTERVAL_SEC = 60
# 만료 5분 전 선갱신
_RENEW_BEFORE_SEC = 300

# cross-process lock 기본 디렉터리
_DEFAULT_LOCK_DIR = ".omc/state"


class KISAuth:
    """KIS OAuth2 토큰 관리.

    - 디스크 캐시로 재시작 시 재사용
    - 1분 1회 재발급 rate limit 준수
    - 만료 5분 전 선갱신
    - 재발급 실패 + 기존 토큰 유효 시 fallback
    - cross-process filelock: 두 프로세스 동시 발급 시 1회만 HTTP 호출
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        paper: bool = True,
        cache_path: str | None = None,
        lock_dir: str | None = None,
    ) -> None:
        self._app_key = app_key
        self._app_secret = app_secret
        self._paper = paper

        if paper:
            self._base_url = "https://openapivts.koreainvestment.com:29443"
        else:
            self._base_url = "https://openapi.koreainvestment.com:9443"

        cache_file = cache_path or (
            f".omc/state/kis_token_{'paper' if paper else 'live'}.json"
        )
        self._cache_path = Path(cache_file)

        # cross-process lock 파일: paper/live 분리
        _lock_dir = Path(lock_dir) if lock_dir else Path(_DEFAULT_LOCK_DIR)
        lock_suffix = "paper" if paper else "live"
        self._token_lock_path = _lock_dir / f"kis_token_{lock_suffix}.lock"

        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._last_issued_at: float = 0.0  # monotonic

        # async concurrent refresh 직렬화 (background task 도입 금지, lazy refresh 구조 유지)
        self._async_lock: asyncio.Lock | None = None

        self._load_cache()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_token(self) -> str:
        """유효한 access_token 반환. 필요 시 갱신."""
        if self._should_renew():
            self._issue_token()
        if not self._access_token:
            raise AuthError("KIS access token unavailable")
        return self._access_token

    async def get_token_async(self) -> str:
        """async 경로용 get_token. asyncio.Lock 으로 concurrent refresh 직렬화.

        lazy refresh 구조는 sync get_token 과 동일하게 유지.
        background task / 선행 갱신 루프 도입 금지.
        """
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        async with self._async_lock:
            if self._should_renew():
                self._issue_token()
            if not self._access_token:
                raise AuthError("KIS access token unavailable")
            return self._access_token

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _should_renew(self) -> bool:
        if self._access_token is None or self._expires_at is None:
            return True
        now = datetime.now(tz=timezone.utc)
        return now >= (self._expires_at - timedelta(seconds=_RENEW_BEFORE_SEC))

    def _check_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_issued_at
        if self._last_issued_at > 0 and elapsed < _MIN_REISSUE_INTERVAL_SEC:
            wait = _MIN_REISSUE_INTERVAL_SEC - elapsed
            raise RateLimitError(
                f"KIS 토큰 재발급은 1분에 1회 제한. {wait:.1f}초 후 재시도."
            )

    def _issue_token(self) -> None:
        # cross-process gate: timeout=0 (non-blocking)
        # 락 획득 실패 → 다른 프로세스가 발급 중 → cache 재로드 후 반환
        self._token_lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file_lock = FileLock(str(self._token_lock_path), timeout=0)
            file_lock.acquire()
        except Timeout:
            log.debug("Token file lock busy — waiting for holder to write cache")
            # Poll cache until the lock holder finishes writing (max ~2s)
            for _ in range(20):
                self._load_cache()
                if self._access_token:
                    return
                time.sleep(0.1)
            raise AuthError("KIS token lock busy and no cached token available after wait")

        try:
            self._check_rate_limit()

            url = f"{self._base_url}/oauth2/tokenP"
            body = {
                "grant_type": "client_credentials",
                "appkey": self._app_key,
                "appsecret": self._app_secret,
            }
            try:
                resp = requests.post(
                    url,
                    json=body,
                    headers={"Content-Type": "application/json"},
                    timeout=10,
                )
                resp.raise_for_status()
                self._last_issued_at = time.monotonic()
                data = KISTokenResponse.model_validate(resp.json())
                self._access_token = data.access_token
                self._expires_at = datetime.now(tz=timezone.utc) + timedelta(
                    seconds=data.expires_in
                )
                self._save_cache()
                log.info("KIS token issued, expires_in=%ds", data.expires_in)
            except RateLimitError:
                raise
            except Exception as exc:
                log.warning("KIS token issuance failed: %s", exc)
                # fallback: 기존 토큰이 아직 유효하면 계속 사용
                if self._access_token and self._expires_at:
                    now = datetime.now(tz=timezone.utc)
                    if now < self._expires_at:
                        log.warning("Using existing valid token as fallback")
                        return
                raise AuthError(f"KIS token issuance failed: {exc}") from exc
        finally:
            file_lock.release()

    def _load_cache(self) -> None:
        if not self._cache_path.exists():
            return
        try:
            data = json.loads(self._cache_path.read_text())
            self._access_token = data.get("access_token")
            exp_str = data.get("expires_at")
            if exp_str:
                self._expires_at = datetime.fromisoformat(exp_str)
            log.debug("Loaded KIS token from cache %s", self._cache_path)
        except Exception as exc:
            log.warning("Failed to load KIS token cache: %s", exc)

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "access_token": self._access_token,
                "expires_at": self._expires_at.isoformat() if self._expires_at else None,
            }
            self._cache_path.write_text(json.dumps(payload))
        except Exception as exc:
            log.warning("Failed to save KIS token cache: %s", exc)
