from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from src.brokers.errors import AuthError, RateLimitError
from src.brokers.kis.schemas import KISTokenResponse

log = logging.getLogger(__name__)

# KIS 토큰 재발급 최소 간격: 1분 1회
_MIN_REISSUE_INTERVAL_SEC = 60
# 만료 5분 전 선갱신
_RENEW_BEFORE_SEC = 300


class KISAuth:
    """KIS OAuth2 토큰 관리.

    - 디스크 캐시로 재시작 시 재사용
    - 1분 1회 재발급 rate limit 준수
    - 만료 5분 전 선갱신
    - 재발급 실패 + 기존 토큰 유효 시 fallback
    """

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        paper: bool = True,
        cache_path: str | None = None,
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

        self._access_token: str | None = None
        self._expires_at: datetime | None = None
        self._last_issued_at: float = 0.0  # monotonic

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
