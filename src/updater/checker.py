"""GitHub Release polling + SHA256 verify (#128 AC1, AC2)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

import requests


REPO_API = "https://api.github.com/repos/{owner}/{repo}/releases/latest"


class ChecksumMismatch(RuntimeError):
    """Raised when downloaded artifact SHA256 does not match release manifest."""


@dataclass
class ReleaseInfo:
    tag: str
    asset_url: str
    sha256: str
    body: str


class UpdateChecker:
    """Poll GitHub Releases and verify artifact integrity.

    KillSwitch integration (AC3): caller passes `is_safe_to_run` callback.
    Returns False during emergency halt → skip update.
    """

    def __init__(
        self,
        owner: str,
        repo: str,
        is_safe_to_run: Callable[[], bool] | None = None,
        http_get: Callable[..., requests.Response] | None = None,
    ) -> None:
        self.owner = owner
        self.repo = repo
        self._is_safe = is_safe_to_run or (lambda: True)
        self._http_get = http_get or requests.get

    def check_latest(self) -> Optional[ReleaseInfo]:
        """Return latest release info, or None if KillSwitch tripped or no release."""
        if not self._is_safe():
            return None
        url = REPO_API.format(owner=self.owner, repo=self.repo)
        resp = self._http_get(url, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        assets = data.get("assets", [])
        if not assets:
            return None

        # Convention: first .exe asset + matching .sha256 asset
        exe_asset = next((a for a in assets if a["name"].endswith(".exe")), None)
        sha_asset = next((a for a in assets if a["name"].endswith(".sha256")), None)
        if not exe_asset or not sha_asset:
            return None

        sha_resp = self._http_get(sha_asset["browser_download_url"], timeout=10)
        if sha_resp.status_code != 200:
            return None
        sha256_expected = sha_resp.text.strip().split()[0]

        return ReleaseInfo(
            tag=data["tag_name"],
            asset_url=exe_asset["browser_download_url"],
            sha256=sha256_expected,
            body=data.get("body", ""),
        )

    def download_and_verify(self, release: ReleaseInfo, dest: Path) -> Path:
        """Download artifact and verify SHA256.

        Raises:
            ChecksumMismatch: if downloaded file SHA256 differs from manifest.
        """
        if not self._is_safe():
            raise RuntimeError("KillSwitch tripped — refuse to download update")
        dest.parent.mkdir(parents=True, exist_ok=True)
        resp = self._http_get(release.asset_url, timeout=60, stream=True)
        h = hashlib.sha256()
        with dest.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    h.update(chunk)
                    f.write(chunk)
        digest = h.hexdigest()
        if digest != release.sha256:
            dest.unlink(missing_ok=True)
            raise ChecksumMismatch(
                f"sha256 mismatch: expected={release.sha256[:16]}... got={digest[:16]}..."
            )
        return dest
