"""Patch-notes invariant check (불변식 #8, 2026-05-26 v0.6.0).

새 전략·시그널·리스크 정책·production.yaml 활성화 변경이 PR 에 들어있으면
``docs/patch-notes/index.yaml`` 의 `versions:` 리스트에도 새 entry 가 함께
appended 되어야 한다.

판정:
    - "감시 대상" 파일이 변경됐고
    - 동시에 ``docs/patch-notes/index.yaml`` 이 *변경되지 않았으면*
      → 위반 (exit 1)
    - 단, 감시 대상 변경이 *테스트/문서/주석 only* 일 가능성도 있어
      strict 모드만 차단; warn 모드는 로그만 (CI rollout v1 동안).

호출:
    python scripts/check_patch_notes.py             # warn 모드 (기본)
    python scripts/check_patch_notes.py --strict    # 위반 시 exit 1
    BASE_REF=origin/master python scripts/check_patch_notes.py
        # CI 에서 PR base ref 지정 (default = origin/master)

GitHub Actions 의 ``pull_request`` 이벤트에서는
``${{ github.event.pull_request.base.sha }}`` 가 BASE_REF 대신 BASE_SHA 로
전달돼도 동작 (둘 다 fallback). 로컬에선 ``git merge-base origin/master HEAD``
로 자동 추정.

스크립트는 자기 자신의 변경에 트리거되지 않는다 (skip self).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Windows cp949 console 에서도 한글·✓·❌ 가 깨지지 않게 stdout/stderr 강제 UTF-8.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # Py3.7+ TextIOWrapper
    except Exception:
        pass

# 감시 대상 — 이 prefix 로 시작하는 파일이 변경되면 patch-notes 갱신 필요.
WATCHED_PREFIXES: tuple[str, ...] = (
    "src/backtest/strategies/",
    "configs/orchestrator/production.yaml",
    "src/risk/",
    "src/portfolio/",
    "src/live/",
)

# 변경돼도 patch-notes 갱신 불요인 파일 (테스트, .ai.md, comment-only).
EXEMPT_SUFFIXES: tuple[str, ...] = (
    ".ai.md",
    "/__init__.py",
)
EXEMPT_PATH_PARTS: tuple[str, ...] = (
    "/tests/",
    "/test_",
)

PATCH_NOTES_PATH = "docs/patch-notes/index.yaml"
SELF_PATH = "scripts/check_patch_notes.py"
SELF_WORKFLOW = ".github/workflows/patch-notes-check.yml"


def _resolve_base_ref() -> str:
    """우선순위: env BASE_REF → env BASE_SHA → origin/master."""
    env_ref = os.environ.get("BASE_REF") or os.environ.get("BASE_SHA")
    if env_ref:
        return env_ref
    return "origin/master"


def _changed_files(base_ref: str) -> list[str]:
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            capture_output=True, text=True, check=True, encoding="utf-8",
        )
    except subprocess.CalledProcessError as exc:
        print(f"❌ git diff 실패: {exc.stderr.strip()}", file=sys.stderr)
        sys.exit(2)
    return [line.strip() for line in out.stdout.splitlines() if line.strip()]


def _is_watched(path: str) -> bool:
    if not any(path.startswith(p) for p in WATCHED_PREFIXES):
        return False
    if any(path.endswith(s) for s in EXEMPT_SUFFIXES):
        return False
    if any(part in path for part in EXEMPT_PATH_PARTS):
        return False
    return True


def _is_self_change_only(changed: list[str]) -> bool:
    """본 스크립트 또는 워크플로우만 바뀌었으면 patch-notes 강제 안 함."""
    return all(p in {SELF_PATH, SELF_WORKFLOW} for p in changed)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument(
        "--strict", action="store_true",
        help="위반 시 exit 1 (기본 warn, exit 0)",
    )
    args = ap.parse_args()

    base_ref = _resolve_base_ref()
    changed = _changed_files(base_ref)
    if not changed:
        print(f"✓ {base_ref}...HEAD 변경 파일 없음 — skip")
        return 0

    if _is_self_change_only(changed):
        print("✓ check_patch_notes 자체 변경만 — skip")
        return 0

    watched = [p for p in changed if _is_watched(p)]
    if not watched:
        print(f"✓ {len(changed)} 변경 중 감시 대상 없음 — skip")
        return 0

    patch_notes_updated = PATCH_NOTES_PATH in changed
    if patch_notes_updated:
        print(
            f"✓ patch-notes 갱신됨 ({PATCH_NOTES_PATH}) — "
            f"{len(watched)} 감시 대상 변경 동반"
        )
        return 0

    # 위반
    print(
        f"❌ 불변식 #8 위반: 감시 대상 {len(watched)} 파일 변경됐는데 "
        f"{PATCH_NOTES_PATH} 갱신 없음.\n"
        f"   감시 대상 변경:",
        file=sys.stderr,
    )
    for p in watched:
        print(f"     - {p}", file=sys.stderr)
    print(
        f"\n   조치: {PATCH_NOTES_PATH} 의 `versions:` 리스트 맨 앞에 "
        f"새 entry 를 append 한 뒤 같은 PR 에 포함하세요.\n"
        f"   스키마: 파일 상단 주석 참조 (version/date/title/tags/summary/items/refs).",
        file=sys.stderr,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
