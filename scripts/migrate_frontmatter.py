#!/usr/bin/env python3
"""
migrate_frontmatter.py — docs/ 하위 전 노트에 프론트매터를 일괄 추가.

경로 규칙에 따라 type 을 자동 추론하고, 파일명(확장자 제외)을 kebab-case id 로,
첫 H1 을 name 으로 채운다. 이미 프론트매터가 있는 노트는 건드리지 않는다 (idempotent).

사용법:
    python scripts/migrate_frontmatter.py --dry-run
    python scripts/migrate_frontmatter.py --apply

Dataview 대시보드(`docs/dashboards/`) 와 `.ai.md` 는 스킵한다.
"""
from __future__ import annotations

import argparse
import io
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"

FRONTMATTER_RE = re.compile(r"^---\s*\n.*?\n---\s*\n", re.DOTALL)
H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
SLUG_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class Plan:
    path: Path
    note_type: str
    fields: dict

    @property
    def rel(self) -> str:
        try:
            return str(self.path.relative_to(REPO_ROOT)).replace("\\", "/")
        except ValueError:
            return str(self.path)


def has_frontmatter(text: str) -> bool:
    return text.startswith("---\n") and FRONTMATTER_RE.match(text) is not None


def extract_h1(text: str) -> Optional[str]:
    m = H1_RE.search(text)
    if m:
        return m.group(1).strip()
    return None


def to_kebab(stem: str) -> str:
    """파일명 stem 을 kebab-case id 로 변환. 이미 유효하면 그대로 유지."""
    # Ticker 등 대문자/숫자만 있는 경우는 그대로 둔다.
    if re.fullmatch(r"[A-Z0-9]+", stem):
        return stem
    s = stem.strip().lower()
    s = SLUG_RE.sub("-", s).strip("-")
    return s or stem


def rel_parts(path: Path) -> tuple[str, ...]:
    return tuple(p.replace("\\", "/") for p in path.relative_to(DOCS_DIR).parts)


def infer_type(path: Path) -> Optional[str]:
    """경로 기반 type 추론. None 이면 스킵."""
    parts = rel_parts(path)
    if not parts:
        return None
    top = parts[0]
    stem = path.stem

    # Skip Dataview dashboards
    if top == "dashboards":
        return None
    # Skip schemas self-doc
    if top == "schemas":
        return None
    # Skip ontology TTL docs (non-md handled by rglob filter anyway)
    if top == "ontology":
        return None

    if top == "specs":
        if len(parts) >= 3:
            sub = parts[1]
            if sub == "strategies":
                return "strategy"
            if sub == "signals":
                return "signal"
            if sub == "risk-rules":
                return "risk-rule"
            if sub == "instruments":
                return "instrument"
        # Flat docs/specs/*.md
        if len(parts) == 2:
            return "spec-architecture"
        return None

    if top == "runbooks":
        return "runbook"

    if top == "background":
        return "research"

    if top == "whitepaper":
        return "whitepaper"

    if top == "onboarding":
        return "onboarding"

    if top == "work":
        if len(parts) >= 3 and parts[1] == "done" and parts[2] == "backtests":
            return "backtest"
        if len(parts) >= 2 and parts[1] == "incidents":
            if stem.startswith("pm-"):
                return "postmortem"
            return "incident"
        if len(parts) >= 3 and parts[1] == "done":
            return "work-done"
        # docs/work/active/* 는 진행 중 작업 노트 — work-done 동일 처리
        if len(parts) >= 3 and parts[1] == "active":
            return "work-done"
        return None

    return None


# ---------------------------- 타입별 필드 빌더 ----------------------------


def build_strategy(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "strategy",
        "id": id_,
        "name": name,
        "status": "draft",
        "instruments": [],
        "timeframe": "1d",
        "owner": "siwoo",
        "created": "2026-04-14",
    }


def build_signal(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "signal",
        "id": id_,
        "name": name,
        "inputs": [],
        "lookback": 0,
    }


def build_risk_rule(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "risk-rule",
        "id": id_,
        "name": name,
        "severity": "warn",
        "scope": "portfolio",
        "threshold": 0,
        "action": "alert",
    }


def build_instrument(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "instrument",
        "id": id_,
        "name": name,
        "asset_class": "crypto-spot",
        "venue": "binance",
        "tick_size": 0.01,
    }


def build_spec_architecture(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "spec-architecture",
        "id": id_,
        "name": name,
        "owner": "siwoo",
        "status": "draft",
        "tags": [],
    }


def build_runbook(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "runbook",
        "id": id_,
        "name": name,
        "severity": "P2",
    }


def build_research(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "research",
        "id": id_,
        "name": name,
        "sources": [],
    }


def build_onboarding(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "onboarding",
        "id": id_,
        "name": name,
    }


def build_whitepaper(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "whitepaper",
        "id": id_,
        "name": name,
        "version": "0.1",
    }


def build_backtest(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "backtest",
        "id": id_,
        "strategy": "unknown",
        "period": ["2026-01-01", "2026-04-14"],
        "metrics": {"sharpe": 0, "mdd": 0, "trades": 0},
    }


def build_incident(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "incident",
        "id": id_,
        "occurred": "2026-04-14T00:00:00+09:00",
        "severity": "P3",
        "affected_strategies": [],
        "root_cause": name,
    }


def build_postmortem(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "postmortem",
        "id": id_,
        "incident": "unknown",
        "authors": ["siwoo"],
        "status": "draft",
    }


def build_work_done(path: Path, name: str, id_: str) -> dict:
    return {
        "type": "work-done",
        "id": id_,
        "name": name,
        "status": "done",
    }


BUILDERS: dict[str, Callable[[Path, str, str], dict]] = {
    "strategy": build_strategy,
    "signal": build_signal,
    "risk-rule": build_risk_rule,
    "instrument": build_instrument,
    "spec-architecture": build_spec_architecture,
    "runbook": build_runbook,
    "research": build_research,
    "onboarding": build_onboarding,
    "whitepaper": build_whitepaper,
    "backtest": build_backtest,
    "incident": build_incident,
    "postmortem": build_postmortem,
    "work-done": build_work_done,
}


# ---------------------------- YAML 직렬화 (단순화) ----------------------------


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # 날짜·ISO 문자열은 그대로 둔다. 특수문자가 있거나 공백 포함이면 quote.
    if re.fullmatch(r"[A-Za-z0-9_\-\.:/+@]+", s):
        return s
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def _dump_fields(fields: dict) -> str:
    lines: list[str] = []
    for k, v in fields.items():
        if isinstance(v, list):
            if not v:
                lines.append(f"{k}: []")
            else:
                inner = ", ".join(_yaml_scalar(x) for x in v)
                lines.append(f"{k}: [{inner}]")
        elif isinstance(v, dict):
            lines.append(f"{k}:")
            for kk, vv in v.items():
                lines.append(f"  {kk}: {_yaml_scalar(vv)}")
        else:
            lines.append(f"{k}: {_yaml_scalar(v)}")
    return "\n".join(lines)


def render_frontmatter(fields: dict) -> str:
    return "---\n" + _dump_fields(fields) + "\n---\n\n"


# ---------------------------- 메인 파이프라인 ----------------------------


def iter_target_files(docs_dir: Path = DOCS_DIR):
    for md in sorted(docs_dir.rglob("*.md")):
        if md.name == ".ai.md" or md.name.endswith(".ai.md"):
            continue
        if ".obsidian" in md.parts:
            continue
        yield md


def plan_for(path: Path) -> Optional[Plan]:
    text = path.read_text(encoding="utf-8")
    if has_frontmatter(text):
        return None
    note_type = infer_type(path)
    if note_type is None:
        return None
    builder = BUILDERS.get(note_type)
    if builder is None:
        return None
    name = extract_h1(text) or path.stem
    # work-done 은 이슈 폴더 단위이므로 id 를 부모 폴더명 + 파일명으로 고유화
    if note_type == "work-done":
        id_ = f"{path.parent.name}-{to_kebab(path.stem)}"
    else:
        id_ = to_kebab(path.stem)
    fields = builder(path, name, id_)
    return Plan(path=path, note_type=note_type, fields=fields)


def apply_plan(plan: Plan) -> None:
    text = plan.path.read_text(encoding="utf-8")
    if has_frontmatter(text):
        return
    new = render_frontmatter(plan.fields) + text
    plan.path.write_text(new, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="docs/ 프론트매터 일괄 마이그레이션")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="변경 예상만 출력")
    mode.add_argument("--apply", action="store_true", help="파일 수정 적용")
    parser.add_argument("--docs-dir", type=Path, default=DOCS_DIR)
    args = parser.parse_args()

    plans: list[Plan] = []
    skipped_existing = 0
    skipped_unknown = 0

    for md in iter_target_files(args.docs_dir):
        text = md.read_text(encoding="utf-8")
        if has_frontmatter(text):
            skipped_existing += 1
            continue
        p = plan_for(md)
        if p is None:
            skipped_unknown += 1
            continue
        plans.append(p)

    by_type: dict[str, int] = {}
    for p in plans:
        by_type[p.note_type] = by_type.get(p.note_type, 0) + 1

    print(f"[migrate] 대상 후보 {len(plans)} 건")
    for t, n in sorted(by_type.items()):
        print(f"  - {t}: {n}")
    print(f"[migrate] 스킵(이미 프론트매터 있음): {skipped_existing}")
    print(f"[migrate] 스킵(경로 규칙 외 / 대시보드 등): {skipped_unknown}")

    if args.dry_run:
        for p in plans:
            print(f"  + {p.rel}  type={p.note_type}  id={p.fields.get('id')}")
        return 0

    applied = 0
    for p in plans:
        apply_plan(p)
        applied += 1
    print(f"[migrate] 적용 완료: {applied} 파일 수정")
    return 0


if __name__ == "__main__":
    sys.exit(main())
