"""LivePositionRiskManager 시간기반 청산(timeout) — 2026-06-15.

진입 후 max_hold_sec 경과 + TP/SL 미도달이면 시장가 청산. 거래소 네이티브
TP/SL 이 있어도 동작(거래소는 가격 임계만, 시간청산 안 함) → 상승장 숏이
무한정 깔리는 것 차단. sim 의 4봉/1h hold 와 동일 개념.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore
from src.portfolio.live_position_risk import LivePositionRiskManager

_T0 = datetime(2026, 6, 15, 0, 0, 0, tzinfo=timezone.utc)


def _mgr(*, side="sell", entry=100.0, qty=1.0, max_hold_sec=3600.0, native=None,
         pending_exit_timeout_sec=None):
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    pnl.record_fill(strategy_id="sid", symbol="X", side=side,
                    qty=Decimal(str(qty)), price=Decimal(str(entry)))
    store.record_fill(strategy_id="sid", symbol="X", side=side, qty=Decimal(str(qty)))
    mgr = LivePositionRiskManager(
        position_store=store, pnl_aggregator=pnl,
        max_hold_sec=max_hold_sec, native_tpsl_check=native,
        pending_exit_timeout_sec=pending_exit_timeout_sec,
    )
    mgr.register_strategy_policy("sid", stop_loss_pct=0.005, take_profit_pct=0.011)
    return mgr, store, pnl


def test_timeout_fires_after_max_hold():
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    # 첫 평가 — 밴드 안(SL 100.5/TP 98.9), entry_ts=T0 stamp, 발동 없음.
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    # 1h+ 경과, 여전히 밴드 안 → timeout 청산.
    intents = mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=3601))
    assert len(intents) == 1
    assert intents[0].side == "buy"          # 숏 커버
    assert intents[0].reduce_only is True
    assert "time_exit" in intents[0].reason


def test_timeout_not_before_max_hold():
    mgr, _s, _p = _mgr(max_hold_sec=3600.0)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=1800)) == []


def test_timeout_disabled_when_none():
    mgr, _s, _p = _mgr(max_hold_sec=None)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    # 거의 무한 경과해도 비활성이면 timeout 안 남.
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(days=10)) == []


def test_timeout_fires_even_with_native_tpsl():
    """거래소 네이티브 TP/SL 활성이어도 timeout 은 발동 (거래소는 시간청산 안 함)."""
    mgr, _s, _p = _mgr(max_hold_sec=3600.0, native=lambda s: True)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    intents = mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=3601))
    assert len(intents) == 1
    assert "time_exit" in intents[0].reason


def test_native_still_skips_price_exit_before_timeout():
    """네이티브 활성 + SL 가격 도달 + max_hold 전 → synthetic 가격청산 안 함."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0,
                       native=lambda s: True)
    # 숏인데 +1% (SL) 이지만 네이티브가 담당 → synthetic 미발동.
    assert mgr.evaluate("X", Decimal("101.0"), _T0) == []


def test_price_sl_still_fires_before_timeout_when_no_native():
    """timeout 도입이 기존 가격 SL 을 안 깨뜨림 — 네이티브 없으면 SL 즉시 발동."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0, native=None)
    intents = mgr.evaluate("X", Decimal("100.6"), _T0)  # +0.6% > SL 0.5%
    assert len(intents) == 1
    assert "stop_loss" in intents[0].reason


def test_sweep_fires_for_tick_starved_position():
    """틱 한 번도 못 받은 종목 — sweep 이 baseline stamp 후 max_hold 경과 시 청산.

    NVDA/SPYUSDT 무한보유 사고 재현: evaluate() 가 한 번도 안 불려도(틱 0)
    sweep 이 청산해야 한다. 첫 sweep 은 baseline stamp + skip, 그 다음 max_hold
    경과한 sweep 에서 발동.
    """
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    # 틱 없이 sweep 만 — 첫 호출은 baseline stamp, 발동 없음.
    assert mgr.sweep_timeouts(_T0, lambda s: None) == []
    # max_hold 경과 → 시장가 커버.
    intents = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)
    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert intents[0].reduce_only is True
    assert "time_exit" in intents[0].reason
    assert "src=sweep" in intents[0].reason


def test_sweep_uses_tick_stamped_entry_ts():
    """evaluate() 가 이미 stamp 한 entry_ts 를 sweep 이 그대로 써 정시 청산."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []   # entry_ts=T0 (정확)
    # baseline skip 없이 바로 정시 청산 (entry_ts 가 이미 T0).
    intents = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: Decimal("100.0"))
    assert len(intents) == 1
    assert "time_exit" in intents[0].reason


def test_sweep_disabled_when_max_hold_none():
    mgr, _s, _p = _mgr(max_hold_sec=None)
    assert mgr.sweep_timeouts(_T0, lambda s: None) == []
    assert mgr.sweep_timeouts(_T0 + timedelta(days=10), lambda s: None) == []


def test_sweep_is_timeout_only_not_price():
    """sweep 은 timeout 전용 — stale price 가 SL 넘어도 가격청산 안 함(틱 경로 담당)."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.sweep_timeouts(_T0, lambda s: Decimal("100.0"))      # baseline
    # max_hold 전인데 가격은 SL(+0.5%) 훌쩍 초과 → sweep 은 무시(발동 0).
    assert mgr.sweep_timeouts(_T0 + timedelta(seconds=60), lambda s: Decimal("105.0")) == []


def test_sweep_no_double_fire_within_pending_guard():
    """sweep 발동 직후 재호출은 in-flight guard 로 중복 발사 차단."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.sweep_timeouts(_T0, lambda s: None)                  # baseline
    first = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)
    assert len(first) == 1
    # 직후(=pending guard 안) 재sweep → store 아직 held≠0 이라도 재발사 안 함.
    again = mgr.sweep_timeouts(_T0 + timedelta(seconds=3602), lambda s: None)
    assert again == []


def test_sweep_fallback_price_is_avg_cost_when_lookup_none():
    """price_lookup 가 None 이면 reason 의 last=avg_cost (시장가 참조가)."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.sweep_timeouts(_T0, lambda s: None)
    intents = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)
    assert len(intents) == 1
    assert "last=100" in intents[0].reason  # avg_cost fallback


def test_sweep_timeout_fire_clears_entry_ts():
    """sweep timeout 발화 시 _entry_ts 도 (high_water/dynamic 처럼) 리셋된다.

    회귀 방지(2026-06-22): 발화 후 _entry_ts 가 남으면, 청산이 거래소 reject
    (40804) 되거나 틱이 안 와 held==0 평가가 안 돌 때 stale entry_ts 가 영구
    잔존 → 같은 종목 재진입이 즉시 timeout (BZUSDT/ALLOUSDT 등 진입직후 청산 churn).
    """
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.evaluate("X", Decimal("100.0"), _T0)                 # stamp T0
    intents = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)
    assert len(intents) == 1 and "time_exit" in intents[0].reason
    assert ("sid", "X") not in mgr._entry_ts, "sweep 발화 후 _entry_ts 잔존 — stale 재진입 churn"


def test_evaluate_timeout_fire_clears_entry_ts():
    """tick(evaluate) timeout 발화 시에도 _entry_ts 리셋."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.evaluate("X", Decimal("100.0"), _T0)                 # stamp T0
    intents = mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=3601))
    assert len(intents) == 1 and "time_exit" in intents[0].reason
    assert ("sid", "X") not in mgr._entry_ts


def test_reentry_after_sweep_close_no_instant_timeout():
    """sweep 청산 후 (틱 없이) 재진입 — stale entry_ts 안 물려 즉시 청산 안 됨.

    BZUSDT repro: 첫 사이클이 sweep 으로 닫히고 held==0 평가가 한 번도 안 돌면
    (저유동/틱멈춤), 버그 버전은 옛 entry_ts 를 그대로 써 재진입 즉시 timeout.
    """
    mgr, store, pnl = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0,
                           pending_exit_timeout_sec=5.0)
    mgr.evaluate("X", Decimal("100.0"), _T0)                 # stamp T0
    assert len(mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)) == 1
    # 청산 체결 → flat. 단, 틱이 없어 held==0 evaluate 는 안 돈다(핵심 조건).
    store.force_sync_position(strategy_id="sid", symbol="X", qty=Decimal("0"))
    if hasattr(pnl, "reset_cost_basis"):
        pnl.reset_cost_basis("sid", "X")
    # 한참 뒤 재진입 (T0+1만초).
    pnl.record_fill(strategy_id="sid", symbol="X", side="sell",
                    qty=Decimal("1"), price=Decimal("100"))
    store.record_fill(strategy_id="sid", symbol="X", side="sell", qty=Decimal("1"))
    # 재진입 직후 sweep — 버그면 stale T0 로 held_sec 거대 → 즉시 발화. 고치면 baseline.
    instant = mgr.sweep_timeouts(_T0 + timedelta(seconds=10000), lambda s: None)
    assert instant == [], "재진입 직후 stale entry_ts 로 즉시 timeout (churn 버그)"
    # 새 baseline(=T0+10000) 기준 max_hold 경과해야 정상 발화.
    later = mgr.sweep_timeouts(_T0 + timedelta(seconds=10000 + 3601), lambda s: None)
    assert len(later) == 1 and "time_exit" in later[0].reason


def test_entry_ts_resets_on_flat_reentry():
    """청산(flat) 후 재진입은 timeout 타이머가 리셋된다."""
    mgr, store, pnl = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.evaluate("X", Decimal("100.0"), _T0)               # stamp T0
    # 외부 청산 → flat
    store.force_sync_position(strategy_id="sid", symbol="X", qty=Decimal("0"))
    pnl.reset_cost_basis("sid", "X") if hasattr(pnl, "reset_cost_basis") else None
    mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=10))  # flat → entry_ts pop
    # 재진입
    pnl.record_fill(strategy_id="sid", symbol="X", side="sell",
                    qty=Decimal("1"), price=Decimal("100"))
    store.record_fill(strategy_id="sid", symbol="X", side="sell", qty=Decimal("1"))
    mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=20))  # fresh stamp @ +20
    # +20 에서 1800s 뒤(=fresh 기준 30분 < 1h) → timeout 아직 안 남.
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=1820)) == []


# ── 진입측 _entry_ts stamp (2026-06-23, 네이티브청산 churn 근본 fix) ──────────
# register_entry_override(= orch._on_entry, 모든 진입마다 호출)가 _entry_ts 를
# overwrite. #466(청산측 pop)이 못 잡는 거래소 네이티브 TP/SL 청산 경로를 진입측
# 에서 차단 (GRAM 15:01 숏 네이티브청산 → 18:01 롱 재진입 held_sec=10812 사고).


def test_register_entry_override_overwrites_stale_entry_ts():
    """진입 훅이 stale _entry_ts 를 now 로 overwrite (setdefault 미적용)."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    stale = _T0 - timedelta(seconds=99999)
    mgr._entry_ts[("sid", "X")] = stale          # 네이티브청산으로 안 지워진 옛 값
    mgr.register_entry_override("sid", "X", stop_loss_pct=0.005, take_profit_pct=0.011)
    stamped = mgr._entry_ts[("sid", "X")]
    assert stamped != stale, "진입 훅이 stale entry_ts 를 덮어써야 함"
    now = datetime.now(timezone.utc)
    assert abs((now - stamped).total_seconds()) < 60, "now 근처로 stamp"


def test_register_entry_override_stamps_even_with_no_override():
    """override pct 전부 None(정적 policy) 이어도 _entry_ts 는 stamp (early-return 前)."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr._entry_ts.clear()
    mgr.register_entry_override("sid", "X")       # 전부 None → override 미등록
    assert ("sid", "X") in mgr._entry_ts, "override 없어도 _entry_ts stamp"


def test_native_close_reentry_no_instant_timeout():
    """GRAM repro: 네이티브청산으로 entry_ts 잔존 → 진입 훅이 재진입 시 리셋 → 즉시청산 X.

    봇 청산발화 없이 (= 네이티브 TP/SL) entry_ts 가 옛 값으로 남은 상태에서
    재진입하면, 진입 훅(register_entry_override)이 _entry_ts 를 now 로 덮어쓰므로
    held_sec 거대 계산이 안 일어남.
    """
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    # 3시간 전 entry_ts 가 잔존(네이티브청산은 pop 안 함) → 재진입 훅 호출.
    mgr._entry_ts[("sid", "X")] = datetime.now(timezone.utc) - timedelta(seconds=10812)
    mgr.register_entry_override("sid", "X", stop_loss_pct=0.005, take_profit_pct=0.011)
    # 진입 직후 sweep — fresh entry_ts(now) 라 held_sec≈0 → 즉시청산 안 됨.
    assert mgr.sweep_timeouts(datetime.now(timezone.utc), lambda s: None) == []


# ── per-strategy max_hold 오버라이드 (2026-06-18, MA크로스 time-stop 면제) ──
# 핵심: airborne(미선언)은 global 1h 그대로(영향 0), MA크로스류만 면제/별도.

def _mgr2(max_hold_sec=3600.0):
    """두 전략(airborne=글로벌, exempt=면제 대상) 숏 포지션 보유 매니저."""
    store = StrategyPositionStore()
    pnl = PnLAggregator()
    for sid in ("airborne", "exempt"):
        pnl.record_fill(strategy_id=sid, symbol="X", side="sell",
                        qty=Decimal("1"), price=Decimal("100"))
        store.record_fill(strategy_id=sid, symbol="X", side="sell", qty=Decimal("1"))
    mgr = LivePositionRiskManager(
        position_store=store, pnl_aggregator=pnl, max_hold_sec=max_hold_sec,
    )
    for sid in ("airborne", "exempt"):
        mgr.register_strategy_policy(sid, stop_loss_pct=0.005, take_profit_pct=0.011)
    return mgr, store, pnl


def test_per_strategy_max_hold_none_exempts_only_that_sid():
    """exempt 전략은 time-stop 면제, airborne 은 global 1h 그대로(영향 0) — sweep."""
    mgr, _s, _p = _mgr2(max_hold_sec=3600.0)
    mgr.set_strategy_max_hold("exempt", None)        # MA크로스류 면제
    mgr.sweep_timeouts(_T0, lambda s: None)          # baseline stamp
    intents = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)
    assert {i.strategy_id for i in intents} == {"airborne"}, \
        "exempt 가 청산됐거나 airborne 이 누락 — 면제/영향0 실패"


def test_per_strategy_no_override_is_global_byte_identical():
    """오버라이드 미설정이면 전 전략 global time-stop (airborne 기존 동작 불변)."""
    mgr, _s, _p = _mgr2(max_hold_sec=3600.0)         # set_strategy_max_hold 미호출
    mgr.sweep_timeouts(_T0, lambda s: None)
    intents = mgr.sweep_timeouts(_T0 + timedelta(seconds=3601), lambda s: None)
    assert {i.strategy_id for i in intents} == {"airborne", "exempt"}


def test_per_strategy_exempt_evaluate_path():
    """evaluate(틱) 경로도 면제 — None override 면 무한 경과해도 time_exit 안 남."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.set_strategy_max_hold("sid", None)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(days=10)) == []


def test_per_strategy_max_hold_longer_value():
    """양수 오버라이드 = 그 전략만 더 긴 보유한도(global 지나도 override 전엔 미발동)."""
    mgr, _s, _p = _mgr(side="sell", entry=100.0, max_hold_sec=3600.0)
    mgr.set_strategy_max_hold("sid", 7200.0)
    assert mgr.evaluate("X", Decimal("100.0"), _T0) == []
    assert mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=3601)) == []
    intents = mgr.evaluate("X", Decimal("100.0"), _T0 + timedelta(seconds=7201))
    assert len(intents) == 1 and "time_exit" in intents[0].reason
