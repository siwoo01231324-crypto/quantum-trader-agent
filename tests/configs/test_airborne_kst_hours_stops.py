"""Regression — production.yaml 의 live-airborne-bb-reversal-kst-hours 가
SL 1.0% / TP 2.0% 룰 (2026-06-16 손익비 widening, R/R 1:2)을 정확히 들고 있어야 한다.

옛 룰 (0.03/0.06, 또는 좁은 0.005/0.011) 로 회귀 시 즉시 catch — 누가 실수로
되돌리면 CI fail.

또한 spec frontmatter 도 동일 값. 실 운영과 spec 가 mismatch 면 docs 가 거짓.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_PROD = _REPO / "configs" / "orchestrator" / "production.yaml"
_SPEC = _REPO / "docs" / "specs" / "strategies" / "live-airborne-bb-reversal-kst-hours.md"


def _load_strategy_kwargs(strategy_id: str) -> dict:
    data = yaml.safe_load(_PROD.read_text(encoding="utf-8"))
    for entry in data.get("strategies", []) or []:
        if entry.get("id") == strategy_id:
            return entry.get("kwargs", {}) or {}
    raise AssertionError(
        f"strategy id={strategy_id!r} not found / inactive in production.yaml"
    )


def test_kst_hours_uses_1_0_pct_stop():
    """SL = 1.0% (2026-06-16 widening, 옛 0.5%/3% 에서 변경)."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    assert kwargs["stop_loss_pct"] == 0.010, (
        f"stop_loss_pct must be 0.010 (2026-06-16 widening). "
        f"got {kwargs.get('stop_loss_pct')!r}"
    )


def test_kst_hours_uses_2_0_pct_take_profit():
    """TP = 2.0% (2026-06-16 widening, 옛 1.0%/1.1%/6% 에서 변경). R/R 1:2."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    assert kwargs["take_profit_pct"] == 0.020, (
        f"take_profit_pct must be 0.020 (2026-06-16 widening). "
        f"got {kwargs.get('take_profit_pct')!r}"
    )


def test_kst_hours_risk_reward_is_1_to_2():
    """TP : SL 비율 = 1:2 (SL 1.0% / TP 2.0%, 2026-06-16 widening 후에도 유지)."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    sl = kwargs["stop_loss_pct"]
    tp = kwargs["take_profit_pct"]
    assert abs(tp / sl - 2.0) < 1e-9, (
        f"R/R 1:2 가 깨졌다 — tp/sl = {tp/sl}. "
        f"airborne 의 설계 손익비는 1:2 (spec 참조)."
    )


def test_kst_hours_cooldown_still_900():
    """cooldown_after_stop_sec 는 룰 변경과 무관 — 900 (15분) 유지."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    assert kwargs["cooldown_after_stop_sec"] == 900


# ── spec frontmatter 도 같은 값 (production.yaml ↔ spec mismatch 차단) ────

def _parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---", text, flags=re.DOTALL)
    assert m, "spec 파일에 YAML frontmatter 없음"
    return yaml.safe_load(m.group(1))


def test_spec_frontmatter_matches_production_yaml():
    """spec frontmatter 의 stop/TP 가 production.yaml 과 일치 — docs drift 차단."""
    spec_fm = _parse_frontmatter(_SPEC.read_text(encoding="utf-8"))
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    assert spec_fm["stop_loss_pct"] == kwargs["stop_loss_pct"], (
        f"spec frontmatter stop_loss_pct ({spec_fm['stop_loss_pct']}) ≠ "
        f"production.yaml ({kwargs['stop_loss_pct']}) — docs 가 거짓."
    )
    assert spec_fm["take_profit_pct"] == kwargs["take_profit_pct"], (
        f"spec frontmatter take_profit_pct ({spec_fm['take_profit_pct']}) ≠ "
        f"production.yaml ({kwargs['take_profit_pct']})."
    )


def test_spec_frontmatter_5y_verdict_is_rejected_with_losing_pf():
    """5y 판정은 REJECTED 이고 profit_factor_bt < 1.0 (손실 룰) 이어야 한다.

    verdict_5y prose 의 특정 숫자 문자열에 결합하지 않는다 — verdict 텍스트는
    게이트/룰 변경마다 정당하게 재작성되므로(예: 2026-06-06 KST gate v3),
    PF 는 전용 필드 ``profit_factor_bt`` 로 검증해 안정적인 회귀 가드로 둔다.
    옛 룰 PF 1.081 PASS 로 회귀하면 (verdict 또는 PF 둘 중 하나로) catch.
    """
    spec_fm = _parse_frontmatter(_SPEC.read_text(encoding="utf-8"))
    verdict = spec_fm.get("verdict_5y", "")
    pf = spec_fm.get("profit_factor_bt")
    assert "REJECTED" in verdict, (
        f"verdict_5y 가 REJECTED 아님 — 5y 미통과 룰 반영 필요. got: {verdict[:120]!r}"
    )
    assert pf is not None and float(pf) < 1.0, (
        f"profit_factor_bt 가 1.0 이상 — 옛 PASS 수치로 회귀? got: {pf!r}"
    )
