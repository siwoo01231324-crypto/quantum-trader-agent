"""Regression — production.yaml 의 live-airborne-bb-reversal-kst-hours 가
사용자 결정 (2026-06-05) 한 0.005/0.010 룰을 정확히 들고 있어야 한다.

옛 룰 (0.03/0.06) 로 회귀 시 즉시 catch — 누가 실수로 되돌리면 CI fail.

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


def test_kst_hours_uses_0_5_pct_stop():
    """SL = 0.5% (옛 3% 에서 변경, 2026-06-05)."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    assert kwargs["stop_loss_pct"] == 0.005, (
        f"stop_loss_pct must be 0.005 (옛 0.03 에서 사용자 결정으로 변경). "
        f"got {kwargs.get('stop_loss_pct')!r}"
    )


def test_kst_hours_uses_1_0_pct_take_profit():
    """TP = 1.0% (옛 6% 에서 변경, 2026-06-05). R/R 1:2 유지."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    assert kwargs["take_profit_pct"] == 0.010, (
        f"take_profit_pct must be 0.010. got {kwargs.get('take_profit_pct')!r}"
    )


def test_kst_hours_risk_reward_is_1_to_2():
    """TP : SL 비율은 항상 1:2 (옛 룰 6%/3% 도 1:2, 새 룰 1%/0.5% 도 1:2)."""
    kwargs = _load_strategy_kwargs("live-airborne-bb-reversal-kst-hours")
    sl = kwargs["stop_loss_pct"]
    tp = kwargs["take_profit_pct"]
    assert tp / sl == 2.0, (
        f"R/R 1:2 가 깨졌다 — tp/sl = {tp/sl}. "
        f"airborne 의 설계 손익비는 항상 1:2 (spec 참조)."
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


def test_spec_frontmatter_5y_verdict_reflects_new_rule():
    """verdict_5y 는 새 룰 (REJECTED PF 0.545) 반영 — 옛 룰 PF 1.081 PASS 가 남아있으면 안 됨."""
    spec_fm = _parse_frontmatter(_SPEC.read_text(encoding="utf-8"))
    verdict = spec_fm.get("verdict_5y", "")
    assert "REJECTED" in verdict and "0.545" in verdict, (
        f"verdict_5y 가 옛 PF 1.081 PASS 텍스트를 유지 중 — 사용자 룰 변경 "
        f"(2026-06-05) 미반영. got: {verdict[:120]!r}"
    )
