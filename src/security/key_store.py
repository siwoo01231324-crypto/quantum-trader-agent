"""DPAPI-backed API key store.

Storage layout:
    %APPDATA%/qta/secrets/<key_name>.blob   — DPAPI-encrypted binary blob
    %APPDATA%/qta/secrets/<key_name>.meta   — plaintext JSON metadata (name, updated_at)

The .blob file never contains plaintext. The .meta file never contains the
secret value — only name and timestamp.

Usage:
    store = KeyStore()
    store.put("BINANCE_API_KEY", "my_secret_value")
    value = store.get("BINANCE_API_KEY")
    store.rotate("BINANCE_API_KEY", "new_secret_value")
    store.delete("BINANCE_API_KEY")
    names = store.list_keys()
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

if sys.platform != "win32":
    raise RuntimeError("KeyStore requires Windows (DPAPI unavailable on this platform)")

from src.security import dpapi


class KeyStoreError(Exception):
    pass


class KeyNotFoundError(KeyStoreError):
    pass


class KeyStore:
    """Stores API keys encrypted with Windows DPAPI in %APPDATA%/qta/secrets/."""

    def __init__(self, base_dir: Path | None = None) -> None:
        if base_dir is None:
            appdata = os.environ.get("APPDATA")
            if not appdata:
                raise KeyStoreError("APPDATA environment variable is not set")
            base_dir = Path(appdata) / "qta" / "secrets"
        self._dir = Path(base_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def put(self, name: str, secret: str) -> None:
        """Store (or overwrite) *secret* under *name*, encrypted with DPAPI."""
        self._validate_name(name)
        plaintext = secret.encode("utf-8")
        blob = dpapi.encrypt(plaintext, description=name)
        blob_path = self._blob_path(name)
        meta_path = self._meta_path(name)
        blob_path.write_bytes(blob)
        meta_path.write_text(
            json.dumps({"name": name, "updated_at": _now_iso()}),
            encoding="utf-8",
        )

    def get(self, name: str) -> str:
        """Retrieve the plaintext secret for *name*.

        Raises KeyNotFoundError if not stored.
        Raises dpapi.DecryptionError if DPAPI rejects the blob (wrong user/machine).
        """
        self._validate_name(name)
        blob_path = self._blob_path(name)
        if not blob_path.exists():
            raise KeyNotFoundError(f"Key '{name}' not found in store")
        blob = blob_path.read_bytes()
        plaintext, _ = dpapi.decrypt(blob)
        return plaintext.decode("utf-8")

    def rotate(self, name: str, new_secret: str) -> None:
        """Replace the stored secret for *name* atomically.

        Writes the new blob first, then deletes the old one — if write fails
        the old blob is untouched.
        """
        self._validate_name(name)
        # Encode new blob
        plaintext = new_secret.encode("utf-8")
        new_blob = dpapi.encrypt(plaintext, description=name)

        blob_path = self._blob_path(name)
        meta_path = self._meta_path(name)
        tmp_path = blob_path.with_suffix(".blob.tmp")

        # Write new blob to temp file first (atomic-ish on Windows)
        tmp_path.write_bytes(new_blob)
        tmp_path.replace(blob_path)  # atomic on same filesystem
        meta_path.write_text(
            json.dumps({"name": name, "updated_at": _now_iso()}),
            encoding="utf-8",
        )

    def delete(self, name: str) -> None:
        """Delete stored key *name*. No-op if not found."""
        self._validate_name(name)
        blob_path = self._blob_path(name)
        meta_path = self._meta_path(name)
        if blob_path.exists():
            blob_path.unlink()
        if meta_path.exists():
            meta_path.unlink()

    def list_keys(self) -> list[str]:
        """Return sorted list of stored key names."""
        return sorted(p.stem for p in self._dir.glob("*.blob"))

    def meta(self, name: str) -> dict:
        """Return metadata dict for *name* (name, updated_at)."""
        meta_path = self._meta_path(name)
        if not meta_path.exists():
            raise KeyNotFoundError(f"Key '{name}' not found in store")
        return json.loads(meta_path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _blob_path(self, name: str) -> Path:
        return self._dir / f"{name}.blob"

    def _meta_path(self, name: str) -> Path:
        return self._dir / f"{name}.meta"

    @staticmethod
    def _validate_name(name: str) -> None:
        if not name or not name.replace("_", "").replace("-", "").isalnum():
            raise KeyStoreError(
                f"Invalid key name '{name}': only alphanumeric, underscore, hyphen allowed"
            )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()
