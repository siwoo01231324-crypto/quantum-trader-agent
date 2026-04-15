#!/usr/bin/env python3
"""
아키텍처 불변식 검증 스크립트

v1 단계: 기본적으로 warn 모드 (exit 0). `--strict` 지정 시 위반이 있으면 exit 1.

검사 항목:
  1. 프론트매터 스키마 — 필수 필드 누락 검증 (7개 타입)
  2. id 필드와 파일명 일치
  3. 본문의 [[id]] 위키링크 대상 존재 여부
  4. docs/ontology/trading.ttl rdflib 파싱 검증
  5. SHACL 제약 검증 — docs/ontology/shapes.ttl + instances.ttl (pyshacl)

사용법:
  python scripts/check_invariants.py              # warn 모드
  python scripts/check_invariants.py --strict     # fail 모드
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path
from typing import Any

# Windows cp949 환경에서도 유니코드 출력 허용
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
TRADING_TTL = DOCS_DIR / "ontology" / "trading.ttl"
SHAPES_TTL = DOCS_DIR / "ontology" / "shapes.ttl"
INSTANCES_TTL = DOCS_DIR / "ontology" / "instances.ttl"

REQUIRED_FIELDS: dict[str, list[str]] = {
    "strategy": ["type", "id", "name", "status", "instruments", "timeframe", "owner", "created"],
    "signal": ["type", "id", "name", "inputs", "lookback"],
    "risk-rule": ["type", "id", "name", "severity", "scope", "threshold", "action"],
    "instrument": ["type", "id", "name", "asset_class", "venue", "tick_size"],
    "backtest": ["type", "id", "strategy", "period", "metrics"],
    "incident": ["type", "id", "occurred", "severity", "affected_strategies", "root_cause"],
    "postmortem": ["type", "id", "incident", "authors", "status"],
    "spec-architecture": ["type", "id", "name", "owner", "status"],
    "runbook": ["type", "id", "name", "severity"],
    "research": ["type", "id", "name", "sources"],
    "onboarding": ["type", "id", "name"],
    "whitepaper": ["type", "id", "name", "version"],
    "work-done": ["type", "id", "name", "status"],
}

# 필수 필드 중 "비어 있어도 허용" 하는 필드 (빈 리스트 등).
EMPTY_OK_FIELDS = {"instruments", "inputs", "sources", "tags", "affected_strategies"}

# threshold 처럼 0 이 정상 값인 필드는 None 만 금지.
ZERO_OK_FIELDS = {"threshold", "lookback", "tick_size"}

WIKILINK_RE = re.compile(r"\[\[([^\[\]|#]+?)(?:\|[^\]]+)?\]\]")
INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
CODE_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)


def _load_frontmatter():
    try:
        import frontmatter  # type: ignore
        return frontmatter
    except ImportError:
        return None


def _iter_md_with_fm(frontmatter_mod) -> list[tuple[Path, dict, str]]:
    result = []
    if not DOCS_DIR.exists():
        return result
    for md in sorted(DOCS_DIR.rglob("*.md")):
        if ".obsidian" in md.parts:
            continue
        # `.draft.md` 는 LLM 에이전트가 생성한 초안 — 스키마 검증에서 제외.
        # (파일명이 `.draft.md` 로 끝나는 경우)
        if md.name.endswith(".draft.md"):
            continue
        try:
            post = frontmatter_mod.load(md)
        except Exception:
            continue
        fm = post.metadata or {}
        if fm.get("type"):
            result.append((md, fm, post.content))
    return result


def _collect_drafts() -> list[Path]:
    if not DOCS_DIR.exists():
        return []
    return [p for p in DOCS_DIR.rglob("*.draft.md") if ".obsidian" not in p.parts]


def check_drafts_on_main() -> list[str]:
    """`.draft.md` 가 main 브랜치 작업 트리에 남아있으면 경고."""
    import os
    import subprocess

    drafts = _collect_drafts()
    if not drafts:
        return []

    branch = os.environ.get("GITHUB_REF_NAME") or os.environ.get("CI_BRANCH")
    if not branch:
        try:
            out = subprocess.check_output(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(REPO_ROOT),
                stderr=subprocess.DEVNULL,
            )
            branch = out.decode("utf-8").strip()
        except Exception:
            branch = ""

    if branch not in {"main", "master"}:
        return []
    return [
        f"[draft] {p.relative_to(REPO_ROOT)}: .draft.md 가 {branch} 에 남아있음 (초안은 리뷰 후 .md 로 승격)"
        for p in drafts
    ]


def check_frontmatter_schema(notes) -> list[str]:
    warnings: list[str] = []
    for path, fm, _body in notes:
        t = fm.get("type")
        if t not in REQUIRED_FIELDS:
            warnings.append(f"[schema] {path}: 알 수 없는 type={t!r}")
            continue
        for required in REQUIRED_FIELDS[t]:
            if required not in fm or fm.get(required) in (None, ""):
                warnings.append(f"[schema] {path}: 필수 필드 누락 '{required}' (type={t})")
    return warnings


def check_id_filename_match(notes) -> list[str]:
    warnings: list[str] = []
    for path, fm, _body in notes:
        # work-done 은 이슈 폴더 내부 파일(00_issue.md 등)이므로 id 가 폴더명 기반 — 파일명 일치 검증 제외
        if fm.get("type") == "work-done":
            continue
        expected = path.stem
        actual = fm.get("id")
        if actual and str(actual) != expected:
            warnings.append(f"[id] {path}: id={actual!r} 가 파일명 {expected!r} 과 불일치")
    return warnings


def _strip_code(body: str) -> str:
    """Inline 코드(`...`) 와 펜스드 코드(```...```) 블록 제거."""
    no_fence = CODE_FENCE_RE.sub("", body)
    return INLINE_CODE_RE.sub("", no_fence)


def check_wikilinks(notes) -> list[str]:
    warnings: list[str] = []
    known_ids = {str(fm.get("id")) for _, fm, _ in notes if fm.get("id")}
    for path, _fm, body in notes:
        clean = _strip_code(body)
        for m in WIKILINK_RE.finditer(clean):
            target = m.group(1).strip()
            if "/" in target or target.endswith(".md"):
                continue  # 경로형 링크는 스킵
            if target not in known_ids:
                warnings.append(f"[wikilink] {path}: [[{target}]] 대상 노트 없음")
    return warnings


def check_shacl() -> list[str]:
    """SHACL 제약 기반 도메인 규칙 검증."""
    warnings: list[str] = []
    if not INSTANCES_TTL.exists():
        warnings.append(f"[shacl] {INSTANCES_TTL} 없음 — ontology_sync --write 먼저 실행 필요")
        return warnings
    if not SHAPES_TTL.exists():
        warnings.append(f"[shacl] {SHAPES_TTL} 없음")
        return warnings
    # scripts/ 는 sys.path 에 보통 들어있지 않으므로 명시적으로 추가.
    scripts_dir = str(Path(__file__).resolve().parent)
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    try:
        from shacl_validate import run_shacl  # type: ignore
    except ImportError as e:
        warnings.append(f"[shacl] shacl_validate 임포트 실패: {e}")
        return warnings
    try:
        violations = run_shacl(
            data_path=INSTANCES_TTL,
            shapes_path=SHAPES_TTL,
            ontology_path=TRADING_TTL if TRADING_TTL.exists() else None,
        )
    except RuntimeError as e:
        warnings.append(f"[shacl] 실행 실패: {e}")
        return warnings
    for v in violations:
        shape = v.source_shape or "UnknownShape"
        focus = v.focus_node
        warnings.append(f"[shacl] {shape} · {focus}: {v.message}")
    return warnings


def check_ttl_parses() -> list[str]:
    warnings: list[str] = []
    if not TRADING_TTL.exists():
        warnings.append(f"[ttl] {TRADING_TTL} 없음")
        return warnings
    try:
        from rdflib import Graph  # type: ignore
    except ImportError:
        warnings.append("[ttl] rdflib 미설치 — 파싱 검증 스킵")
        return warnings
    try:
        g = Graph()
        g.parse(str(TRADING_TTL), format="turtle")
        if len(g) == 0:
            warnings.append(f"[ttl] {TRADING_TTL}: 트리플 0 개")
    except Exception as e:
        warnings.append(f"[ttl] {TRADING_TTL} 파싱 실패: {e}")
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="아키텍처 불변식 검증")
    parser.add_argument("--strict", action="store_true", help="위반 시 exit 1")
    args = parser.parse_args()

    fm_mod = _load_frontmatter()
    all_warnings: list[str] = []

    if fm_mod is None:
        all_warnings.append("[deps] python-frontmatter 미설치 — 프론트매터 검증 스킵 (pip install python-frontmatter)")
        notes = []
    else:
        notes = _iter_md_with_fm(fm_mod)
        all_warnings += check_frontmatter_schema(notes)
        all_warnings += check_id_filename_match(notes)
        all_warnings += check_wikilinks(notes)

    all_warnings += check_ttl_parses()
    all_warnings += check_shacl()
    all_warnings += check_drafts_on_main()

    if all_warnings:
        print(f"[check_invariants] {len(all_warnings)} 경고")
        for w in all_warnings:
            print(f"  - {w}")
        if args.strict:
            print("[check_invariants] --strict: FAIL")
            return 1
        print("[check_invariants] warn 모드 — v1 단계이므로 통과 처리")
        return 0

    print(f"[check_invariants] 통과 ({len(notes)} 노트 검증)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
