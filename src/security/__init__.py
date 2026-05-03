"""
Windows DPAPI-based API key store.

Entry points:
  KeyStore         — store/retrieve/rotate/delete encrypted key blobs
  dpapi.encrypt    — raw DPAPI encrypt bytes → bytes
  dpapi.decrypt    — raw DPAPI decrypt bytes → bytes
  rotate_cli       — CLI tool: `python -m src.security.rotate_cli`
"""

from src.security.key_store import KeyStore, KeyNotFoundError, KeyStoreError

__all__ = ["KeyStore", "KeyNotFoundError", "KeyStoreError"]
