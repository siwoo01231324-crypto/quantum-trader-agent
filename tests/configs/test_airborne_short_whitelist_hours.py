"""Regression — production.yaml 의 live-airborne-short-whitelist-v1 시간게이트가
2026-06-23 "24시각 전부 진입" 결정을 유지해야 한다.

근거: short-whitelist 는 숏 전용 전략으로, 롱 게이트(kst-hours)와 독립 운영.
2026-06-23 — 오후 역알파 제외 정책 폐기, 24시각 전부 진입으로 변경.
누가 실수로 일부 시간을 제외하면 즉시 catch.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_PROD = _REPO / "configs" / "orchestrator" / "production.yaml"

# 2026-06-23 결정 — 24시각 전부 진입
_EXPECTED = list(range(24))


def _short_wl_kwargs() -> dict:
    data = yaml.safe_load(_PROD.read_text(encoding="utf-8"))
    for entry in data.get("strategies", []) or []:
        if entry.get("id") == "live-airborne-short-whitelist-v1":
            return entry.get("kwargs", {}) or {}
    raise AssertionError(
        "live-airborne-short-whitelist-v1 가 production.yaml 에 없음 / 비활성"
    )


def test_short_whitelist_excludes_afternoon_dead_hours():
    """2026-06-23 — 24시각 전부 포함, 제외 시각 없음."""
    hours = _short_wl_kwargs()["kst_entry_hours"]
    assert len(hours) == 24, (
        f"24시각 전부 포함 기대, got {len(hours)}시각: {sorted(hours)}"
    )
    for h in range(24):
        assert h in hours, f"KST {h}시가 게이트에서 빠졌다 — 2026-06-23 전시각 결정 회귀."


def test_short_whitelist_keeps_golden_hours():
    """황금시간(01/03/06/10/11/23) 은 반드시 유지."""
    hours = _short_wl_kwargs()["kst_entry_hours"]
    for h in (1, 3, 6, 10, 11, 23):
        assert h in hours, f"황금시간 KST {h}시가 게이트에서 빠졌다"


def test_short_whitelist_hours_exact_set():
    """정확히 24시간 전부 = 0..23."""
    hours = sorted(_short_wl_kwargs()["kst_entry_hours"])
    assert hours == _EXPECTED, f"expected {_EXPECTED}, got {hours}"
