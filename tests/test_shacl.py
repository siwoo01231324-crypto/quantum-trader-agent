"""tests/test_shacl.py — SHACL 규칙별 위반/준수 픽스처 단위 테스트.

픽스처 네이밍 규칙:
  tests/fixtures/shacl/rule_{NN}_{slug}_{violates|compliant}.ttl

각 규칙에 대해 위반/준수 한 쌍이 반드시 존재해야 한다.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "shacl"
SHAPES_TTL = REPO_ROOT / "docs" / "ontology" / "shapes.ttl"
ONTOLOGY_TTL = REPO_ROOT / "docs" / "ontology" / "trading.ttl"

# scripts/ 를 sys.path 에 추가해 shacl_validate 임포트.
sys.path.insert(0, str(REPO_ROOT / "scripts"))
from shacl_validate import run_shacl  # noqa: E402

FIXTURE_PATTERN = re.compile(r"rule_(\d+)_(.+)_(violates|compliant)\.ttl$")

# 규칙 번호 → 기대 shape 이름 매핑. shapes.ttl 의 NodeShape 이름과 일치해야 함.
RULE_TO_SHAPE = {
    "01": "LiveStrategyRiskRuleShape",
    "02": "LiveStrategySharpeShape",
    "03": "IncidentCriticalPostmortemShape",
    "04": "BacktestPeriodShape",
    "05": "SignalLookbackShape",
    "06": "RiskRuleThresholdRangeShape",
    "07": "StrategyTimeframeEnumShape",
    "08": "InstrumentVenueEnumShape",
    "09": "IncidentP0AffectedShape",
    "10": "PostmortemFinalActionItemShape",
}


def _collect_fixtures() -> list[tuple[str, Path, bool]]:
    """fixtures/shacl/*.ttl 을 스캔해 (rule_num, path, expected_violates) 리스트 반환."""
    items: list[tuple[str, Path, bool]] = []
    for ttl in sorted(FIXTURE_DIR.glob("rule_*.ttl")):
        m = FIXTURE_PATTERN.match(ttl.name)
        if not m:
            continue
        rule_num = m.group(1)
        kind = m.group(3)
        items.append((rule_num, ttl, kind == "violates"))
    return items


@pytest.fixture(scope="module")
def shapes_and_ontology_exist() -> None:
    assert SHAPES_TTL.exists(), f"shapes.ttl 누락: {SHAPES_TTL}"
    assert ONTOLOGY_TTL.exists(), f"trading.ttl 누락: {ONTOLOGY_TTL}"


@pytest.mark.parametrize(
    "rule_num,fixture_path,expected_violates",
    [
        pytest.param(rn, fp, ev, id=f"rule{rn}-{'violates' if ev else 'compliant'}-{fp.stem}")
        for rn, fp, ev in _collect_fixtures()
    ],
)
def test_shacl_rule(
    shapes_and_ontology_exist, rule_num: str, fixture_path: Path, expected_violates: bool
) -> None:
    """각 픽스처에 대해 기대 shape 이름의 위반 여부를 확인한다."""
    expected_shape = RULE_TO_SHAPE.get(rule_num)
    assert expected_shape, f"RULE_TO_SHAPE 에 rule {rule_num} 매핑 없음"

    violations = run_shacl(
        data_path=fixture_path,
        shapes_path=SHAPES_TTL,
        ontology_path=ONTOLOGY_TTL,
    )

    matched = [v for v in violations if v.source_shape == expected_shape]

    if expected_violates:
        assert matched, (
            f"[{fixture_path.name}] {expected_shape} 위반 기대했으나 감지되지 않음. "
            f"전체 위반: {[v.source_shape for v in violations]}"
        )
        # 한국어 메시지가 살아있는지 간단 검증.
        assert all(v.message and v.message.strip() for v in matched)
    else:
        assert not matched, (
            f"[{fixture_path.name}] {expected_shape} 위반이 없어야 하는데 감지됨. "
            f"메시지: {[v.message for v in matched]}"
        )


def test_every_rule_has_both_violates_and_compliant() -> None:
    """10개 규칙 각각에 대해 위반/준수 픽스처가 모두 존재함을 보증."""
    seen: dict[str, set[str]] = {}
    for rn, _fp, violates in _collect_fixtures():
        seen.setdefault(rn, set()).add("violates" if violates else "compliant")

    missing: list[str] = []
    for rn in RULE_TO_SHAPE.keys():
        kinds = seen.get(rn, set())
        for need in ("violates", "compliant"):
            if need not in kinds:
                missing.append(f"rule {rn} → {need}")

    assert not missing, f"누락된 픽스처: {missing}"


def test_shape_names_exist_in_shapes_ttl() -> None:
    """RULE_TO_SHAPE 의 모든 shape 이름이 실제 shapes.ttl 에 존재해야 한다."""
    text = SHAPES_TTL.read_text(encoding="utf-8")
    for _rn, name in RULE_TO_SHAPE.items():
        assert f"qta:{name}" in text, f"shapes.ttl 에 qta:{name} 없음"
