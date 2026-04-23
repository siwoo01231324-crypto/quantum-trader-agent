"""`services.doc_agent` 초안 생성기 테스트.

각 생성기는 `output_root` 를 받아 임시 디렉토리로 격리한다. 이로써 레포의
`docs/work/` 에 테스트 아티팩트가 누적되지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from services.doc_agent.generators import (  # noqa: E402
    generate_backtest_draft,
    generate_incident_draft,
    generate_postmortem_draft,
)

FIXTURES = Path(__file__).parent.parent / "fixtures" / "doc_agent"


def _read_fm(path: Path) -> dict:
    """최소 YAML 프론트매터 파서 (테스트 전용)."""
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"{path} must start with frontmatter"
    end = text.index("\n---", 4)
    out: dict = {}
    for raw in text[4:end].splitlines():
        if not raw.strip() or raw.startswith("  "):
            continue
        key, _, val = raw.partition(":")
        out[key.strip()] = val.strip()
    return out


# --- Backtest ----------------------------------------------------------------
def test_backtest_draft_created(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "bt-sample.json").read_text(encoding="utf-8"))
    path = generate_backtest_draft(data, output_root=tmp_path)
    assert path.exists()
    assert path.name.startswith("bt-")
    assert path.name.endswith(".draft.md")


def test_backtest_draft_frontmatter_schema(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "bt-sample.json").read_text(encoding="utf-8"))
    path = generate_backtest_draft(data, output_root=tmp_path)
    fm = _read_fm(path)
    # backtest 스키마 필수 필드
    for required in ("type", "id", "strategy"):
        assert required in fm, required
    assert fm["type"] == "backtest"
    assert fm["status"] == "draft"
    # id 와 파일명(확장자 .draft.md 제거) 일치
    expected_id = path.name.replace(".draft.md", "")
    assert fm["id"] == expected_id


def test_backtest_requires_strategy(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        generate_backtest_draft({"metrics": {}}, output_root=tmp_path)


# --- Incident ----------------------------------------------------------------
def test_incident_draft_created(tmp_path: Path) -> None:
    event = json.loads((FIXTURES / "incident-sample.json").read_text(encoding="utf-8"))
    path = generate_incident_draft(event, output_root=tmp_path)
    assert path.exists()
    assert path.name.startswith("inc-2026-04-12-")
    assert path.name.endswith(".draft.md")


def test_incident_draft_frontmatter_has_root_cause(tmp_path: Path) -> None:
    event = json.loads((FIXTURES / "incident-sample.json").read_text(encoding="utf-8"))
    path = generate_incident_draft(event, output_root=tmp_path)
    fm = _read_fm(path)
    for required in ("type", "id", "occurred", "severity", "root_cause"):
        assert required in fm, required
    assert fm["type"] == "incident"
    # market_context + symptom 으로 root_cause 생성됐는지
    assert "유동성" in fm["root_cause"] or "슬리피지" in fm["root_cause"]


def test_incident_requires_occurred(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        generate_incident_draft(
            {"severity": "P2", "affected_strategies": ["x"], "symptom": "y"},
            output_root=tmp_path,
        )


# --- Postmortem --------------------------------------------------------------
def test_postmortem_draft_with_backlinks(tmp_path: Path) -> None:
    # 선행: 인시던트 초안 생성 (postmortem 이 읽을 대상)
    event = json.loads((FIXTURES / "incident-sample.json").read_text(encoding="utf-8"))
    inc_path = generate_incident_draft(event, output_root=tmp_path)
    incident_id = inc_path.name.replace(".draft.md", "")

    pm_path = generate_postmortem_draft(incident_id, output_root=tmp_path)
    assert pm_path.exists()
    assert pm_path.name.startswith("pm-")
    body = pm_path.read_text(encoding="utf-8")
    # 백링크에 incident id 포함
    assert f"[[{incident_id}]]" in body
    # affected_strategies 백링크 수집 확인
    assert "[[momo-btc-v2]]" in body
    fm = _read_fm(pm_path)
    assert fm["type"] == "postmortem"
    assert fm["status"] == "draft"


def test_postmortem_without_incident_note_still_generates(tmp_path: Path) -> None:
    """인시던트 노트가 없어도 (외부 trigger) 최소 초안이 생성된다."""
    path = generate_postmortem_draft("inc-2026-04-13-unknown", output_root=tmp_path)
    assert path.exists()
    body = path.read_text(encoding="utf-8")
    assert "[[inc-2026-04-13-unknown]]" in body


def test_audit_log_written(tmp_path: Path) -> None:
    data = json.loads((FIXTURES / "bt-sample.json").read_text(encoding="utf-8"))
    generate_backtest_draft(data, output_root=tmp_path)
    logs = list((tmp_path / "docs" / "work" / "agent-runs").glob("*-backtest.log"))
    assert logs, "감사 로그가 기록되어야 한다"
