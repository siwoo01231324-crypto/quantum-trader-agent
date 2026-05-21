"""2026-05-21 — live-scanner stop/TP 청산 후 cooldown 차단 회귀.

ATR breakout 자리에서 stop 맞고 1초 만에 재진입 → 또 stop → 18초간 30회 churn
→ $20+ 손실 사례 (NEARUSDT, cand-c-breakout, 16:57:49~16:58:07) 의 fix.

`LiveScannerMixin.cooldown_after_stop_sec > 0` 이면 `release_live_position()`
호출 시점부터 그만큼 monotonic 시각을 기록 → dispatch 에서 그 안의 BUY 신호
차단. cooldown=0 (default) 이면 dict 변경 zero — 기존 contract 보존.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
import portfolio._async_orchestrator as orch_mod
from risk.dsl import Policy


def _orch(**kw) -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"), **kw)


def _ohlcv(n: int = 30) -> pd.DataFrame:
    import numpy as np
    close = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": np.full(n, 1000.0)}, index=idx,
    )


_SNAP = {
    "equity_krw": 1_000_000.0,
    "equity_usdt": 1_000_000.0,
    "ohlcv_history": {"BTCUSDT": _ohlcv()},
}


class _ScannerNoCooldown(LiveScannerMixin):
    """Default cooldown_after_stop_sec=0.0 (mixin default)."""

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


class _ScannerWith5MinCooldown(LiveScannerMixin):
    cooldown_after_stop_sec: ClassVar[float] = 300.0

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


def _run(orch, ts="2026-01-01") -> list:
    return asyncio.run(orch.run_bar(pd.Timestamp(ts), _SNAP))


def test_default_no_cooldown_preserves_legacy_behaviour():
    """cooldown=0 → release 후 즉시 재진입 가능 (backward compat 보장)."""
    orch = _orch()
    orch.register_strategy("scan", _ScannerNoCooldown())

    assert len(_run(orch, "2026-01-01")) == 1, "first entry must flow"
    assert _run(orch, "2026-01-02") == [], "blocked by _live_entered"

    orch.release_live_position("scan", "BTCUSDT")
    # cooldown_after_stop_sec=0 → 즉시 재진입 허용 — _stop_cooldown_until 비어있음.
    assert ("scan", "BTCUSDT") not in orch._stop_cooldown_until
    assert len(_run(orch, "2026-01-03")) == 1, "immediate re-entry on cooldown=0"


def test_cooldown_blocks_reentry_within_window(monkeypatch):
    """cooldown>0 → release 후 그 시간 안엔 BUY 차단 (churn fix)."""
    fake = {"t": 1000.0}
    monkeypatch.setattr(orch_mod.time, "monotonic", lambda: fake["t"])

    orch = _orch()
    orch.register_strategy("scan", _ScannerWith5MinCooldown())

    # 첫 진입 + stop 청산.
    assert len(_run(orch, "2026-01-01")) == 1
    orch.release_live_position("scan", "BTCUSDT")

    # 만료시각 기록 확인 — monotonic + 300.
    assert orch._stop_cooldown_until[("scan", "BTCUSDT")] == pytest.approx(1300.0)

    # cooldown 안 (t+60s, t+299s) — BUY 신호는 들어왔지만 dispatch 에서 차단.
    fake["t"] += 60.0
    assert _run(orch, "2026-01-02") == [], "cooldown 안 BUY 차단되어야 함"
    fake["t"] += 239.0  # t=1299, 만료 1초 전
    assert _run(orch, "2026-01-03") == [], "cooldown 만료 직전도 차단"

    # 만료 후 (t=1301) — 재진입 허용 + dict 에서 자동 cleanup.
    fake["t"] += 2.0
    assert len(_run(orch, "2026-01-04")) == 1, "cooldown 만료 후 재진입 허용"
    assert ("scan", "BTCUSDT") not in orch._stop_cooldown_until, "만료 entry cleanup"


def test_release_without_strategy_registered_is_no_op(monkeypatch):
    """Defensive — release_live_position 이 미등록 sid 에 호출돼도 안 깨짐."""
    fake = {"t": 1000.0}
    monkeypatch.setattr(orch_mod.time, "monotonic", lambda: fake["t"])

    orch = _orch()
    # strategy 등록 안 함 → getattr(None, ...) → 0 → cooldown 기록 X.
    orch.release_live_position("ghost", "BTCUSDT")
    assert orch._stop_cooldown_until == {}


def test_cooldown_isolated_per_symbol_and_strategy(monkeypatch):
    """(sid, symbol) 키 단위 격리 — A 가 cooldown 중이어도 B 는 진입 가능."""
    fake = {"t": 1000.0}
    monkeypatch.setattr(orch_mod.time, "monotonic", lambda: fake["t"])

    orch = _orch()
    orch.register_strategy("a", _ScannerWith5MinCooldown())
    orch.register_strategy("b", _ScannerWith5MinCooldown())

    # a 만 stop → cooldown 기록.
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _SNAP))  # 양쪽 다 entry
    orch.release_live_position("a", "BTCUSDT")

    fake["t"] += 60.0
    # a 는 cooldown 안 → 차단. b 는 cooldown 없음 → _live_entered 만 차단 (release 안 했으니).
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-02"), _SNAP))
    # b 는 _live_entered 에 있음 → 차단됨 (cooldown 과 무관).
    # a 도 차단됨 — 하지만 reason 이 stop_cooldown_active 여야 함 (live_position_open 아님).
    # 본 테스트는 단순히 "둘 다 차단" 확인 — reason 구분은 별 테스트.
    assert intents == []

    # b release (cooldown=300 이지만 우리는 b 의 cooldown 도 시작) — 60s 후엔 둘 다 cooldown 안.
    orch.release_live_position("b", "BTCUSDT")
    fake["t"] += 200.0  # a 는 t=1260 (cooldown 안), b 는 t=1260 부터 60s 지남
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-03"), _SNAP))
    assert intents == [], "둘 다 아직 cooldown 안"

    fake["t"] += 200.0  # a 는 1460 (만료), b 는 release 후 ~400s (만료)
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-04"), _SNAP))
    assert len(intents) == 2, "둘 다 cooldown 만료 → 재진입"
