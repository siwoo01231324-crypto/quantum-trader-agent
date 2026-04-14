"""Obsidian 볼트를 MCP stdio 서버로 노출하는 서비스 패키지.

외부 LLM (Claude Code 등) 이 `docs/` 하위 Obsidian 볼트를 도구(MCP tool) 로
직접 읽고 쓸 수 있도록 tool 7종을 제공한다.

tool 목록:
  - read_note, list_notes, search
  - write_note, append_section (기본 dry-run)
  - sparql, graph_neighbors
"""

from __future__ import annotations

from . import tools  # noqa: F401  (편의를 위해 재노출)

__all__ = ["tools"]
