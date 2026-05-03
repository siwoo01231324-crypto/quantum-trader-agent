"""Unit tests for scripts/update_progress_table.py (#139)."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import update_progress_table as upt  # noqa: E402


SAMPLE_ISSUES = [
    {"number": 119, "title": "research: 월 10% 가능성", "state": "CLOSED",
     "labels": [{"name": "research"}]},
    {"number": 145, "title": "ICT 시그널 카탈로그", "state": "OPEN",
     "labels": []},
    {"number": 108, "title": "asyncio.Lock 전환", "state": "CLOSED",
     "labels": [{"name": "chore"}]},
]


def test_build_table_sorts_descending_by_number():
    table = upt.build_table(SAMPLE_ISSUES)
    # Skip header row "| # | 상태 |..." — only data rows have "#<digit>"
    import re as _re
    numbers = [int(m.group(1)) for m in _re.finditer(r"\| #(\d+) \|", table)]
    assert numbers == sorted(numbers, reverse=True)


def test_build_table_states_emoji():
    table = upt.build_table(SAMPLE_ISSUES)
    # CLOSED -> ✅, OPEN -> 🔄
    assert "✅" in table
    assert "🔄" in table


def test_build_table_progress_summary():
    table = upt.build_table(SAMPLE_ISSUES)
    assert "2/3" in table
    assert "66.7%" in table


def test_build_table_pipe_escaping():
    issues = [{"number": 1, "title": "a | b", "state": "OPEN", "labels": []}]
    table = upt.build_table(issues)
    assert "a \\| b" in table


def test_replace_section_replaces_between_sentinels():
    content = (
        "header\n"
        f"{upt.SENTINEL_START}\n"
        "OLD TABLE\n"
        f"{upt.SENTINEL_END}\n"
        "footer\n"
    )
    new = upt.replace_section(content, "NEW TABLE")
    assert "OLD TABLE" not in new
    assert "NEW TABLE" in new
    assert "header" in new and "footer" in new


def test_replace_section_raises_when_sentinels_missing():
    with pytest.raises(ValueError, match="Sentinels not found"):
        upt.replace_section("no markers here", "table")


def test_main_dry_run_no_write(tmp_path):
    wp = tmp_path / "wp.md"
    wp.write_text(
        f"x\n{upt.SENTINEL_START}\nOLD\n{upt.SENTINEL_END}\ny\n",
        encoding="utf-8",
    )
    with patch.object(upt, "fetch_issues", return_value=SAMPLE_ISSUES):
        rc = upt.main(["--dry-run", "--whitepaper", str(wp)])
    assert rc == 0
    assert "OLD" in wp.read_text(encoding="utf-8")  # not modified


def test_main_writes_when_not_dry_run(tmp_path):
    wp = tmp_path / "wp.md"
    wp.write_text(
        f"x\n{upt.SENTINEL_START}\nOLD\n{upt.SENTINEL_END}\ny\n",
        encoding="utf-8",
    )
    with patch.object(upt, "fetch_issues", return_value=SAMPLE_ISSUES):
        rc = upt.main(["--whitepaper", str(wp)])
    assert rc == 0
    out = wp.read_text(encoding="utf-8")
    assert "OLD" not in out
    assert "#119" in out
