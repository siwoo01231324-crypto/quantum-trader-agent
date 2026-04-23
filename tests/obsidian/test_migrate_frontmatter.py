"""tests/test_migrate_frontmatter.py

scripts/migrate_frontmatter.py 의 경로 기반 type 추론, 필드 생성, idempotency 검증.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
SCRIPT = ROOT / "scripts" / "migrate_frontmatter.py"


def _load():
    spec = importlib.util.spec_from_file_location("migrate_frontmatter", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["migrate_frontmatter"] = mod
    spec.loader.exec_module(mod)  # type: ignore[attr-defined]
    return mod


@pytest.fixture(scope="module")
def mf():
    return _load()


@pytest.fixture
def fake_docs(tmp_path: Path, mf):
    """임시 docs/ 트리를 만들고 DOCS_DIR 을 패치한다."""
    docs = tmp_path / "docs"
    # 케이스별 파일
    layout = {
        # spec-architecture (플랫)
        "specs/data-lake-schema.md": "# Data Lake Schema\n\n본문.\n",
        # strategy
        "specs/strategies/some-strat.md": "# Some Strat\n",
        # signal
        "specs/signals/my-signal.md": "# My Signal\n",
        # risk-rule
        "specs/risk-rules/rr-5.md": "# RR 5\n",
        # instrument
        "specs/instruments/ETHUSDT.md": "# ETH Perpetual\n",
        # runbook
        "runbooks/kill-switch-runbook.md": "# Kill Switch Runbook\n",
        # research
        "background/07-microstructure.md": "# Microstructure\n",
        # onboarding
        "onboarding/getting-started.md": "# Getting Started\n",
        # whitepaper
        "whitepaper/qta-v01.md": "# QTA v01\n",
        # backtest
        "work/done/backtests/bt-2026-04-10-some-strat.md": "# BT\n",
        # incident vs postmortem by filename prefix
        "work/incidents/inc-2026-04-12-x.md": "# Incident X\n",
        "work/incidents/pm-2026-04-12.md": "# PM\n",
        # work-done issue folder
        "work/done/000001-foo/00_issue.md": "# Issue Foo\n",
        # dashboard - SKIP
        "dashboards/live.md": "# Live Dashboard\n",
        # schemas - SKIP
        "schemas/note-schemas.md": "# Schemas\n",
        # .ai.md - SKIP
        "specs/.ai.md": "# specs dir\n",
        # already has frontmatter - SKIP
        "specs/strategies/already.md": "---\ntype: strategy\nid: already\n---\n\n# Already\n",
    }
    for rel, body in layout.items():
        p = docs / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    # DOCS_DIR 패치
    mf.DOCS_DIR = docs
    return docs


def test_infer_type_paths(fake_docs, mf):
    cases = {
        "specs/data-lake-schema.md": "spec-architecture",
        "specs/strategies/some-strat.md": "strategy",
        "specs/signals/my-signal.md": "signal",
        "specs/risk-rules/rr-5.md": "risk-rule",
        "specs/instruments/ETHUSDT.md": "instrument",
        "runbooks/kill-switch-runbook.md": "runbook",
        "background/07-microstructure.md": "research",
        "onboarding/getting-started.md": "onboarding",
        "whitepaper/qta-v01.md": "whitepaper",
        "work/done/backtests/bt-2026-04-10-some-strat.md": "backtest",
        "work/incidents/inc-2026-04-12-x.md": "incident",
        "work/incidents/pm-2026-04-12.md": "postmortem",
        "work/done/000001-foo/00_issue.md": "work-done",
    }
    for rel, expected in cases.items():
        p = fake_docs / rel
        assert mf.infer_type(p) == expected, rel


def test_skips_dashboards_schemas_and_ai_md(fake_docs, mf):
    assert mf.infer_type(fake_docs / "dashboards/live.md") is None
    assert mf.infer_type(fake_docs / "schemas/note-schemas.md") is None
    # .ai.md is filtered by iter_target_files
    targets = {p.name for p in mf.iter_target_files(fake_docs)}
    assert ".ai.md" not in targets


def test_apply_then_idempotent(fake_docs, mf, capsys):
    # 1차 plan
    plans = [mf.plan_for(p) for p in mf.iter_target_files(fake_docs)]
    plans = [p for p in plans if p is not None]
    assert len(plans) >= 5, f"too few plans: {len(plans)}"
    # 적용
    for p in plans:
        mf.apply_plan(p)
    # 2차 dry-run — 변경 대상 0 건
    second = [mf.plan_for(p) for p in mf.iter_target_files(fake_docs)]
    second = [p for p in second if p is not None]
    assert second == [], f"expected idempotent, got {len(second)} changes"


def test_existing_frontmatter_untouched(fake_docs, mf):
    p = fake_docs / "specs/strategies/already.md"
    original = p.read_text(encoding="utf-8")
    plans = [mf.plan_for(q) for q in mf.iter_target_files(fake_docs)]
    plans = [x for x in plans if x is not None]
    for x in plans:
        mf.apply_plan(x)
    assert p.read_text(encoding="utf-8") == original


def test_h1_becomes_name(fake_docs, mf):
    p = fake_docs / "specs/strategies/some-strat.md"
    plan = mf.plan_for(p)
    assert plan is not None
    assert plan.fields["name"] == "Some Strat"
    assert plan.fields["id"] == "some-strat"
    assert plan.fields["type"] == "strategy"


def test_instrument_ticker_id_preserved(fake_docs, mf):
    p = fake_docs / "specs/instruments/ETHUSDT.md"
    plan = mf.plan_for(p)
    assert plan is not None
    # 티커형 대문자는 그대로 유지
    assert plan.fields["id"] == "ETHUSDT"
    assert plan.fields["type"] == "instrument"


def test_postmortem_detected_by_prefix(fake_docs, mf):
    p = fake_docs / "work/incidents/pm-2026-04-12.md"
    plan = mf.plan_for(p)
    assert plan is not None
    assert plan.fields["type"] == "postmortem"


def test_rendered_frontmatter_is_valid_block(fake_docs, mf):
    p = fake_docs / "runbooks/kill-switch-runbook.md"
    plan = mf.plan_for(p)
    assert plan is not None
    mf.apply_plan(plan)
    text = p.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    # 두 번째 --- 가 반드시 존재
    assert text.count("\n---\n") >= 1
    assert "type: runbook" in text
    assert "id: kill-switch-runbook" in text
