"""HTS 조건검색식 evaluator 단위 테스트 (#230).

각 조건 A~H 의 boundary + happy/fail path 검증. 통합 시나리오 (3종 검색식
전체 통과) 도 1건씩 포함.
"""
from __future__ import annotations

import pytest

from src.screeners.hts_cond import (
    DailyScreeningInputs,
    DtsCondition,
    DtsInputs,
    PROFILE_DTS,
    PROFILE_SWING,
    PROFILE_WAIT5M,
    SwingCondition,
    Wait5mCondition,
    Wait5mInputs,
    cond_h_dts,
    cond_h_wait5m,
    common_passes,
)
from src.screeners.hts_cond.common import (
    cond_a,
    cond_b,
    cond_c,
    cond_d,
    cond_e,
    cond_f,
    cond_g,
)
from src.screeners.hts_cond.dts import ThreeMinBar


# -- Fixtures ---------------------------------------------------------------

def _passing_daily(**overrides) -> DailyScreeningInputs:
    """단타·5분대기 검색식을 통과하는 베이스라인 일간 입력 (모든 조건 True)."""
    defaults = dict(
        symbol="005930",
        prev_close=5_000.0,
        prev_close_2=4_950.0,
        today_close=5_400.0,            # 등락률 +8% → B (≥2%), G (≥5%) 통과, A (≤10000) 통과
        today_volume=200_000,           # ≥40,000, ≤9억 → C 통과
        vol_5d_cumsum=2_000_000,        # ≥50만, ≤900억 → D 통과
        power_ratio=120.0,              # ≥90, ≤1000 → E 통과
        ma5=5_300.0,
        ma20=5_100.0,
        ma60=4_900.0,                   # close=5400 > ma5=5300 > ma20=5100 > ma60=4900 → F 통과
    )
    defaults.update(overrides)
    return DailyScreeningInputs(**defaults)


def _ascending_3min_bars(n: int = 30, base: float = 5_000.0, step: float = 5.0) -> list[ThreeMinBar]:
    """단조증가 3분봉 — 모든 봉의 close 가 SMA20 보다 높아짐 (H 통과 보장)."""
    return [ThreeMinBar(close=base + i * step) for i in range(n)]


def _descending_3min_bars(n: int = 30, base: float = 5_000.0, step: float = 5.0) -> list[ThreeMinBar]:
    """단조감소 — 마지막 10봉 모두 close < SMA20 → H 실패."""
    return [ThreeMinBar(close=base - i * step) for i in range(n)]


# -- 개별 조건 단위 테스트 ----------------------------------------------------

class TestCondA:
    def test_within_range_today_passes(self) -> None:
        i = _passing_daily(today_close=5_400.0)
        assert cond_a(i, PROFILE_DTS) is True

    def test_below_min_fails(self) -> None:
        i = _passing_daily(today_close=800.0)
        assert cond_a(i, PROFILE_DTS) is False

    def test_above_max_dts_fails(self) -> None:
        i = _passing_daily(today_close=10_500.0)
        assert cond_a(i, PROFILE_DTS) is False

    def test_swing_window_uses_prev_close(self) -> None:
        # 스윙 A: 2봉이내 — today 가 9100 (out) 이어도 prev 가 5000 이면 통과
        i = _passing_daily(today_close=9_100.0, prev_close=5_000.0)
        assert cond_a(i, PROFILE_SWING) is True

    def test_swing_max_9000(self) -> None:
        # 스윙 max 는 9000 — today=9001 + prev=9001 둘 다 out
        i = _passing_daily(today_close=9_001.0, prev_close=9_001.0)
        assert cond_a(i, PROFILE_SWING) is False


class TestCondB:
    def test_dts_2pct_lower_bound(self) -> None:
        # 정확히 2% → 통과
        i = _passing_daily(prev_close=5_000.0, today_close=5_100.0)
        assert cond_b(i, PROFILE_DTS) is True

    def test_dts_below_2pct_fails(self) -> None:
        i = _passing_daily(prev_close=5_000.0, today_close=5_099.0)
        assert cond_b(i, PROFILE_DTS) is False

    def test_swing_3pct_lower_bound(self) -> None:
        i = _passing_daily(prev_close=5_000.0, today_close=5_150.0)
        assert cond_b(i, PROFILE_SWING) is True

    def test_swing_below_3pct_fails(self) -> None:
        i = _passing_daily(prev_close=5_000.0, today_close=5_149.0)
        assert cond_b(i, PROFILE_SWING) is False

    def test_above_30pct_fails(self) -> None:
        i = _passing_daily(prev_close=5_000.0, today_close=6_500.1)
        assert cond_b(i, PROFILE_DTS) is False

    def test_zero_prev_close_returns_false(self) -> None:
        i = _passing_daily(prev_close=0.0)
        assert cond_b(i, PROFILE_DTS) is False


class TestCondC:
    def test_dts_40k_lower(self) -> None:
        i = _passing_daily(today_volume=40_000)
        assert cond_c(i, PROFILE_DTS) is True

    def test_dts_below_40k_fails(self) -> None:
        i = _passing_daily(today_volume=39_999)
        assert cond_c(i, PROFILE_DTS) is False

    def test_swing_50k_lower(self) -> None:
        i = _passing_daily(today_volume=50_000)
        assert cond_c(i, PROFILE_SWING) is True

    def test_swing_below_50k_fails(self) -> None:
        i = _passing_daily(today_volume=49_999)
        assert cond_c(i, PROFILE_SWING) is False


class TestCondD:
    def test_500k_lower_passes(self) -> None:
        i = _passing_daily(vol_5d_cumsum=500_000)
        assert cond_d(i) is True

    def test_below_500k_fails(self) -> None:
        i = _passing_daily(vol_5d_cumsum=499_999)
        assert cond_d(i) is False


class TestCondE:
    def test_90_lower_passes(self) -> None:
        i = _passing_daily(power_ratio=90.0)
        assert cond_e(i) is True

    def test_below_90_fails(self) -> None:
        i = _passing_daily(power_ratio=89.9)
        assert cond_e(i) is False

    def test_1000_upper_passes(self) -> None:
        i = _passing_daily(power_ratio=1000.0)
        assert cond_e(i) is True

    def test_above_1000_fails(self) -> None:
        i = _passing_daily(power_ratio=1000.1)
        assert cond_e(i) is False


class TestCondF:
    def test_strict_descending_mas_passes(self) -> None:
        i = _passing_daily(today_close=5_400, ma5=5_300, ma20=5_100, ma60=4_900)
        assert cond_f(i) is True

    def test_equal_ma_fails_strict(self) -> None:
        # close == ma5 → strictly greater 위반
        i = _passing_daily(today_close=5_300, ma5=5_300, ma20=5_100, ma60=4_900)
        assert cond_f(i) is False

    def test_inverted_fails(self) -> None:
        i = _passing_daily(today_close=5_400, ma5=5_500, ma20=5_100, ma60=4_900)
        assert cond_f(i) is False


class TestCondG:
    def test_5pct_lower_passes(self) -> None:
        i = _passing_daily(prev_close=5_000.0, today_close=5_250.0)
        assert cond_g(i) is True

    def test_below_5pct_fails(self) -> None:
        i = _passing_daily(prev_close=5_000.0, today_close=5_249.0)
        assert cond_g(i) is False


# -- H 조건 테스트 ----------------------------------------------------------

class TestCondHDts:
    def test_ascending_passes(self) -> None:
        bars = _ascending_3min_bars(30)
        assert cond_h_dts(bars) is True

    def test_descending_fails(self) -> None:
        bars = _descending_3min_bars(30)
        assert cond_h_dts(bars) is False

    def test_too_few_bars_fails(self) -> None:
        # < 20봉 → SMA20 계산 불가
        assert cond_h_dts(_ascending_3min_bars(15)) is False

    def test_single_supporting_bar_in_window_passes(self) -> None:
        # 마지막 10봉 중 9봉이 SMA 아래, 1봉만 SMA 위 → True
        bars = [ThreeMinBar(close=100.0)] * 20 + \
               [ThreeMinBar(close=90.0)] * 9 + \
               [ThreeMinBar(close=110.0)]
        assert cond_h_dts(bars) is True

    def test_disparity_999_upper_excluded(self) -> None:
        # 19봉 close=1 + 마지막 봉 close=10001
        # → n=20, window=10 → start_idx=19, 평가는 i=19 만
        # → sma(bars[0..19]) = (19×1 + 10001)/20 = 510.0
        # → disparity = 10001/510*100 ≈ 1961% > 999% → False
        bars = [ThreeMinBar(close=1.0)] * 19 + [ThreeMinBar(close=10_001.0)]
        assert cond_h_dts(bars) is False


class TestCondHWait5m:
    def test_at_vi_passes(self) -> None:
        # current 정확히 VI (proximity = 0) → 통과
        assert cond_h_wait5m(prev_close=5_000.0, current_price=5_500.0) is True

    def test_within_3pct_passes(self) -> None:
        # VI=5500, current=5400 → proximity=(5500-5400)/5500=1.82% → 통과
        assert cond_h_wait5m(prev_close=5_000.0, current_price=5_400.0) is True

    def test_far_from_vi_fails(self) -> None:
        # VI=5500, current=5000 → proximity=9.09% → 실패
        assert cond_h_wait5m(prev_close=5_000.0, current_price=5_000.0) is False

    def test_above_vi_fails(self) -> None:
        # current > VI → proximity 음수 → 실패 (이미 발동)
        assert cond_h_wait5m(prev_close=5_000.0, current_price=5_600.0) is False

    def test_zero_prev_close_returns_false(self) -> None:
        assert cond_h_wait5m(prev_close=0.0, current_price=100.0) is False


# -- 통합 시나리오 ----------------------------------------------------------

class TestDtsCondition:
    def test_full_passes(self) -> None:
        daily = _passing_daily()
        bars = _ascending_3min_bars(30)
        inputs = DtsInputs(daily=daily, three_min_bars=bars)
        assert DtsCondition().passes(inputs) is True

    def test_daily_fail_blocks(self) -> None:
        daily = _passing_daily(power_ratio=50.0)  # E 실패
        bars = _ascending_3min_bars(30)
        inputs = DtsInputs(daily=daily, three_min_bars=bars)
        assert DtsCondition().passes(inputs) is False

    def test_h_fail_blocks(self) -> None:
        daily = _passing_daily()
        bars = _descending_3min_bars(30)
        inputs = DtsInputs(daily=daily, three_min_bars=bars)
        assert DtsCondition().passes(inputs) is False

    def test_debug_returns_all_keys(self) -> None:
        daily = _passing_daily()
        bars = _ascending_3min_bars(30)
        inputs = DtsInputs(daily=daily, three_min_bars=bars)
        result = DtsCondition().debug(inputs)
        assert set(result.keys()) == {"A", "B", "C", "D", "E", "F", "G", "H"}
        assert all(result.values())


class TestWait5mCondition:
    def test_full_passes(self) -> None:
        # prev=5000 → VI=5500. today_close=5400 → ret=+8% (B,G 통과), proximity=1.82% (H 통과)
        daily = _passing_daily(prev_close=5_000.0, today_close=5_400.0)
        inputs = Wait5mInputs(daily=daily, current_price=5_400.0)
        assert Wait5mCondition().passes(inputs) is True

    def test_h_fail_blocks(self) -> None:
        daily = _passing_daily(prev_close=5_000.0, today_close=5_400.0)
        # current=5000 → proximity=9% → H 실패
        inputs = Wait5mInputs(daily=daily, current_price=5_000.0)
        assert Wait5mCondition().passes(inputs) is False


class TestSwingCondition:
    def test_full_passes(self) -> None:
        # 스윙 임계값: A 9000 상한, B 3%+, C 50k+
        # prev=5000, today=5400 → +8% (B 통과), today 5400 (≤9000), volume must be ≥50k
        daily = _passing_daily(
            prev_close=5_000.0, today_close=5_400.0,
            today_volume=60_000,
        )
        assert SwingCondition().passes(daily) is True

    def test_swing_b_3pct_lower_blocks(self) -> None:
        # 등락률 2.5% → swing B (≥3%) 실패. 동일 입력이 DTS B (≥2%) 는 통과해야 함.
        # ma5/20/60 을 today_close=5125 기준 정배열로 재지정 (F 통과 위함).
        daily = _passing_daily(
            prev_close=5_000.0, today_close=5_125.0,    # +2.5%
            today_volume=60_000,
            ma5=5_100.0, ma20=4_900.0, ma60=4_700.0,    # close=5125 > ma5=5100 정배열
        )
        assert SwingCondition().passes(daily) is False  # swing B 실패
        # 같은 입력이 DTS profile (B≥2%) 로는 G 만 실패 (2.5% < 5%). B,F 는 통과.
        from src.screeners.hts_cond.common import cond_b, cond_g
        assert cond_b(daily, PROFILE_DTS) is True
        assert cond_g(daily) is False  # 2.5% < 5% → G 실패는 어느 profile 이든 동일


# -- profile threshold 정합성 -----------------------------------------------

class TestProfileThresholds:
    def test_dts_and_wait5m_share_thresholds(self) -> None:
        """단타와 5분대기는 A,B,C,D,E,F,G 동일 — H 만 다름."""
        assert PROFILE_DTS == PROFILE_WAIT5M

    def test_swing_stricter_than_dts(self) -> None:
        assert PROFILE_SWING.a_price_max < PROFILE_DTS.a_price_max
        assert PROFILE_SWING.a_window > PROFILE_DTS.a_window
        assert PROFILE_SWING.b_return_min > PROFILE_DTS.b_return_min
        assert PROFILE_SWING.c_volume_min > PROFILE_DTS.c_volume_min
