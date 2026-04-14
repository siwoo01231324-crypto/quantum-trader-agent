"""Obsidian MCP stdio 서버 엔트리.

실행:
  python -m services.obsidian_mcp.server            # dry-run 쓰기
  python -m services.obsidian_mcp.server --write    # 실쓰기 활성화
  OBSIDIAN_MCP_ALLOW_WRITE=1 python -m services.obsidian_mcp.server

MCP SDK (`mcp` 패키지) 가 없으면 경고를 출력하고 skeleton 모드로 종료한다.
도구 로직은 `services.obsidian_mcp.tools` 에 모두 구현되어 있으므로, SDK 유무와
무관하게 단위테스트 가능하다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import tools as t

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "docs" / ".obsidian" / "mcp-config.json"


def _build_ctx(args: argparse.Namespace) -> t.VaultContext:
    cfg_path = Path(args.config) if args.config else DEFAULT_CONFIG_PATH
    ctx = t.load_config(cfg_path if cfg_path.exists() else None, vault_root=args.vault_root)
    if args.write:
        os.environ["OBSIDIAN_MCP_ALLOW_WRITE"] = "1"
        ctx.write_mode = "enabled"
    return ctx


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Obsidian Vault MCP stdio server")
    p.add_argument("--config", help="mcp-config.json 경로 (기본: docs/.obsidian/mcp-config.json)")
    p.add_argument("--vault-root", help="볼트 루트 (기본: config.vault_root or 'docs')")
    p.add_argument("--write", action="store_true", help="실쓰기 활성화 (기본은 dry-run)")
    p.add_argument("--selftest", action="store_true", help="SDK 없이 도구 호출만 점검")
    return p.parse_args(argv)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "read_note",
        "description": "id 로 Obsidian 노트를 읽어 frontmatter(dict) + body(str) 반환",
        "inputSchema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
        },
    },
    {
        "name": "list_notes",
        "description": "type / tag / path_prefix 필터로 노트 요약 리스트",
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {"type": "string"},
                "tag": {"type": "string"},
                "path_prefix": {"type": "string"},
            },
        },
    },
    {
        "name": "search",
        "description": "풀텍스트·태그·위키링크 매칭 검색",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "write_note",
        "description": "노트 쓰기 (기본 dry-run). create_if_missing 이면 신규 생성.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "frontmatter": {"type": "object"},
                "body": {"type": "string"},
                "create_if_missing": {"type": "boolean", "default": False},
                "target_dir": {"type": "string"},
            },
            "required": ["id", "frontmatter", "body"],
        },
    },
    {
        "name": "append_section",
        "description": "기존 노트에 '## heading' 섹션 추가 (기본 dry-run)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "heading": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["id", "heading", "content"],
        },
    },
    {
        "name": "sparql",
        "description": "docs/ontology/{trading,instances}.ttl 에 대해 SPARQL 쿼리",
        "inputSchema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "graph_neighbors",
        "description": "위키링크·프론트매터 참조 기반 이웃 노드 탐색",
        "inputSchema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "depth": {"type": "integer", "default": 1, "minimum": 1, "maximum": 5},
            },
            "required": ["id"],
        },
    },
]


def dispatch(ctx: t.VaultContext, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """tool 이름 → 함수 라우팅. MCP SDK 없이도 호출 가능한 동기 디스패처."""
    args = args or {}
    if name == "read_note":
        return t.read_note(ctx, args["id"])
    if name == "list_notes":
        return t.list_notes(ctx, args.get("type"), args.get("tag"), args.get("path_prefix"))
    if name == "search":
        return t.search(ctx, args.get("query", ""))
    if name == "write_note":
        return t.write_note(
            ctx,
            args["id"],
            args.get("frontmatter") or {},
            args.get("body") or "",
            create_if_missing=bool(args.get("create_if_missing")),
            target_dir=args.get("target_dir"),
        )
    if name == "append_section":
        return t.append_section(ctx, args["id"], args["heading"], args["content"])
    if name == "sparql":
        return t.sparql(ctx, args["query"])
    if name == "graph_neighbors":
        return t.graph_neighbors(ctx, args["id"], int(args.get("depth", 1)))
    return {"ok": False, "error": f"unknown tool: {name}"}


# ---------------------------------------------------------------------------
# MCP SDK wiring (optional)
# ---------------------------------------------------------------------------


async def _run_mcp_server(ctx: t.VaultContext) -> int:
    try:
        from mcp.server import Server  # type: ignore
        from mcp.server.stdio import stdio_server  # type: ignore
        import mcp.types as types  # type: ignore
    except ImportError:
        print(
            "[obsidian_mcp] 'mcp' SDK 미설치. 설치: pip install mcp\n"
            "             (SDK 없이 tool 로직만 쓰려면 --selftest 또는 Python import 사용)",
            file=sys.stderr,
        )
        return 2

    server: Any = Server("obsidian-vault")

    @server.list_tools()
    async def _list_tools():  # pragma: no cover — MCP SDK 경로
        return [
            types.Tool(
                name=d["name"],
                description=d["description"],
                inputSchema=d["inputSchema"],
            )
            for d in TOOL_DEFS
        ]

    @server.call_tool()
    async def _call_tool(name: str, arguments: dict[str, Any] | None):  # pragma: no cover
        result = dispatch(ctx, name, arguments or {})
        return [types.TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))]

    async with stdio_server() as (read_stream, write_stream):  # pragma: no cover
        await server.run(read_stream, write_stream, server.create_initialization_options())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    ctx = _build_ctx(args)

    if args.selftest:
        print(f"[obsidian_mcp] vault_root={ctx.vault_root}")
        print(f"[obsidian_mcp] writes_enabled={ctx.writes_enabled}")
        print(f"[obsidian_mcp] tools={[d['name'] for d in TOOL_DEFS]}")
        sample = dispatch(ctx, "list_notes", {})
        print(f"[obsidian_mcp] list_notes -> count={sample.get('count')}")
        return 0

    try:
        return asyncio.run(_run_mcp_server(ctx))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
