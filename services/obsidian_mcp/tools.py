"""Obsidian MCP 서버용 도구 구현체.

MCP SDK 에 의존하지 않는 순수 함수 집합. `server.py` 가 이들을 MCP tool 로 래핑한다.
테스트는 이 파일의 함수를 직접 호출한다.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import yaml  # type: ignore
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore

try:
    import frontmatter  # type: ignore
except ImportError:  # pragma: no cover
    frontmatter = None  # type: ignore


WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\]]+)?\]\]")

DEFAULT_CONFIG = {
    "vault_root": "docs",
    "allowed_paths": [
        "docs/specs/",
        "docs/work/",
    ],
    "write_mode": "dry-run",  # dry-run | enabled
    "sparql_endpoint": None,
}


# ---------------------------------------------------------------------------
# Config / context
# ---------------------------------------------------------------------------


@dataclass
class VaultContext:
    """볼트 루트 + 설정 컨텍스트."""

    vault_root: Path
    allowed_paths: list[str] = field(default_factory=list)
    write_mode: str = "dry-run"  # dry-run | enabled
    sparql_endpoint: str | None = None

    @property
    def writes_enabled(self) -> bool:
        if os.environ.get("OBSIDIAN_MCP_ALLOW_WRITE") == "1":
            return True
        return self.write_mode == "enabled"


def load_config(
    config_path: Path | str | None = None,
    vault_root: Path | str | None = None,
) -> VaultContext:
    """설정 파일 로드. 없으면 기본값 사용.

    우선순위: 인자 > config 파일 > 기본값.
    """
    cfg: dict[str, Any] = dict(DEFAULT_CONFIG)
    if config_path:
        p = Path(config_path)
        if p.exists():
            try:
                cfg.update(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                pass

    root = Path(vault_root) if vault_root else Path(cfg.get("vault_root") or "docs")
    return VaultContext(
        vault_root=root.resolve(),
        allowed_paths=list(cfg.get("allowed_paths") or []),
        write_mode=str(cfg.get("write_mode") or "dry-run"),
        sparql_endpoint=cfg.get("sparql_endpoint"),
    )


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _iter_notes(ctx: VaultContext):
    if not ctx.vault_root.exists():
        return
    for md in sorted(ctx.vault_root.rglob("*.md")):
        if ".obsidian" in md.parts:
            continue
        yield md


def _load_note(path: Path) -> tuple[dict[str, Any], str]:
    if frontmatter is not None:
        post = frontmatter.load(path)
        return dict(post.metadata or {}), post.content or ""
    # 폴백 파서 — --- ... --- 프론트매터를 수동 파싱
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            header = text[3:end].strip("\n")
            body = text[end + 4 :].lstrip("\n")
            meta: dict[str, Any] = {}
            if yaml is not None:
                try:
                    loaded = yaml.safe_load(header) or {}
                    if isinstance(loaded, dict):
                        meta = loaded
                except Exception:
                    meta = {}
            return meta, body
    return {}, text


def _dump_note(frontmatter_dict: dict[str, Any], body: str) -> str:
    if yaml is None:
        header = json.dumps(frontmatter_dict, ensure_ascii=False, indent=2)
    else:
        header = yaml.safe_dump(frontmatter_dict, allow_unicode=True, sort_keys=False).rstrip()
    return f"---\n{header}\n---\n\n{body.lstrip()}"


def _find_note_path(ctx: VaultContext, note_id: str) -> Path | None:
    """id (== 파일명 stem) 로 노트 경로 찾기."""
    for md in _iter_notes(ctx):
        if md.stem == note_id:
            return md
        # 프론트매터 id 매칭도 허용
        try:
            meta, _ = _load_note(md)
        except Exception:
            continue
        if str(meta.get("id") or "") == note_id:
            return md
    return None


def _is_allowed_write(ctx: VaultContext, rel_path: Path | str) -> bool:
    """allowed_paths 화이트리스트 안에 rel_path 가 속하는가?"""
    s = str(rel_path).replace("\\", "/").lstrip("./")
    if not ctx.allowed_paths:
        return True  # 화이트리스트 비어있으면 전부 허용
    for prefix in ctx.allowed_paths:
        p = str(prefix).replace("\\", "/").lstrip("./")
        if s.startswith(p):
            return True
    return False


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------


def read_note(ctx: VaultContext, note_id: str) -> dict[str, Any]:
    """id 로 노트 로드 → {id, path, frontmatter, body}."""
    path = _find_note_path(ctx, note_id)
    if path is None:
        return {"ok": False, "error": f"note not found: {note_id}"}
    meta, body = _load_note(path)
    return {
        "ok": True,
        "id": str(meta.get("id") or path.stem),
        "path": str(path.relative_to(ctx.vault_root)).replace("\\", "/"),
        "frontmatter": meta,
        "body": body,
    }


def list_notes(
    ctx: VaultContext,
    type: str | None = None,
    tag: str | None = None,
    path_prefix: str | None = None,
) -> dict[str, Any]:
    """type / tag / path_prefix 필터링 후 (id, name, type, path) 요약 리스트."""
    items: list[dict[str, Any]] = []
    for md in _iter_notes(ctx):
        rel = str(md.relative_to(ctx.vault_root)).replace("\\", "/")
        if path_prefix and not rel.startswith(path_prefix.lstrip("./")):
            continue
        try:
            meta, _ = _load_note(md)
        except Exception:
            continue
        if type and meta.get("type") != type:
            continue
        if tag:
            tags = meta.get("tags") or []
            if not isinstance(tags, list) or tag not in tags:
                continue
        items.append(
            {
                "id": str(meta.get("id") or md.stem),
                "name": meta.get("name"),
                "type": meta.get("type"),
                "path": rel,
                "tags": meta.get("tags") or [],
            }
        )
    return {"ok": True, "count": len(items), "items": items}


def search(ctx: VaultContext, query: str) -> dict[str, Any]:
    """풀텍스트 + 태그 + 위키링크 매칭. 대소문자 무시."""
    if not query:
        return {"ok": True, "count": 0, "items": []}
    q = query.lower().strip()
    items: list[dict[str, Any]] = []
    for md in _iter_notes(ctx):
        try:
            meta, body = _load_note(md)
        except Exception:
            continue
        hay_parts: list[str] = [md.stem]
        if meta.get("name"):
            hay_parts.append(str(meta["name"]))
        if meta.get("id"):
            hay_parts.append(str(meta["id"]))
        for tag in meta.get("tags") or []:
            hay_parts.append(str(tag))
        hay_parts.append(body)
        haystack = "\n".join(hay_parts).lower()
        if q in haystack:
            wikilinks = [m.group(1) for m in WIKILINK_RE.finditer(body)]
            matched_link = any(q == wl.lower() for wl in wikilinks)
            items.append(
                {
                    "id": str(meta.get("id") or md.stem),
                    "path": str(md.relative_to(ctx.vault_root)).replace("\\", "/"),
                    "type": meta.get("type"),
                    "matched_wikilink": matched_link,
                }
            )
    return {"ok": True, "count": len(items), "items": items}


def write_note(
    ctx: VaultContext,
    note_id: str,
    frontmatter_dict: dict[str, Any],
    body: str,
    create_if_missing: bool = False,
    target_dir: str | None = None,
) -> dict[str, Any]:
    """노트 쓰기. 기본 dry-run — writes_enabled 일 때만 실제 파일 쓴다."""
    if not isinstance(frontmatter_dict, dict):
        return {"ok": False, "error": "frontmatter must be a dict"}

    existing_path = _find_note_path(ctx, note_id)

    if existing_path is None:
        if not create_if_missing:
            return {"ok": False, "error": f"note not found: {note_id} (create_if_missing=False)"}
        # 경로 결정
        if target_dir is None:
            t = str(frontmatter_dict.get("type") or "").strip()
            sub = {
                "strategy": "specs/strategies",
                "signal": "specs/signals",
                "risk-rule": "specs/risk-rules",
                "instrument": "specs/instruments",
                "backtest": "work/done/backtests",
                "incident": "work/incidents",
                "postmortem": "work/incidents",
            }.get(t, "")
            if not sub:
                return {"ok": False, "error": f"cannot infer target_dir for type={t!r}; pass target_dir"}
            target = ctx.vault_root / sub / f"{note_id}.md"
        else:
            target = ctx.vault_root / target_dir / f"{note_id}.md"
    else:
        target = existing_path

    rel = target.relative_to(ctx.vault_root) if target.is_absolute() else target
    if not _is_allowed_write(ctx, rel):
        return {
            "ok": False,
            "error": f"path not in allowed_paths: {rel}",
            "allowed_paths": ctx.allowed_paths,
        }

    # id 일관성 보강
    fm = dict(frontmatter_dict)
    fm.setdefault("id", note_id)

    new_content = _dump_note(fm, body)

    if not ctx.writes_enabled:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(rel).replace("\\", "/"),
            "would_create": existing_path is None,
            "preview": new_content[:2000],
        }

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")
    return {
        "ok": True,
        "dry_run": False,
        "path": str(rel).replace("\\", "/"),
        "created": existing_path is None,
        "bytes": len(new_content.encode("utf-8")),
    }


def append_section(
    ctx: VaultContext,
    note_id: str,
    heading: str,
    content: str,
) -> dict[str, Any]:
    """노트 하단에 `## heading` 섹션 추가. 기본 dry-run."""
    path = _find_note_path(ctx, note_id)
    if path is None:
        return {"ok": False, "error": f"note not found: {note_id}"}

    rel = path.relative_to(ctx.vault_root)
    if not _is_allowed_write(ctx, rel):
        return {"ok": False, "error": f"path not in allowed_paths: {rel}"}

    meta, body = _load_note(path)
    heading = heading.strip().lstrip("#").strip()
    appended = body.rstrip() + f"\n\n## {heading}\n\n{content.strip()}\n"
    new_content = _dump_note(meta, appended)

    if not ctx.writes_enabled:
        return {
            "ok": True,
            "dry_run": True,
            "path": str(rel).replace("\\", "/"),
            "appended_preview": f"## {heading}\n\n{content.strip()}",
        }

    path.write_text(new_content, encoding="utf-8")
    return {
        "ok": True,
        "dry_run": False,
        "path": str(rel).replace("\\", "/"),
        "bytes": len(new_content.encode("utf-8")),
    }


def sparql(ctx: VaultContext, query: str) -> dict[str, Any]:
    """`trading.ttl` + `instances.ttl` 에 대해 SPARQL 쿼리 실행."""
    try:
        from rdflib import Graph  # type: ignore
    except ImportError:
        return {"ok": False, "error": "rdflib not installed"}

    g = Graph()
    loaded: list[str] = []
    for name in ("trading.ttl", "instances.ttl"):
        p = ctx.vault_root / "ontology" / name
        if p.exists():
            try:
                g.parse(str(p), format="turtle")
                loaded.append(name)
            except Exception as e:
                return {"ok": False, "error": f"parse {name} failed: {e}"}
    if not loaded:
        return {"ok": False, "error": "no ontology files found under vault_root/ontology/"}

    try:
        result = g.query(query)
    except Exception as e:
        return {"ok": False, "error": f"sparql error: {e}"}

    rows: list[dict[str, Any]] = []
    bindings_vars = [str(v) for v in (result.vars or [])]
    for row in result:
        if bindings_vars:
            rows.append({v: (str(row[v]) if row[v] is not None else None) for v in bindings_vars})
        else:
            rows.append({"value": str(row)})
    return {"ok": True, "loaded": loaded, "count": len(rows), "rows": rows}


def graph_neighbors(ctx: VaultContext, note_id: str, depth: int = 1) -> dict[str, Any]:
    """위키링크 기반 백링크·아웃링크 탐색."""
    if depth < 1:
        depth = 1
    if depth > 5:
        depth = 5

    # 전체 그래프 1회 빌드
    nodes: dict[str, Path] = {}
    out: dict[str, set[str]] = {}
    for md in _iter_notes(ctx):
        try:
            meta, body = _load_note(md)
        except Exception:
            continue
        nid = str(meta.get("id") or md.stem)
        nodes[nid] = md
        targets: set[str] = set()
        for m in WIKILINK_RE.finditer(body):
            target = m.group(1).strip()
            if "/" in target or target.endswith(".md"):
                continue
            targets.add(target)
        # 프론트매터 참조 필드도 포함
        for key in ("uses_signals", "risk_rules", "instruments", "violated_rules",
                    "affected_strategies", "strategy", "incident", "postmortem",
                    "source_model"):
            val = meta.get(key)
            if isinstance(val, list):
                for v in val:
                    targets.add(str(v))
            elif isinstance(val, str) and val:
                targets.add(val)
        out[nid] = targets

    if note_id not in nodes:
        return {"ok": False, "error": f"note not found: {note_id}"}

    incoming: dict[str, set[str]] = {}
    for src, dests in out.items():
        for d in dests:
            incoming.setdefault(d, set()).add(src)

    visited: set[str] = {note_id}
    frontier: set[str] = {note_id}
    edges: list[dict[str, str]] = []
    for _ in range(depth):
        new_frontier: set[str] = set()
        for n in frontier:
            for tgt in out.get(n, set()):
                edges.append({"from": n, "to": tgt, "direction": "out"})
                if tgt not in visited:
                    new_frontier.add(tgt)
            for src in incoming.get(n, set()):
                edges.append({"from": src, "to": n, "direction": "in"})
                if src not in visited:
                    new_frontier.add(src)
        visited |= new_frontier
        frontier = new_frontier
        if not frontier:
            break

    return {
        "ok": True,
        "root": note_id,
        "depth": depth,
        "nodes": sorted(visited),
        "outlinks": sorted(out.get(note_id, set())),
        "backlinks": sorted(incoming.get(note_id, set())),
        "edges": edges,
    }


__all__ = [
    "VaultContext",
    "load_config",
    "read_note",
    "list_notes",
    "search",
    "write_note",
    "append_section",
    "sparql",
    "graph_neighbors",
]
