"""Regression — airborne sim 의 +2%/-1% 룰 분리 (2026-06-04).

사용자 요청 — dashboard 가 기본 룰(+1%/-0.5%) 외에 더 넓은 폭(+2%/-1%) 룰
통계도 별도 페이지에서 보여줘야 함. 두 룰 결과가 같은 cache 파일에
섞이지 않게 rule_key 별 인스턴스 분리.

테스트 항목:
1. `_simulate_airborne_fire` default 호출 (positional 2 args) 는 byte-identical
   — 기존 caller 영향 0.
2. `tp_pct/sl_pct/hold_bars` keyword 로 룰 override 시 정확한 threshold 적용.
3. `_get_airborne_sim_cache(rule_key)` 가 룰별 다른 인스턴스 + 다른 파일 경로.
4. 모르는 rule_key 는 default 로 fallback (잘못된 query param 보호).
"""
from __future__ import annotations

from src.dashboard.app import (
    AIRBORNE_HOLD_BARS,
    AIRBORNE_HOLD_BARS_2PCT,
    AIRBORNE_SL_PCT,
    AIRBORNE_SL_PCT_2PCT,
    AIRBORNE_TP_PCT,
    AIRBORNE_TP_PCT_2PCT,
    _AIRBORNE_SIM_CACHE_PATHS,
    _get_airborne_sim_cache,
    _simulate_airborne_fire,
)


def _bar(open_: float, high: float, low: float, close: float) -> dict:
    return {"open": open_, "high": high, "low": low, "close": close,
            "open_time": 0, "close_time": 0}


def _long_fire(entry: float = 100.0) -> dict:
    return {"ts": "2026-06-04T00:00:00+00:00", "symbol": "BTCUSDT",
            "side": "long", "fire_close": entry}


# ── 룰 상수 검증 ────────────────────────────────────────────────────────────

def test_rule_constants():
    """두 룰 상수 + 1:2 손익비 유지 + hold_bars 동일 (1h)."""
    assert AIRBORNE_TP_PCT == 0.010
    assert AIRBORNE_SL_PCT == 0.005
    assert AIRBORNE_TP_PCT_2PCT == 0.020
    assert AIRBORNE_SL_PCT_2PCT == 0.010
    assert AIRBORNE_HOLD_BARS == AIRBORNE_HOLD_BARS_2PCT == 4
    # 손익비 1:2 유지
    assert AIRBORNE_TP_PCT / AIRBORNE_SL_PCT == 2.0
    assert AIRBORNE_TP_PCT_2PCT / AIRBORNE_SL_PCT_2PCT == 2.0


# ── _simulate_airborne_fire byte-identical 가드 ─────────────────────────────

def test_simulate_default_byte_identical():
    """positional 2-arg 호출은 기존과 동일 결과 — default 룰 (+1%/-0.5%/4)."""
    entry = 100.0
    # 4봉, 2봉째 +1.05% high 찍어 TP
    bars = [
        _bar(100, 100.5, 99.7, 100.1),
        _bar(100.1, 101.05, 100.0, 101.0),  # +1.05% high → TP
        _bar(100, 100.5, 99.5, 100.0),
        _bar(100, 100.5, 99.5, 100.0),
    ]
    out = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out is not None
    assert out["outcome"] == "TP"
    assert out["pct"] == AIRBORNE_TP_PCT * 100  # +1.0%
    assert out["bar_idx"] == 2


# ── 룰 override 가드 ────────────────────────────────────────────────────────

def test_simulate_2pct_threshold_higher():
    """+2%/-1% 룰 — +1% high 로는 TP 안 찍힘. +2% 가야 TP."""
    entry = 100.0
    # +1.05% 만 찍는 봉 — default 룰은 TP, 2pct 룰은 hold
    bars = [
        _bar(100, 100.5, 99.8, 100.1),
        _bar(100.1, 101.05, 100.0, 101.0),  # +1.05% — default 룰 TP 트리거
        _bar(100, 100.5, 99.5, 100.0),
        _bar(100, 100.5, 99.5, 100.0),
    ]
    out_default = _simulate_airborne_fire(_long_fire(entry), bars)
    assert out_default["outcome"] == "TP"

    out_2pct = _simulate_airborne_fire(
        _long_fire(entry), bars,
        tp_pct=0.020, sl_pct=0.010, hold_bars=4,
    )
    # 2pct 룰엔 TP 안 찍힘 — high 가 +1.05% 뿐. timeout 예상 (+1% close).
    assert out_2pct is not None
    assert out_2pct["outcome"] == "timeout"


def test_simulate_2pct_tp_pct_is_two():
    """+2% high 찍으면 2pct 룰에서 TP pct=+2.0%."""
    entry = 100.0
    bars = [_bar(100, 102.1, 99.5, 102.0)] + [_bar(100,100.5,99.5,100)] * 3
    out_2pct = _simulate_airborne_fire(
        _long_fire(entry), bars, tp_pct=0.020, sl_pct=0.010, hold_bars=4,
    )
    assert out_2pct["outcome"] == "TP"
    assert out_2pct["pct"] == 2.0
    assert out_2pct["bar_idx"] == 1


# ── cache 분리 가드 ─────────────────────────────────────────────────────────

def test_sim_cache_paths_separated():
    """default 와 2pct 의 캐시 파일 경로가 분리돼있어야 — 결과 안 섞임."""
    assert _AIRBORNE_SIM_CACHE_PATHS["default"] == "logs/airborne_fires/sim_cache.jsonl"
    assert _AIRBORNE_SIM_CACHE_PATHS["2pct"] == "logs/airborne_fires/sim_cache_2pct.jsonl"
    assert _AIRBORNE_SIM_CACHE_PATHS["default"] != _AIRBORNE_SIM_CACHE_PATHS["2pct"]


def test_get_sim_cache_different_instances():
    """rule_key 별 다른 인스턴스 — 같은 fire 의 두 룰 결과가 따로 저장됨."""
    c_default = _get_airborne_sim_cache("default")
    c_2pct = _get_airborne_sim_cache("2pct")
    assert c_default is not c_2pct
    assert str(c_default.path) != str(c_2pct.path)


def test_get_sim_cache_unknown_falls_back_to_default():
    """모르는 rule_key 는 default 로 fallback (잘못된 query param 보호)."""
    c_unknown = _get_airborne_sim_cache("totally_unknown_rule")
    c_default = _get_airborne_sim_cache("default")
    assert c_unknown is c_default
