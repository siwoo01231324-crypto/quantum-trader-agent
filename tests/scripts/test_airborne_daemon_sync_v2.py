"""Regression — airborne_alert_daemon 의 텔레그램 안내가 strategy v3 와 일치.

사용자 지적 (2026-06-05):
  "원래는 텔레그램 알림에서 kst hours 거래 예정이라고 알림 와도 실제로는
   필터링돼서 안 살 수도 있잖아 그것도 통일시켜야 될 거 같은데?"

v3 (2026-06-23) 갱신: KST gate {1,3,5,7,9,14,18,21,22,23} (한 달 신호 sim 기반).

가드:
  1. _KST_HOURS_KSTHOURS 가 strategy 의 _KST_TOP_HOURS_V3 와 동일 (직접 import)
  2. KST 11시 fire 알림 — "게이트 외" 표시 (v3 에서도 밖)
  3. KST 7시 fire — "진입 예정" 표시 (v3 포함, 8시는 v3 에서 제외)
  4. BTC 하락추세 + LONG fire — "BTC 하락추세 LONG 차단" 표시
  5. BTC 하락추세 + SHORT fire — 정상 "진입 예정" (short 그대로 통과)
  6. label 생성기가 set 변경 시 자동 동기 ({1,3,5,7,9,14,18,21,22,23} → '1/3/5/7/9/14/18/21/22/23')
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from scripts.airborne_alert_daemon import (
    _KST_HOURS_KSTHOURS,
    _format_strategy_notice,
    _kst_hours_label,
    _update_btc_trend_state,
)
import scripts.airborne_alert_daemon as daemon_mod
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    _KST_TOP_HOURS_V3,
)


# ── (1) truth source 통일 ───────────────────────────────────────────────────

def test_daemon_kst_hours_imported_from_strategy():
    """daemon 의 set 이 strategy 의 _KST_TOP_HOURS_V3 와 동일 객체 또는 동일 값."""
    assert _KST_HOURS_KSTHOURS == _KST_TOP_HOURS_V3 == frozenset({1, 3, 5, 7, 9, 14, 18, 21, 22, 23})


# ── (7) 게이트 판정·표시 = 도착시각(=알림시각) 통일 (2026-06-11 봉루프 decouple) ──

def test_arrival_hour_is_bar_close_for_trader_match():
    """게이트 판정 기준 = fire *도착시각*(= 봉 마감 = 알림시각).

    봉루프 decouple 한 트레이더의 신규 게이트가 floor(fire_ts,1h).KST.hour 를
    집합에 대조하므로(docs/specs/airborne-fire-driven-consume.md), 데몬의
    마감라벨 ev.open_time 의 KST hour 가 곧 도착시각 = 게이트 시각이다.
    v0.6.51 의 -1h 시작시각 보정 되돌림.
    """
    from scripts.airborne_alert_daemon import _fire_arrival_kst_hour
    # 데몬 마감라벨 00:00 KST = 15:00 UTC → 도착시각 00:00 KST (=0)
    ev_open_ms = int(pd.Timestamp("2026-06-08T15:00:00Z").timestamp() * 1000)
    assert _fire_arrival_kst_hour(ev_open_ms) == 0  # 도착(마감) 0시
    # 마감라벨 04:00 KST = 19:00 UTC → 도착 04시
    ev_open_ms2 = int(pd.Timestamp("2026-06-08T19:00:00Z").timestamp() * 1000)
    assert _fire_arrival_kst_hour(ev_open_ms2) == 4


def test_notice_displays_arrival_hour_and_verbatim_set():
    """텔레그램 표시 = *도착시각*(= 알림시각) + 게이트 집합 그대로 (시프트 제거).

    알람이 7시에 오면 매수도 7시, 게이트 판정도 7 ∈ {1,3,5,7,9,14,18,21,22,23} → ✅.
    11시 도착(게이트 외)이면 'KST 11시 게이트 외' 그대로 (12시 시프트 없음).
    """
    msg = _format_strategy_notice(side="long", kst_hour=11, symbol="XPLUSDT")
    assert "KST 11시" in msg        # 도착시각 그대로 표기 (시프트 없음)
    assert "KST 12시" not in msg     # +1 시프트 제거됨
    assert "게이트 외" in msg
    # 게이트 집합 표시도 시프트 없이 verbatim {1,3,5,7,9,14,18,21,22,23}.
    assert "1/3/5/7/9/14/18/21/22/23" in msg


def test_notice_gate_decision_uses_arrival_hour():
    """판정 = 도착시각 — 23시 도착 long → kst-hours ✅ (23 ∈ 롱 게이트).

    봉루프 decouple 게이트와 동일: 도착시각이 곧 매수시각이자 게이트 시각.
    2026-06-23: kst-hours 는 롱 전용 → long 으로 게이트 통과 검증.
    """
    msg = _format_strategy_notice(side="long", kst_hour=23, symbol="MRVLUSDT")
    kst_seg = msg.split("kst-hours")[1].split("short-whitelist")[0]
    assert "✅ 진입 예정" in kst_seg  # 23 ∈ {1,3,5,7,9,14,18,21,22,23} → 롱 진입


def test_daemon_kst_hours_excludes_11():
    """11시 v3 밖 검증 (v1/v2 회귀 차단)."""
    assert 11 not in _KST_HOURS_KSTHOURS


def test_daemon_kst_hours_includes_7_and_8():
    """KST 7시 v3 포함, 8시 v3 제외 확인 (20시도 v3 에서 제외)."""
    assert 7 in _KST_HOURS_KSTHOURS
    assert 8 not in _KST_HOURS_KSTHOURS
    assert 20 not in _KST_HOURS_KSTHOURS


# ── (2) UI 안내 — KST gate ──────────────────────────────────────────────────

def setup_function(_func):
    """매 테스트 전 BTC trend state 초기화."""
    daemon_mod._BTC_DOWNTREND_STATE = None
    daemon_mod._BTC_DOWNTREND_REASON = ""


def test_notice_kst_11_blocked_v2():
    """v2 에서 11시는 게이트 외. v1 에선 '진입 예정' 이었던 거짓 차단."""
    msg = _format_strategy_notice(side="long", kst_hour=11, symbol="BTCUSDT")
    assert "❌" in msg
    assert "게이트 외" in msg
    assert "진입 예정" not in msg.split("kst-hours")[1].split("short-whitelist")[0]


def test_notice_kst_7_pass_v2():
    """v2 에서 7시 신규 활성."""
    msg = _format_strategy_notice(side="long", kst_hour=7, symbol="BTCUSDT")
    assert "✅ 진입 예정" in msg.split("kst-hours")[1].split("short-whitelist")[0]


def test_notice_kst_6_pass_v3():
    """v3 (2026-06-23) — KST 7시 포함 (6시는 새 set 에서 제외됨). 7시 진입 예정."""
    msg = _format_strategy_notice(side="long", kst_hour=7, symbol="BTCUSDT")
    assert "✅ 진입 예정" in msg.split("kst-hours")[1].split("short-whitelist")[0]


def test_kst_hours_label_auto_sync():
    """label 자동 생성기 — set 바뀌면 안내 문자열도 자동 갱신."""
    assert _kst_hours_label(frozenset({1, 3, 5, 7, 9, 14, 18, 21, 22, 23})) == "1/3/5/7/9/14/18/21/22/23"
    # set 다른 값으로도 동작 (회귀 가드 — 옛 hardcode 없어졌는지)
    assert _kst_hours_label(frozenset({8, 11, 16, 22})) == "8/11/16/22"


# ── (3) BTC trend filter 안내 ──────────────────────────────────────────────

def _btc_downtrend_hist(n: int = 300) -> pd.DataFrame:
    """EMA200 아래 close — strategy 의 _btc_is_downtrend 가 True 반환."""
    import numpy as np
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    closes = list(np.full(250, 100.0)) + list(np.linspace(100, 80, 50))
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0] * n,
    }, index=idx)


def _btc_uptrend_hist(n: int = 300) -> pd.DataFrame:
    import numpy as np
    idx = pd.date_range("2026-01-01", periods=n, freq="1h")
    closes = list(np.linspace(50, 100, n))
    return pd.DataFrame({
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": [1000.0] * n,
    }, index=idx)


def test_btc_trend_state_updates_to_downtrend():
    """_update_btc_trend_state 호출 → _BTC_DOWNTREND_STATE True."""
    _update_btc_trend_state(_btc_downtrend_hist())
    assert daemon_mod._BTC_DOWNTREND_STATE is True
    assert daemon_mod._BTC_DOWNTREND_REASON


def test_btc_trend_state_updates_to_uptrend():
    _update_btc_trend_state(_btc_uptrend_hist())
    assert daemon_mod._BTC_DOWNTREND_STATE is False


def test_notice_btc_downtrend_blocks_long_in_kst_gate():
    """KST 7시 (gate ON, 7∈새set) + BTC 하락추세 + LONG → '진입 예정' 이 아니라 차단 표시."""
    _update_btc_trend_state(_btc_downtrend_hist())
    msg = _format_strategy_notice(side="long", kst_hour=7, symbol="BTCUSDT")
    kst_segment = msg.split("kst-hours")[1].split("short-whitelist")[0]
    assert "❌" in kst_segment
    assert "BTC" in kst_segment and "차단" in kst_segment
    assert "진입 예정" not in kst_segment


def test_notice_btc_downtrend_passes_short_in_kst_gate():
    """KST 7시 + BTC 하락추세 + SHORT → short-whitelist 정상 진입 (btc 무관).

    2026-06-23: kst-hours 는 롱 전용이라 short 은 short-whitelist 가 담당.
    BTC 하락추세는 long 만 차단 → short 은 영향 없음.
    """
    _update_btc_trend_state(_btc_downtrend_hist())
    msg = _format_strategy_notice(side="short", kst_hour=7, symbol="BTCUSDT")
    # kst-hours 는 롱 전용 → short 미지원
    kst_segment = msg.split("kst-hours")[1].split("short-whitelist")[0]
    assert "SHORT 미지원" in kst_segment
    # short-whitelist 는 btc 하락추세 무관하게 진입 (24h 게이트)
    wl_segment = msg.split("short-whitelist")[1]
    assert "✅ 진입 예정" in wl_segment


def test_notice_btc_uptrend_passes_long_in_kst_gate():
    """KST 7시 + BTC 상승추세 + LONG → 정상 진입."""
    _update_btc_trend_state(_btc_uptrend_hist())
    msg = _format_strategy_notice(side="long", kst_hour=7, symbol="BTCUSDT")
    kst_segment = msg.split("kst-hours")[1].split("short-whitelist")[0]
    assert "✅ 진입 예정" in kst_segment


def test_notice_btc_state_none_does_not_block():
    """BTC state 미확보 (daemon 부팅 직후) → graceful, long block 안 함."""
    assert daemon_mod._BTC_DOWNTREND_STATE is None
    msg = _format_strategy_notice(side="long", kst_hour=7, symbol="BTCUSDT")
    kst_segment = msg.split("kst-hours")[1].split("short-whitelist")[0]
    # KST 7 (gate ON) + BTC 데이터 없음 → BTC check skip → 진입 예정 (graceful)
    assert "✅ 진입 예정" in kst_segment
