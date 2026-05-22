"""2026-05-22 — orchestrator._live_entered ↔ store 정합 회귀.

버그: ``restore_live_entered`` 가 부팅 시 store 의 phantom 포지션을
``_live_entered`` 에 등록한 뒤, ``PositionReconciler`` 가 broker ground-truth
와 비교해 store qty 를 0 으로 ``force_sync_position`` 해도 ``_live_entered``
set 은 그대로 남았다. 결과: store flat 인데 dispatch 가 그 (sid, symbol) 을
"live_position_open" 으로 영구 진입 차단 → 재시작 후 11시간 매수 0.

Fix: reconciler auto-fix 콜백 → ``orchestrator.sync_live_entered`` → set 정합.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


def _orch() -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(Policy(policy_version=1, name="test"))


def _ohlcv(n: int = 30) -> pd.DataFrame:
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
    """cooldown_after_stop_sec=0.0 (mixin default)."""

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


class _ScannerWith5MinCooldown(LiveScannerMixin):
    cooldown_after_stop_sec: ClassVar[float] = 300.0

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="scan_buy")


def _run(orch, ts="2026-01-01") -> list:
    return asyncio.run(orch.run_bar(pd.Timestamp(ts), _SNAP))


def test_sync_live_entered_qty_zero_discards():
    """qty==0 (broker flat) → _live_entered 에서 제거 → 재진입 허용."""
    orch = _orch()
    orch._live_entered.add(("scan", "BTCUSDT"))
    orch.sync_live_entered("scan", "BTCUSDT", 0.0)
    assert ("scan", "BTCUSDT") not in orch._live_entered


def test_sync_live_entered_qty_nonzero_adds():
    """qty!=0 (broker 보유) → _live_entered 에 추가 → 중복 진입 차단."""
    orch = _orch()
    orch.sync_live_entered("scan", "ETHUSDT", 12.5)
    assert ("scan", "ETHUSDT") in orch._live_entered


def test_sync_live_entered_does_not_touch_cooldown():
    """reconcile sync 는 stop 청산이 아니므로 cooldown 을 걸지 않는다."""
    orch = _orch()
    orch._live_entered.add(("scan", "BTCUSDT"))
    orch.sync_live_entered("scan", "BTCUSDT", 0.0)
    assert ("scan", "BTCUSDT") not in orch._stop_cooldown_until


def test_phantom_restore_then_reconcile_sync_reallows_entry():
    """★ 핵심 회귀: phantom restore → 진입 차단 → reconcile sync → 진입 재개.

    ① 부팅 시 restore_live_entered 가 store 의 phantom 을 _live_entered 에 등록
    ② dispatch 가 그 종목을 "live_position_open" 으로 차단
    ③ reconciler auto-fix 가 broker flat 확인 → sync_live_entered(.., 0)
    ④ _live_entered 비워짐 → 진입 재개

    sync_live_entered 미연결이면 ③④ 가 안 일어나 영구 차단된다.
    """
    orch = _orch()
    orch.register_strategy("scan", _ScannerNoCooldown())

    # ① phantom restore — store 가 BTCUSDT 보유로 보고 _live_entered 등록
    n = orch.restore_live_entered({"scan": [("BTCUSDT", 0.5)]})
    assert n == 1
    assert ("scan", "BTCUSDT") in orch._live_entered

    # ② 진입 차단 (이미 보유 중으로 간주)
    assert _run(orch, "2026-01-01") == [], "phantom restore 로 진입 차단됨"

    # ③ reconciler auto-fix 시뮬: broker flat → sync_live_entered(.., 0)
    orch.sync_live_entered("scan", "BTCUSDT", 0.0)
    assert ("scan", "BTCUSDT") not in orch._live_entered

    # ④ 진입 재개
    assert len(_run(orch, "2026-01-02")) == 1, "sync 후 진입 재허용돼야 함"


def test_reconcile_sync_does_not_bypass_stop_cooldown():
    """★ churn 회귀 방어: stop 청산으로 #266 cooldown 이 걸린 상태에서
    reconciler 의 sync_live_entered 가 일어나도 cooldown 은 살아있어야 한다.

    sync 가 cooldown 을 우회해 _live_entered 만 비우면 stop→재진입 churn
    (18초 30회, NEAR -$20 사고) 이 되돌아온다. sync 는 _live_entered 만
    건드리고 _stop_cooldown_until 은 절대 안 건드려야 — dispatch 의 cooldown
    가드가 여전히 BUY 를 막는다.
    """
    orch = _orch()
    orch.register_strategy("scan", _ScannerWith5MinCooldown())

    # 진입
    assert len(_run(orch, "2026-01-01")) == 1
    # stop 청산 → cooldown(300s) 기록 + _live_entered discard
    orch.release_live_position("scan", "BTCUSDT")
    assert ("scan", "BTCUSDT") in orch._stop_cooldown_until

    # reconciler sync (broker flat) — _live_entered 만 건드림
    orch.sync_live_entered("scan", "BTCUSDT", 0.0)

    # cooldown 은 그대로 살아있음 → 재진입 여전히 차단 (churn 방어 유지)
    assert ("scan", "BTCUSDT") in orch._stop_cooldown_until
    assert _run(orch, "2026-01-02") == [], "cooldown 안에선 sync 후에도 진입 차단"
