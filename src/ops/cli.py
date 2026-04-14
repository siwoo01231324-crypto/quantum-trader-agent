"""Manual kill-switch CLI.

Usage:
  python -m src.ops.cli kill --reason "manual" [--operator NAME]
  python -m src.ops.cli release --operator NAME
  python -m src.ops.cli status

State is persisted in a small JSON file so multiple processes share view.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

DEFAULT_STATE_PATH = Path(os.environ.get("KILL_STATE_PATH", ".ops/kill_state.json"))


def _load(path: Path) -> dict:
    if not path.exists():
        return {"tripped": False, "events": []}
    return json.loads(path.read_text(encoding="utf-8"))


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def cmd_kill(args: argparse.Namespace) -> int:
    state = _load(args.state)
    state["tripped"] = True
    state.setdefault("events", []).append({
        "ts": time.time(),
        "type": "kill",
        "reason": args.reason,
        "operator": args.operator,
    })
    _save(args.state, state)
    print(f"KILL switch TRIPPED by {args.operator}: {args.reason}")
    return 0


def cmd_release(args: argparse.Namespace) -> int:
    state = _load(args.state)
    if not state.get("tripped"):
        print("kill switch already released")
        return 0
    state["tripped"] = False
    state.setdefault("events", []).append({
        "ts": time.time(),
        "type": "release",
        "operator": args.operator,
    })
    _save(args.state, state)
    print(f"KILL switch RELEASED by {args.operator}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state = _load(args.state)
    print(json.dumps(state, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="kill-switch")
    p.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    sub = p.add_subparsers(dest="cmd", required=True)

    k = sub.add_parser("kill")
    k.add_argument("--reason", required=True)
    k.add_argument("--operator", default=os.environ.get("USER", "unknown"))
    k.set_defaults(func=cmd_kill)

    r = sub.add_parser("release")
    r.add_argument("--operator", default=os.environ.get("USER", "unknown"))
    r.set_defaults(func=cmd_release)

    s = sub.add_parser("status")
    s.set_defaults(func=cmd_status)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
