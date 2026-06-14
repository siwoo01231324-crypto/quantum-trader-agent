"""Regression — production.yaml 의 live-airborne-short-whitelist-v1 시간게이트가
2026-06-14 "오후 역알파 시간대 제외" 결정을 유지해야 한다.

근거: 최근 22일(5/24~6/14) airborne SHORT sim 시간대별 PF —
  14시 PF 0.18 (WR 12%, net -9.14%) 명백한 역알파,
  13/16/17/04시 net 음수 (PF 0.92~1.07).
→ {4, 13, 14, 16, 17} 제외 (24h → 19h).

⚠️ band-aid 주의: 본 전략 spec 의 5y bench 는 TP1%/SL0.5% 운영설정에서는
시간게이트가 PF<1 근본결함(0.5% 노이즈 손절)을 못 살린다고 기록(spec L89).
본 trim 은 #380 라이브누적우선 방침하에서 명백 역알파(특히 14시)만 우선 차단.
누가 실수로 24h 로 되돌리면 즉시 catch.
"""
from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_PROD = _REPO / "configs" / "orchestrator" / "production.yaml"

# 2026-06-14 결정 — 오후 역알파로 제외한 KST 시간
_EXCLUDED = {4, 13, 14, 16, 17}
_EXPECTED = [h for h in range(24) if h not in _EXCLUDED]


def _short_wl_kwargs() -> dict:
    data = yaml.safe_load(_PROD.read_text(encoding="utf-8"))
    for entry in data.get("strategies", []) or []:
        if entry.get("id") == "live-airborne-short-whitelist-v1":
            return entry.get("kwargs", {}) or {}
    raise AssertionError(
        "live-airborne-short-whitelist-v1 가 production.yaml 에 없음 / 비활성"
    )


def test_short_whitelist_excludes_afternoon_dead_hours():
    """역알파 시간 {4,13,14,16,17} 이 진입 게이트에서 빠져 있어야 한다."""
    hours = _short_wl_kwargs()["kst_entry_hours"]
    for h in sorted(_EXCLUDED):
        assert h not in hours, (
            f"역알파 KST {h}시가 다시 들어옴 — 2026-06-14 결정 회귀. "
            f"특히 14시는 sim PF 0.18(WR 12%) 명백한 역알파."
        )


def test_short_whitelist_keeps_golden_hours():
    """황금시간(01/03/06/10/11/23) 은 반드시 유지 (sim PF 1.7~6.7)."""
    hours = _short_wl_kwargs()["kst_entry_hours"]
    for h in (1, 3, 6, 10, 11, 23):
        assert h in hours, f"황금시간 KST {h}시가 게이트에서 빠졌다"


def test_short_whitelist_hours_exact_set():
    """정확히 19시간 = 24h − {4,13,14,16,17}."""
    hours = sorted(_short_wl_kwargs()["kst_entry_hours"])
    assert hours == _EXPECTED, f"expected {_EXPECTED}, got {hours}"
