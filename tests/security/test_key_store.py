"""Tests for KeyStore — run on Windows only (DPAPI required).

Key behaviors verified:
- put/get round-trip
- rotate replaces value, meta timestamp updates
- delete removes blob and meta
- list_keys reflects stored names
- KeyNotFoundError on missing key
- Invalid name raises KeyStoreError
- Blob on disk is not plaintext (no-plaintext-exposure check)
- Decryption failure when blob is tampered (simulates wrong-user scenario)
"""

from __future__ import annotations

import json
import sys
import pytest

if sys.platform != "win32":
    pytest.skip("DPAPI tests require Windows", allow_module_level=True)

import tempfile
from pathlib import Path
from unittest.mock import patch

from src.security.key_store import KeyStore, KeyNotFoundError, KeyStoreError
from src.security.dpapi import DecryptionError


@pytest.fixture
def store(tmp_path: Path) -> KeyStore:
    return KeyStore(base_dir=tmp_path)


def test_put_and_get_roundtrip(store: KeyStore) -> None:
    store.put("BINANCE_API_KEY", "super_secret_123")
    assert store.get("BINANCE_API_KEY") == "super_secret_123"


def test_get_missing_key_raises(store: KeyStore) -> None:
    with pytest.raises(KeyNotFoundError):
        store.get("NONEXISTENT_KEY")


def test_rotate_updates_value(store: KeyStore) -> None:
    store.put("KIS_SECRET", "old_value")
    store.rotate("KIS_SECRET", "new_value")
    assert store.get("KIS_SECRET") == "new_value"


def test_rotate_updates_meta_timestamp(store: KeyStore, tmp_path: Path) -> None:
    store.put("KIS_SECRET", "val1")
    meta_before = store.meta("KIS_SECRET")
    import time; time.sleep(0.01)
    store.rotate("KIS_SECRET", "val2")
    meta_after = store.meta("KIS_SECRET")
    assert meta_after["updated_at"] >= meta_before["updated_at"]


def test_delete_removes_files(store: KeyStore, tmp_path: Path) -> None:
    store.put("MY_KEY", "some_secret")
    store.delete("MY_KEY")
    assert not (tmp_path / "MY_KEY.blob").exists()
    assert not (tmp_path / "MY_KEY.meta").exists()


def test_delete_nonexistent_is_noop(store: KeyStore) -> None:
    store.delete("DOES_NOT_EXIST")  # must not raise


def test_list_keys(store: KeyStore) -> None:
    store.put("KEY_A", "a")
    store.put("KEY_B", "b")
    assert store.list_keys() == ["KEY_A", "KEY_B"]


def test_blob_on_disk_is_not_plaintext(store: KeyStore, tmp_path: Path) -> None:
    secret = "plaintext_must_not_appear_on_disk"
    store.put("LEAK_CHECK", secret)
    blob_bytes = (tmp_path / "LEAK_CHECK.blob").read_bytes()
    assert secret.encode("utf-8") not in blob_bytes, "Plaintext found in blob file!"


def test_meta_file_does_not_contain_secret(store: KeyStore, tmp_path: Path) -> None:
    secret = "meta_must_not_leak_secret_value"
    store.put("META_LEAK", secret)
    meta_text = (tmp_path / "META_LEAK.meta").read_text(encoding="utf-8")
    assert secret not in meta_text, "Secret found in meta file!"


def test_invalid_key_name_raises(store: KeyStore) -> None:
    with pytest.raises(KeyStoreError):
        store.put("invalid name with spaces", "val")


def test_invalid_key_name_empty_raises(store: KeyStore) -> None:
    with pytest.raises(KeyStoreError):
        store.put("", "val")


def test_tampered_blob_raises_decryption_error(store: KeyStore, tmp_path: Path) -> None:
    """Simulates wrong-user / corrupted blob scenario."""
    store.put("TAMPERED", "legit_value")
    blob_path = tmp_path / "TAMPERED.blob"
    # Overwrite blob with garbage bytes — DPAPI will reject it
    blob_path.write_bytes(b"\x00" * 64)
    with pytest.raises(DecryptionError):
        store.get("TAMPERED")


def test_different_user_sid_decryption_blocked(store: KeyStore) -> None:
    """Simulates a different-user decrypt attempt by mocking dpapi.decrypt to raise."""
    store.put("CROSS_USER_KEY", "sensitive_data")
    with patch("src.security.key_store.dpapi.decrypt", side_effect=DecryptionError("wrong SID")):
        with pytest.raises(DecryptionError):
            store.get("CROSS_USER_KEY")


def test_rotate_missing_key_creates_new(store: KeyStore) -> None:
    """rotate() on a missing key should still work (same code path as put)."""
    store.rotate("BRAND_NEW", "fresh_secret")
    assert store.get("BRAND_NEW") == "fresh_secret"
