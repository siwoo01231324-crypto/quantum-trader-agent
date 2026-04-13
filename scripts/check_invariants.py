#!/usr/bin/env python3
"""
아키텍처 불변식 검증 스크립트
위반 시 수정 방법을 메시지에 직접 포함 — Claude가 스스로 수정할 수 있도록

사용법:
  python scripts/check_invariants.py
  python scripts/check_invariants.py --check all
  python scripts/check_invariants.py --check <check_name>

---
⚠️  이 파일은 프로젝트 불변식에 맞게 직접 수정해야 합니다.

## 커스텀 방법

1. CLAUDE.md의 '아키텍처 불변식' 섹션에 불변식을 정의한다
2. 아래 예시를 참고해 불변식 검사 함수를 작성한다
3. main()에 check 호출을 추가한다

## 예시 패턴

### 특정 패턴이 특정 디렉토리 밖에 있으면 위반
```python
PATTERNS = ["import forbidden_module", "from forbidden_module"]
ALLOWED_DIR = "src/allowed_location"

def check_example():
    violations = []
    for py_file in Path(".").rglob("*.py"):
        if ALLOWED_DIR in str(py_file):
            continue
        content = py_file.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            if pattern in content:
                violations.append((py_file, pattern))
    return violations
```

### TypeScript 파일에서 특정 import 금지
```python
FORBIDDEN_TS_IMPORTS = ["from 'forbidden-package'"]

def check_ts_forbidden_imports():
    violations = []
    for ts_file in Path("src").rglob("*.ts"):
        if "node_modules" in str(ts_file):
            continue
        content = ts_file.read_text(encoding="utf-8", errors="ignore")
        for pattern in FORBIDDEN_TS_IMPORTS:
            if pattern in content:
                violations.append((ts_file, pattern))
    return violations
```
---
"""

import sys
import argparse
from pathlib import Path

# ============================================================
# 여기에 프로젝트 불변식 검사 로직을 추가하세요
# ============================================================

# 예시: 검사 제외 경로
EXCLUDED_PATHS = [
    ".worktree/",
    ".claude/",
    "scripts/",
    "node_modules/",
]


def _is_excluded(path_str: str) -> bool:
    return any(excl in path_str for excl in EXCLUDED_PATHS)


# TODO: 프로젝트 불변식에 맞는 check_*() 함수를 작성하세요
# def check_my_invariant() -> list[tuple]:
#     violations = []
#     ...
#     return violations


def main():
    parser = argparse.ArgumentParser(description="아키텍처 불변식 검증")
    parser.add_argument(
        "--check",
        choices=["all"],  # 커스텀 check 이름을 여기에 추가
        default="all",
    )
    args = parser.parse_args()

    exit_code = 0

    # TODO: check 함수 호출을 여기에 추가
    # if args.check in ("my_invariant", "all"):
    #     violations = check_my_invariant()
    #     if violations:
    #         print("\n❌ [불변식 위반] 설명")
    #         print("   수정 방법: ...")
    #         for file, pattern in violations:
    #             print(f"   - {file}  (패턴: {pattern!r})")
    #         exit_code = 1

    if exit_code == 0:
        print("✅ 불변식 검증 통과")

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
