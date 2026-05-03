"""Tests for the dpapi module (Windows only)."""

from __future__ import annotations

import sys
import pytest

if sys.platform != "win32":
    pytest.skip("DPAPI tests require Windows", allow_module_level=True)

from src.security.dpapi import encrypt, decrypt, EncryptionError, DecryptionError


def test_encrypt_returns_bytes() -> None:
    blob = encrypt(b"hello world")
    assert isinstance(blob, bytes)
    assert len(blob) > 0


def test_encrypt_decrypt_roundtrip() -> None:
    plaintext = b"test secret value"
    blob = encrypt(plaintext, description="test")
    recovered, desc = decrypt(blob)
    assert recovered == plaintext
    assert desc == "test"


def test_encrypted_blob_differs_from_plaintext() -> None:
    plaintext = b"must_not_appear_in_blob"
    blob = encrypt(plaintext)
    assert plaintext not in blob


def test_decrypt_garbage_raises_decryption_error() -> None:
    with pytest.raises(DecryptionError):
        decrypt(b"\x00" * 128)


def test_encrypt_empty_bytes() -> None:
    blob = encrypt(b"")
    recovered, _ = decrypt(blob)
    assert recovered == b""


def test_encrypt_unicode_payload() -> None:
    plaintext = "한국어 API 키 테스트 🔐".encode("utf-8")
    blob = encrypt(plaintext, description="unicode test")
    recovered, _ = decrypt(blob)
    assert recovered == plaintext
