"""CLI 엔트리: `python -m services.doc_agent.cli <cmd> <args>`.

Commands:
    backtest <json_path>     백테스트 결과 JSON → 초안
    incident <json_path>     인시던트 이벤트 JSON → 초안
    postmortem <incident_id> incident id → 포스트모템 초안

Flags:
    --with-llm               anthropic SDK 호출 활성화 (없으면 템플릿 폴백)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .generators import (
    generate_backtest_draft,
    generate_incident_draft,
    generate_postmortem_draft,
)


def _load_json(path_str: str) -> dict:
    path = Path(path_str)
    if not path.exists():
        print(f"[doc_agent] 입력 파일 없음: {path}", file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="doc_agent", description="자동 초안 노트 생성기")
    parser.add_argument("--with-llm", action="store_true", help="anthropic SDK 호출 활성화")
    sub = parser.add_subparsers(dest="cmd", required=True)

    bt = sub.add_parser("backtest", help="백테스트 결과 JSON → 초안")
    bt.add_argument("json_path")

    inc = sub.add_parser("incident", help="인시던트 이벤트 JSON → 초안")
    inc.add_argument("json_path")

    pm = sub.add_parser("postmortem", help="incident id → 포스트모템 초안")
    pm.add_argument("incident_id")

    args = parser.parse_args(argv)

    if args.cmd == "backtest":
        path = generate_backtest_draft(_load_json(args.json_path))
    elif args.cmd == "incident":
        path = generate_incident_draft(_load_json(args.json_path))
    elif args.cmd == "postmortem":
        path = generate_postmortem_draft(args.incident_id)
    else:  # pragma: no cover — argparse enforces
        parser.print_help()
        return 2

    print(f"[doc_agent] 생성됨: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
