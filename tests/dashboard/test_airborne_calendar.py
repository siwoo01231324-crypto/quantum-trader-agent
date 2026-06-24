"""Regression — airborne 적중 달력 엔드포인트 `/api/airborne_calendar` (2026-06-24).

window(오늘/7d/30d) 외에 월별 일자별 집계를 제공. 사용자 요청:
"달력으로 어떤날 몇% 어떤날 몇%".

가드:
1. 유효 month(YYYY-MM) → 200 + {month, days, fires_total, rule} 구조.
2. 잘못된 month → 400.
3. days 값은 일자별 {n, sum_pct, net_pct, win_rate, pf, tp, sl, timeout}.
4. rule=2pct 분리.
5. /airborne 페이지에 달력 UI(loadCalendar/calNav/렌더) 임베드.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.dashboard.app import _render_airborne_page, app

_C = TestClient(app)


def test_calendar_valid_month_200_structure():
    r = _C.get("/api/airborne_calendar?month=2026-06")
    assert r.status_code == 200
    j = r.json()
    assert j["month"] == "2026-06"
    assert isinstance(j["days"], dict)
    assert "fires_total" in j
    assert j["rule"]["name"] == "default"


def test_calendar_day_metrics_shape():
    j = _C.get("/api/airborne_calendar?month=2026-06").json()
    for _date, x in j["days"].items():
        assert set(x) >= {"n", "sum_pct", "net_pct", "win_rate", "pf",
                          "tp", "sl", "timeout"}
        assert x["n"] >= 1  # 빈 날은 days 에 없어야


def test_calendar_bad_month_400():
    assert _C.get("/api/airborne_calendar?month=2026-13xx").status_code == 400
    assert _C.get("/api/airborne_calendar?month=garbage").status_code == 400


def test_calendar_2pct_rule_separated():
    j = _C.get("/api/airborne_calendar?month=2026-06&rule=2pct").json()
    assert j["rule"]["name"] == "2pct"
    assert j["rule"]["tp_pct"] == 0.020


def test_airborne_page_embeds_calendar_ui():
    for rule_key, expect in [("default", "default"), ("2pct", "2pct")]:
        h = _render_airborne_page(rule_key)
        assert 'id="calendar"' in h
        assert "/api/airborne_calendar" in h
        assert "function loadCalendar" in h
        assert "function calNav" in h
        assert f"const CAL_RULE = '{expect}';" in h
