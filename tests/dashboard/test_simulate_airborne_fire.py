"""Regression — incomplete sim 이 캐시 박혀 통계 오염시키던 회귀 가드.

2026-06-02 incident: 사용자가 NOMUSDT long fire 직후 (15:00:36) /airborne
페이지 새로고침 → mainnet 봉이 아직 1개만 닫힘 (15:15) → `_fetch_15m_bars
(limit=4)` 가 1봉만 반환 → `_simulate_airborne_fire` 가 1봉 보고 `timeout
+0.04% bar_idx=1` 결과 만듦 → ``AirborneSimCache.put_many`` 가 ``logs/
airborne_fires/sim_cache.jsonl`` 에 영속 저장 → **그 후 4봉 다 닫혀도 dedup
(``if key in self._cache: continue``) 으로 영원히 재계산 안 됨** → win_rate
/ PF 통계 오염.

Fix: 4봉 다 안 닫힌 상태에서 TP/SL 조기 종결도 없으면 ``None`` 반환 →
caller (api_airborne_metrics 의 ``_sim_one``) 가 ``None`` 인 fire 는
``new_sims`` 리스트에서 제외 → 캐시 저장 안 함, 통계 집계 제외 → 다음
새로고침 때 missing 으로 재계산.

조기 종결 (TP/SL/SL_first) 은 정의상 final — 1봉 안에서 TP 찍었으면 그 결과는
4봉 다 닫혀도 안 바뀌므로 그대로 반환.
"""
from __future__ import annotations

from src.dashboard.app import (
    AIRBORNE_HOLD_BARS,
    AIRBORNE_SL_PCT,
    AIRBORNE_TP_PCT,
    _simulate_airborne_fire,
)


def _bar(open_: float, high: float, low: float, close: float) -> dict:
    return {
        "open": open_, "high": high, "low": low, "close": close,
        "open_time": 0, "close_time": 0,
    }


def _long_fire(entry: float = 100.0) -> dict:
    return {"ts": "2026-06-02T06:00:36+00:00", "symbol": "NOMUSDT",
            "side": "long", "fire_close": entry}


def _short_fire(entry: float = 100.0) -> dict:
    return {"ts": "2026-06-02T06:00:36+00:00", "symbol": "XYZUSDT",
            "side": "short", "fire_close": entry}


# ── 기존 동작 (회귀 가드) ────────────────────────────────────────────────────

def test_empty_bars_returns_none():
    """bars=[] → None (기존 동작 byte-identical)."""
    assert _simulate_airborne_fire(_long_fire(), []) is None


def test_full_4bars_timeout_returns_result():
    """4봉 다 close + TP/SL 없음 → timeout pct 결과 그대로."""
    entry = 100.0
    bars = [_bar(100, 100.3, 99.7, 100.1)] * AIRBORNE_HOLD_BARS
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is not None
    assert out["outcome"] == "timeout"
    assert out["bar_idx"] == AIRBORNE_HOLD_BARS


def test_full_4bars_tp_first_returns_tp():
    """4봉 중 2봉째 TP 찍음 → TP 결과 (조기 종결)."""
    entry = 100.0
    tp_px = entry * (1 + AIRBORNE_TP_PCT)
    bars = [
        _bar(100, 100.3, 99.8, 100.1),       # bar 1: nothing
        _bar(100.1, tp_px + 0.1, 100.0, tp_px),  # bar 2: TP hit
        _bar(100, 100.5, 99.5, 100.2),       # bar 3: unused
        _bar(100, 100.5, 99.5, 100.2),       # bar 4: unused
    ]
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is not None
    assert out["outcome"] == "TP"
    assert out["bar_idx"] == 2


# ── 이번 fix 가드 ─────────────────────────────────────────────────────────────

def test_one_bar_no_tp_no_sl_returns_none():
    """1봉만 close (4 미만) + 그 봉에 TP/SL 안 찍힘 → None (incomplete).

    2026-06-02 NOMUSDT incident 의 정확한 시나리오. 회귀 시 +0.04% timeout 같은
    잘못된 결과가 영원히 cache 박힘.
    """
    entry = 100.0
    # 0.04% 정도 등락만 — TP(1%) 도 SL(0.5%) 도 안 찍음
    bars = [_bar(100, 100.04, 99.99, 100.04)]
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is None, (
        f"fire 후 1봉만 닫힌 상태에서 TP/SL 조기 종결도 없으면 incomplete → "
        f"None 으로 캐시 skip 해야 한다. got {out!r}. 이게 안 되면 "
        f"AirborneSimCache 가 그 잘못된 결과를 영원히 박아 통계 오염."
    )


def test_three_bars_no_tp_no_sl_returns_none():
    """3봉 close (여전히 4 미만) + TP/SL 없음 → None."""
    entry = 100.0
    bars = [
        _bar(100, 100.3, 99.7, 100.1),
        _bar(100.1, 100.4, 99.8, 100.2),
        _bar(100.2, 100.5, 99.9, 100.3),
    ]
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is None


def test_one_bar_with_tp_returns_tp():
    """1봉뿐이라도 그 봉에서 TP 찍었으면 결과는 final → 정상 반환.

    조기 종결은 정의상 4봉 다 닫혀도 안 바뀜 → 캐시 가능.
    """
    entry = 100.0
    tp_px = entry * (1 + AIRBORNE_TP_PCT)
    bars = [_bar(100, tp_px + 0.1, 99.9, tp_px)]
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is not None
    assert out["outcome"] == "TP"
    assert out["bar_idx"] == 1


def test_one_bar_with_sl_returns_sl():
    """1봉만 close 인데 SL 찍었으면 그대로 SL 반환 (조기 종결, final)."""
    entry = 100.0
    sl_px = entry * (1 - AIRBORNE_SL_PCT)
    bars = [_bar(100, 100.1, sl_px - 0.1, sl_px)]
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is not None
    assert out["outcome"] == "SL"
    assert out["bar_idx"] == 1


def test_short_fire_three_bars_no_tp_no_sl_returns_none():
    """short side 도 같은 가드 — 3봉만 close, TP/SL 없음 → None."""
    entry = 100.0
    bars = [
        _bar(100, 100.3, 99.7, 99.9),
        _bar(99.9, 100.2, 99.5, 99.8),
        _bar(99.8, 100.0, 99.6, 99.9),
    ]
    out = _simulate_airborne_fire(_short_fire(entry), bars)
    assert out is None
