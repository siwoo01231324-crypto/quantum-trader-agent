"""Key rotation CLI for the DPAPI key store.

Usage:
    python -m src.security.rotate_cli list
    python -m src.security.rotate_cli set   <KEY_NAME>
    python -m src.security.rotate_cli get   <KEY_NAME>
    python -m src.security.rotate_cli rotate <KEY_NAME>
    python -m src.security.rotate_cli delete <KEY_NAME>

The 'set' and 'rotate' commands read the secret from stdin (no echo).
The 'get' command prints the stored value — use only in trusted terminals.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from src.security.key_store import KeyStore, KeyNotFoundError, KeyStoreError
from src.security.dpapi import DecryptionError


def cmd_list(store: KeyStore, _args: argparse.Namespace) -> int:
    keys = store.list_keys()
    if not keys:
        print("(no keys stored)")
        return 0
    for name in keys:
        try:
            m = store.meta(name)
            print(f"  {name}  [updated: {m.get('updated_at', '?')}]")
        except Exception:
            print(f"  {name}")
    return 0


def cmd_set(store: KeyStore, args: argparse.Namespace) -> int:
    name = args.name
    secret = getpass.getpass(f"Enter secret for '{name}': ")
    if not secret:
        print("ERROR: empty secret not allowed", file=sys.stderr)
        return 1
    store.put(name, secret)
    print(f"Stored '{name}'.")
    return 0


def cmd_get(store: KeyStore, args: argparse.Namespace) -> int:
    name = args.name
    try:
        value = store.get(name)
    except KeyNotFoundError:
        print(f"ERROR: key '{name}' not found", file=sys.stderr)
        return 1
    except DecryptionError as exc:
        print(f"ERROR: decryption failed — {exc}", file=sys.stderr)
        return 1
    print(value)
    return 0


def cmd_rotate(store: KeyStore, args: argparse.Namespace) -> int:
    name = args.name
    if name not in store.list_keys():
        print(f"ERROR: key '{name}' not found — use 'set' to create it", file=sys.stderr)
        return 1
    new_secret = getpass.getpass(f"Enter NEW secret for '{name}': ")
    if not new_secret:
        print("ERROR: empty secret not allowed", file=sys.stderr)
        return 1
    store.rotate(name, new_secret)
    print(f"Rotated '{name}'. Old blob deleted.")
    return 0


def cmd_delete(store: KeyStore, args: argparse.Namespace) -> int:
    name = args.name
    store.delete(name)
    print(f"Deleted '{name}'.")
    return 0


_COMMANDS = {
    "list": cmd_list,
    "set": cmd_set,
    "get": cmd_get,
    "rotate": cmd_rotate,
    "delete": cmd_delete,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.security.rotate_cli",
        description="DPAPI key store management CLI",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List stored key names")

    for cmd in ("set", "get", "rotate", "delete"):
        p = sub.add_parser(cmd, help=f"{cmd.capitalize()} a key")
        p.add_argument("name", help="Key name (e.g. BINANCE_API_KEY)")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = KeyStore()
    handler = _COMMANDS[args.command]
    try:
        return handler(store, args)
    except KeyStoreError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
