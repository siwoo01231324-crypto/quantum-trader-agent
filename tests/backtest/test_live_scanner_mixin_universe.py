"""Phase 1 Dynamic Universe Architecture — Mixin default + airborne override."""
from __future__ import annotations

import pytest

from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
)
from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
)


class TestDefaults:
    def test_mixin_default_universe_is_top30(self):
        """LiveScannerMixin.get_universe() = BINANCE_USDT_TOP30 (회귀 안전)."""
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        assert LiveScannerMixin.get_universe() == list(BINANCE_USDT_TOP30)

    def test_mixin_default_interval_is_1d(self):
        """LiveScannerMixin.get_interval() = '1d' (기존 hardcoded 값)."""
        assert LiveScannerMixin.get_interval() == "1d"


class TestAirborneOverride:
    def test_airborne_kst_hours_interval_is_1h(self):
        """airborne-kst-hours 만 interval 을 1h override (Phase 1 범위)."""
        assert LiveAirborneBbReversalKstHours.get_interval() == "1h"

    def test_airborne_kst_hours_universe_is_dynamic_top100(self):
        """Phase 2 — universe = binance_top_dynamic.get_top_n_symbols(100).

        실 fetch 가능 환경 = 100 종목 / region-block / 오프라인 = fallback
        BINANCE_USDT_TOP30. 둘 다 OK — 비지 않고 BTCUSDT 포함.
        """
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        universe = LiveAirborneBbReversalKstHours.get_universe()
        assert isinstance(universe, list)
        assert len(universe) > 0
        assert "BTCUSDT" in universe
        # fallback 이면 정확히 TOP30, dynamic 이면 더 많음
        if len(universe) == len(BINANCE_USDT_TOP30):
            assert set(universe) == set(BINANCE_USDT_TOP30)

    def test_airborne_kst_morning_keeps_default_interval(self):
        """rejected 부모 (kst-morning) 는 default 1d 유지 — 회귀 X."""
        assert LiveAirborneBbReversalKstMorning.get_interval() == "1d"

    def test_class_method_callable_without_instance(self):
        """classmethod 라 instance 없이도 호출 가능."""
        # via class — no __init__
        intervals = {
            LiveAirborneBbReversalKstHours.get_interval(),
            LiveAirborneBbReversalKstMorning.get_interval(),
        }
        assert intervals == {"1d", "1h"}
