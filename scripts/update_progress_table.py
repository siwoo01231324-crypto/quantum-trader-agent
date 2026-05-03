#!/usr/bin/env python3
"""백서 §11-3 진척도 표 자동 갱신 (#139).

`gh issue list --state all` 결과를 Markdown 표로 변환하고, 백서 sentinel
마커 사이 영역을 교체한다.

Usage:
    python scripts/update_progress_table.py [--dry-run] [--whitepaper PATH]

Sentinel markers in whitepaper:
    <!-- progress-table:start -->
    ...auto-generated...
    <!-- progress-table:end -->
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WHITEPAPER = REPO_ROOT / "docs" / "whitepaper" / "qta-master-plan-v01.md"
SENTINEL_START = "<!-- progress-table:start -->"
SENTINEL_END = "<!-- progress-table:end -->"


def fetch_issues() -> list[dict[str, Any]]:
    """Call `gh issue list --state all --limit 200` and parse JSON."""
    result = subprocess.run(
        [
            "gh", "issue", "list",
            "--state", "all",
            "--limit", "200",
            "--json", "number,title,state,labels",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(result.stdout)


def build_table(issues: list[dict[str, Any]]) -> str:
    """Build Markdown table from issue list. Sorted by number desc."""
    issues_sorted = sorted(issues, key=lambda i: i["number"], reverse=True)
    lines = [
        "| # | 상태 | 라벨 | 제목 |",
        "|---|------|------|------|",
    ]
    for issue in issues_sorted:
        state = "✅" if issue["state"] == "CLOSED" else "🔄"
        labels = ",".join(l["name"] for l in issue.get("labels", []))
        title = issue["title"].replace("|", "\\|")
        lines.append(f"| #{issue['number']} | {state} | {labels} | {title} |")

    closed = sum(1 for i in issues if i["state"] == "CLOSED")
    total = len(issues)
    pct = (closed / total * 100) if total else 0.0
    lines.append("")
    lines.append(f"**진척도: {closed}/{total} 완료 ({pct:.1f}%)**")
    return "\n".join(lines)


def replace_section(content: str, new_table: str) -> str:
    """Replace text between sentinels. If sentinels missing, raise."""
    pattern = re.compile(
        re.escape(SENTINEL_START) + r".*?" + re.escape(SENTINEL_END),
        re.DOTALL,
    )
    if not pattern.search(content):
        raise ValueError(
            f"Sentinels not found. Add\n  {SENTINEL_START}\n  ...\n  {SENTINEL_END}\n"
            "to the whitepaper at the §11-3 location."
        )
    replacement = f"{SENTINEL_START}\n{new_table}\n{SENTINEL_END}"
    return pattern.sub(replacement, content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Update whitepaper progress table")
    parser.add_argument("--dry-run", action="store_true", help="Print diff only")
    parser.add_argument("--whitepaper", default=str(DEFAULT_WHITEPAPER))
    args = parser.parse_args(argv)

    whitepaper = Path(args.whitepaper)
    if not whitepaper.exists():
        print(f"ERROR: whitepaper not found at {whitepaper}", file=sys.stderr)
        return 1

    issues = fetch_issues()
    table = build_table(issues)

    original = whitepaper.read_text(encoding="utf-8")
    updated = replace_section(original, table)

    if original == updated:
        print("No changes — table already up-to-date.")
        return 0

    if args.dry_run:
        print("--- DRY-RUN — would write the following table ---")
        print(table)
        return 0

    whitepaper.write_text(updated, encoding="utf-8")
    print(f"Updated {whitepaper}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
