"""Backup + install + rollback (#128 AC4)."""
from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path


class BackupNotFoundError(RuntimeError):
    """Raised when rollback is attempted but no backup exists."""


def _default_backup_root() -> Path:
    """Resolve %APPDATA%/qta/backup on Windows, ~/.qta/backup elsewhere."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / "qta" / "backup"
    return Path.home() / ".qta" / "backup"


class UpdateInstaller:
    """Replace current EXE with new EXE; preserve previous as backup.

    `backup_root` defaults to %APPDATA%/qta/backup (Windows) or ~/.qta/backup.
    """

    def __init__(
        self,
        target_exe: Path,
        backup_root: Path | None = None,
    ) -> None:
        self.target = target_exe
        self.backup_root = backup_root or _default_backup_root()

    def _now_tag(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def install(self, new_exe: Path) -> Path:
        """Backup current target_exe and replace with new_exe.

        Returns: backup path (use this for rollback).
        """
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_root / f"{self.target.stem}-{self._now_tag()}{self.target.suffix}"
        if self.target.exists():
            shutil.copy2(self.target, backup_path)
        shutil.copy2(new_exe, self.target)
        return backup_path

    def rollback(self) -> Path:
        """Restore most recent backup as the active EXE.

        Raises:
            BackupNotFoundError: if no backups exist.
        """
        if not self.backup_root.exists():
            raise BackupNotFoundError(f"no backup root: {self.backup_root}")
        backups = sorted(
            (p for p in self.backup_root.iterdir() if p.is_file() and p.stem.startswith(self.target.stem)),
            key=lambda p: p.stat().st_mtime,
        )
        if not backups:
            raise BackupNotFoundError(f"no backups for {self.target.name}")
        latest = backups[-1]
        shutil.copy2(latest, self.target)
        return latest
