"""Windows DPAPI encrypt/decrypt helpers.

Uses CryptProtectData / CryptUnprotectData bound to the current user's SID.
Any attempt to decrypt on a different user account (different SID) will raise
DecryptionError — this is enforced by the OS, not by this code.

Platform: Windows only. Raises RuntimeError on non-Windows.
"""

from __future__ import annotations

import sys

if sys.platform != "win32":
    raise RuntimeError("src.security.dpapi is Windows-only (DPAPI unavailable on this platform)")

import win32crypt  # pywin32


class DPAPIError(Exception):
    pass


class EncryptionError(DPAPIError):
    pass


class DecryptionError(DPAPIError):
    pass


def encrypt(plaintext: bytes, description: str = "") -> bytes:
    """Encrypt *plaintext* with DPAPI (current-user scope).

    Returns an opaque DPAPI blob. The blob is only decryptable by the same
    Windows user (same SID) on the same machine by default.
    """
    try:
        blob = win32crypt.CryptProtectData(
            plaintext,
            description,
            None,   # optional entropy
            None,   # reserved
            None,   # prompt struct
            0,      # flags: 0 = current-user scope (CRYPTPROTECT_LOCAL_MACHINE not set)
        )
        return blob
    except Exception as exc:
        raise EncryptionError(f"DPAPI encryption failed: {exc}") from exc


def decrypt(blob: bytes) -> tuple[bytes, str]:
    """Decrypt a DPAPI *blob*.

    Returns ``(plaintext_bytes, description_str)``.
    Raises DecryptionError if the blob was encrypted by a different user,
    is corrupted, or on any OS-level failure.
    """
    try:
        description, plaintext = win32crypt.CryptUnprotectData(
            blob,
            None,   # optional entropy
            None,   # reserved
            None,   # prompt struct
            0,      # flags
        )
        return plaintext, description
    except Exception as exc:
        raise DecryptionError(f"DPAPI decryption failed: {exc}") from exc
