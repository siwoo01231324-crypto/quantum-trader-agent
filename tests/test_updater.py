"""Unit tests for src.updater (#128)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.updater import (
    UpdateChecker,
    UpdateInstaller,
    BackupNotFoundError,
    ChecksumMismatch,
    ReleaseInfo,
)


# ---------------------------------------------------------------------------
# UpdateChecker
# ---------------------------------------------------------------------------

def _mock_response(status: int = 200, json_data=None, text: str = "", content: bytes = b""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = json_data or {}
    r.text = text
    r.content = content
    r.iter_content.return_value = [content] if content else []
    return r


def test_checker_returns_none_when_kill_switch_tripped():
    checker = UpdateChecker(
        owner="o", repo="r",
        is_safe_to_run=lambda: False,
        http_get=lambda *a, **kw: _mock_response(),
    )
    assert checker.check_latest() is None


def test_checker_returns_release_info_with_assets():
    release_payload = {
        "tag_name": "v1.2.3",
        "body": "release notes",
        "assets": [
            {"name": "qta.exe", "browser_download_url": "https://x/qta.exe"},
            {"name": "qta.exe.sha256", "browser_download_url": "https://x/qta.sha256"},
        ],
    }
    sha_text = "abcd1234" + "0" * 56 + "  qta.exe"

    def fake_get(url, **kw):
        if url.endswith(".sha256"):
            return _mock_response(text=sha_text)
        return _mock_response(json_data=release_payload)

    checker = UpdateChecker(owner="o", repo="r", http_get=fake_get)
    info = checker.check_latest()
    assert info is not None
    assert info.tag == "v1.2.3"
    assert info.sha256 == "abcd1234" + "0" * 56


def test_checker_returns_none_when_no_assets():
    payload = {"tag_name": "v1", "assets": []}
    checker = UpdateChecker(
        owner="o", repo="r",
        http_get=lambda *a, **kw: _mock_response(json_data=payload),
    )
    assert checker.check_latest() is None


def test_download_and_verify_success(tmp_path):
    content = b"binary-payload"
    sha256 = hashlib.sha256(content).hexdigest()
    release = ReleaseInfo(tag="v1", asset_url="https://x/a.exe", sha256=sha256, body="")
    dest = tmp_path / "downloaded.exe"

    def fake_get(url, **kw):
        return _mock_response(content=content)

    checker = UpdateChecker(owner="o", repo="r", http_get=fake_get)
    out = checker.download_and_verify(release, dest)
    assert out.exists()
    assert out.read_bytes() == content


def test_download_and_verify_mismatch_raises(tmp_path):
    release = ReleaseInfo(tag="v1", asset_url="https://x", sha256="0" * 64, body="")
    dest = tmp_path / "bad.exe"

    def fake_get(url, **kw):
        return _mock_response(content=b"different")

    checker = UpdateChecker(owner="o", repo="r", http_get=fake_get)
    with pytest.raises(ChecksumMismatch):
        checker.download_and_verify(release, dest)
    assert not dest.exists()  # cleaned up


def test_download_refuses_when_kill_switch(tmp_path):
    release = ReleaseInfo(tag="v1", asset_url="x", sha256="x", body="")
    checker = UpdateChecker(
        owner="o", repo="r",
        is_safe_to_run=lambda: False,
        http_get=lambda *a, **kw: _mock_response(),
    )
    with pytest.raises(RuntimeError, match="KillSwitch"):
        checker.download_and_verify(release, tmp_path / "x.exe")


# ---------------------------------------------------------------------------
# UpdateInstaller
# ---------------------------------------------------------------------------

def test_install_backs_up_and_replaces(tmp_path):
    target = tmp_path / "qta.exe"
    target.write_bytes(b"OLD")
    new = tmp_path / "new.exe"
    new.write_bytes(b"NEW")

    installer = UpdateInstaller(target_exe=target, backup_root=tmp_path / "backup")
    backup = installer.install(new)

    assert target.read_bytes() == b"NEW"
    assert backup.read_bytes() == b"OLD"


def test_install_creates_backup_dir(tmp_path):
    target = tmp_path / "qta.exe"
    target.write_bytes(b"OLD")
    new = tmp_path / "new.exe"
    new.write_bytes(b"NEW")
    backup_root = tmp_path / "subdir" / "backup"

    installer = UpdateInstaller(target_exe=target, backup_root=backup_root)
    installer.install(new)

    assert backup_root.exists()


def test_rollback_restores_latest_backup(tmp_path):
    target = tmp_path / "qta.exe"
    target.write_bytes(b"V3")
    backup_root = tmp_path / "backup"
    backup_root.mkdir()
    # Simulate two prior backups
    older = backup_root / "qta-20260101T000000Z.exe"
    older.write_bytes(b"V1")
    newer = backup_root / "qta-20260201T000000Z.exe"
    newer.write_bytes(b"V2")
    # Touch newer to ensure mtime ordering
    import os, time
    os.utime(older, (time.time() - 1000, time.time() - 1000))

    installer = UpdateInstaller(target_exe=target, backup_root=backup_root)
    restored = installer.rollback()

    assert target.read_bytes() == b"V2"
    assert restored == newer


def test_rollback_raises_when_no_backups(tmp_path):
    target = tmp_path / "qta.exe"
    target.write_bytes(b"X")
    installer = UpdateInstaller(target_exe=target, backup_root=tmp_path / "missing")
    with pytest.raises(BackupNotFoundError):
        installer.rollback()
