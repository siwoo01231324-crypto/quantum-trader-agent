"""Auto-update channel for QTA (#128).

Modules:
- checker: GitHub Release API polling + SHA256 verify
- installer: backup + install + rollback
"""
from src.updater.checker import (
    UpdateChecker,
    ReleaseInfo,
    ChecksumMismatch,
)
from src.updater.installer import (
    UpdateInstaller,
    BackupNotFoundError,
)

__all__ = [
    "UpdateChecker",
    "ReleaseInfo",
    "ChecksumMismatch",
    "UpdateInstaller",
    "BackupNotFoundError",
]
