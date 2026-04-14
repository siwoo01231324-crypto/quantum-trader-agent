"""초안 노트(`.draft.md`) 생성기.

세 가지 이벤트에 대응하는 초안 생성기를 제공한다.
  * `generate_backtest_draft`  — 백테스트 결과 JSON → 백테스트 초안
  * `generate_incident_draft`  — 인시던트 이벤트 dict → 인시던트 초안
  * `generate_postmortem_draft` — incident id → 포스트모템 초안 (백링크 자동 수집)

프론트매터 스키마는 `docs/schemas/note-schemas.md` 를 따른다. 초안 단계이므로
`status` 필드는 항상 `draft` 로 고정되며, 파일명은 `.draft.md` 접미사를 붙여
CI(`scripts/check_invariants.py`) 가 스키마 검증에서 제외할 수 있도록 한다.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .audit import log_run

# 레포 루트: 이 파일 기준 2단계 상위 (services/doc_agent/generators.py → repo)
REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
BACKTEST_DIR = DOCS_DIR / "work" / "done" / "backtests"
INCIDENT_DIR = DOCS_DIR / "work" / "incidents"


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-")
    return slug or "event"


def _today(event_ts: str | None = None) -> str:
    """이벤트 타임스탬프(있으면)에서 날짜 추출, 없으면 오늘."""
    if event_ts:
        # ISO 8601 (YYYY-MM-DD... 또는 YYYY-MM-DDTHH:MM:SS+09:00)
        return event_ts[:10]
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")


def _yaml_scalar(value: Any) -> str:
    """간단한 YAML 직렬화 (문자열/숫자/None/bool)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    text = str(value)
    # 날짜 패턴은 quote 불필요
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}:\d{2}[+\-]\d{2}:\d{2})?", text):
        return text
    # 위험 문자 포함 시 따옴표
    if any(ch in text for ch in ":#[]{},&*!|>'\"%@`") or text.strip() != text:
        escaped = text.replace('"', '\\"')
        return f'"{escaped}"'
    return text


def _yaml_list(values: Iterable[Any]) -> str:
    items = ", ".join(_yaml_scalar(v) for v in values)
    return f"[{items}]"


def _render_frontmatter(fields: dict[str, Any]) -> str:
    """dict → YAML 프론트매터 문자열 (--- 포함)."""
    lines: list[str] = ["---"]
    for key, value in fields.items():
        if value is None:
            lines.append(f"{key}: null")
        elif isinstance(value, dict):
            lines.append(f"{key}:")
            for sub_k, sub_v in value.items():
                if isinstance(sub_v, list):
                    lines.append(f"  {sub_k}: {_yaml_list(sub_v)}")
                else:
                    lines.append(f"  {sub_k}: {_yaml_scalar(sub_v)}")
        elif isinstance(value, list):
            lines.append(f"{key}: {_yaml_list(value)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


def _write_draft(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# 1. Backtest draft
# --------------------------------------------------------------------------- #
def generate_backtest_draft(bt_result_json: dict, *, output_root: Path | None = None) -> Path:
    """백테스트 결과 → `.draft.md` 초안.

    Args:
        bt_result_json: 백테스트 엔진 결과 JSON. 최소 `strategy`, `period`, `metrics` 필수.
        output_root: 테스트·재배치용 루트 경로 (기본 REPO_ROOT).

    Returns:
        생성된 초안 파일 경로.
    """
    strategy = bt_result_json.get("strategy")
    if not strategy:
        raise ValueError("bt_result_json['strategy'] 필수")
    period = bt_result_json.get("period") or []
    metrics = bt_result_json.get("metrics") or {}
    artifacts = bt_result_json.get("artifacts") or []

    # 파일명/ID: bt-{YYYY-MM-DD}-{strategy}
    date = _today()
    note_id = f"bt-{date}-{strategy}"

    # backtest 스키마 필수 필드: type, id, strategy, period, metrics
    fields: dict[str, Any] = {
        "type": "backtest",
        "id": note_id,
        "strategy": strategy,
        "period": list(period) if period else [date, date],
        "metrics": {
            "sharpe": metrics.get("sharpe", 0.0),
            "mdd": metrics.get("mdd", 0.0),
            "trades": metrics.get("trades", 0),
        },
        "status": "draft",
    }
    # 선택 메트릭 병합
    for opt in ("win_rate", "cagr"):
        if opt in metrics:
            fields["metrics"][opt] = metrics[opt]
    if artifacts:
        fields["artifacts"] = list(artifacts)

    summary_lines = [
        "## Summary",
        "",
        f"전략 **{strategy}** 를 기간 `{fields['period'][0]} ~ {fields['period'][-1]}` 동안 백테스트했다.",
        f"Sharpe {fields['metrics']['sharpe']}, MDD {fields['metrics']['mdd']}, 체결 {fields['metrics']['trades']} 건.",
        "",
        "## Notes (draft)",
        "",
        "- [ ] 해석 1~2문단 추가",
        "- [ ] 파라미터 민감도 검토",
        "- [ ] walk-forward 결과와 비교",
        "",
        "## Links",
        "",
        f"- Strategy: [[{strategy}]]",
    ]
    if artifacts:
        summary_lines.append("- Artifacts:")
        summary_lines.extend(f"  - `{a}`" for a in artifacts)

    body = "\n".join(summary_lines)
    content = _render_frontmatter(fields) + "\n\n" + body + "\n"

    root = output_root or REPO_ROOT
    path = root / "docs" / "work" / "done" / "backtests" / f"{note_id}.draft.md"
    _write_draft(path, content)
    log_run("backtest", {"id": note_id, "path": str(path)}, root=root)
    return path


# --------------------------------------------------------------------------- #
# 2. Incident draft
# --------------------------------------------------------------------------- #
def generate_incident_draft(event: dict, *, output_root: Path | None = None) -> Path:
    """인시던트 이벤트 → `.draft.md` 초안.

    Required keys on ``event``:
        occurred, severity, affected_strategies, symptom

    Optional:
        violated_rules, market_context, slug
    """
    occurred = event.get("occurred")
    if not occurred:
        raise ValueError("event['occurred'] 필수 (ISO 8601)")
    severity = event.get("severity") or "P3"
    affected = list(event.get("affected_strategies") or [])
    if not affected:
        raise ValueError("event['affected_strategies'] 필수")
    symptom = event.get("symptom") or "증상 미상"
    market_context = event.get("market_context") or ""
    violated = list(event.get("violated_rules") or [])

    date = _today(occurred)
    slug = event.get("slug") or _slugify(symptom)
    note_id = f"inc-{date}-{slug}"

    # root_cause 1~2줄 템플릿 (LLM 호출 없이 결정적 생성)
    if market_context:
        root_cause = f"{market_context} 에서 {symptom} 발생 (초안 — 추가 분석 필요)."
    else:
        root_cause = f"{symptom} 발생 (초안 — 시장 맥락 조사 필요)."

    fields: dict[str, Any] = {
        "type": "incident",
        "id": note_id,
        "occurred": occurred,
        "severity": severity,
        "affected_strategies": affected,
        "root_cause": root_cause,
        "status": "draft",
    }
    if violated:
        fields["violated_rules"] = violated

    body_lines = [
        "## Symptom",
        "",
        symptom,
        "",
        "## Market Context",
        "",
        market_context or "_미상 — 조사 필요_",
        "",
        "## Affected",
        "",
    ]
    body_lines.extend(f"- Strategy: [[{s}]]" for s in affected)
    if violated:
        body_lines.append("")
        body_lines.extend(f"- Violated rule: [[{r}]]" for r in violated)
    body_lines.extend(
        [
            "",
            "## Root Cause (draft)",
            "",
            root_cause,
            "",
            "## Follow-up",
            "",
            "- [ ] 포스트모템 작성",
            "- [ ] 재현 시나리오 확보",
        ]
    )
    content = _render_frontmatter(fields) + "\n\n" + "\n".join(body_lines) + "\n"

    root = output_root or REPO_ROOT
    path = root / "docs" / "work" / "incidents" / f"{note_id}.draft.md"
    _write_draft(path, content)
    log_run("incident", {"id": note_id, "path": str(path)}, root=root)
    return path


# --------------------------------------------------------------------------- #
# 3. Postmortem draft
# --------------------------------------------------------------------------- #
_FM_BLOCK_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_simple_frontmatter(text: str) -> dict[str, Any]:
    """의존성 없는 소박한 YAML 프론트매터 파서 (scalar / inline list 만 지원)."""
    m = _FM_BLOCK_RE.match(text)
    if not m:
        return {}
    out: dict[str, Any] = {}
    current_key: str | None = None
    for raw in m.group(1).splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("  ") and current_key:
            # 중첩 매핑 — 이번 파서에서는 무시 (상위 key 만 중요)
            continue
        if ":" not in raw:
            continue
        key, _, val = raw.partition(":")
        key = key.strip()
        val = val.strip()
        current_key = key
        if not val:
            out[key] = None
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            items = [s.strip().strip('"').strip("'") for s in inner.split(",") if s.strip()]
            out[key] = items
            continue
        out[key] = val.strip('"').strip("'")
    return out


def _find_incident_note(incident_id: str, root: Path) -> Path | None:
    inc_dir = root / "docs" / "work" / "incidents"
    for suffix in (".md", ".draft.md"):
        candidate = inc_dir / f"{incident_id}{suffix}"
        if candidate.exists():
            return candidate
    return None


def generate_postmortem_draft(incident_id: str, *, output_root: Path | None = None) -> Path:
    """Incident 노트를 읽어 포스트모템 초안을 생성한다.

    - 인시던트 노트의 프론트매터에서 `affected_strategies`, `violated_rules` 를 수집해
      Links 섹션에 백링크로 자동 포함한다.
    - 인시던트 노트를 찾지 못하면 빈 Links 섹션을 가진 초안을 생성한다.
    """
    root = output_root or REPO_ROOT
    inc_path = _find_incident_note(incident_id, root)

    affected: list[str] = []
    violated: list[str] = []
    occurred = ""
    if inc_path is not None:
        fm = _parse_simple_frontmatter(inc_path.read_text(encoding="utf-8"))
        affected = list(fm.get("affected_strategies") or [])
        violated = list(fm.get("violated_rules") or [])
        occurred = str(fm.get("occurred") or "")

    date = _today(occurred) if occurred else _today()
    note_id = f"pm-{date}"

    fields: dict[str, Any] = {
        "type": "postmortem",
        "id": note_id,
        "incident": incident_id,
        "authors": ["doc-agent"],
        "status": "draft",
    }

    body_lines = [
        "## Summary",
        "",
        f"인시던트 `{incident_id}` 에 대한 포스트모템 초안.",
        "",
        "## Timeline",
        "",
        "- [ ] 발단 시각",
        "- [ ] 탐지 시각",
        "- [ ] 대응 시작",
        "- [ ] 복구 완료",
        "",
        "## Root Cause",
        "",
        "_초안 — 작성 필요_",
        "",
        "## Action Items",
        "",
        "- [ ] ",
        "",
        "## Links",
        "",
        f"- Incident: [[{incident_id}]]",
    ]
    if affected:
        body_lines.extend(f"- Strategy: [[{s}]]" for s in affected)
    if violated:
        body_lines.extend(f"- Rule: [[{r}]]" for r in violated)

    content = _render_frontmatter(fields) + "\n\n" + "\n".join(body_lines) + "\n"

    path = root / "docs" / "work" / "incidents" / f"{note_id}.draft.md"
    _write_draft(path, content)
    log_run(
        "postmortem",
        {"id": note_id, "incident": incident_id, "path": str(path)},
        root=root,
    )
    return path
