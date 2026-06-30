"""마감봉 게이트 (2026-06-30) — live forming-bar 진입 차단 + 봉당 1진입 dedup.

라이브는 매 틱 평가라 history[-1]=미완성 4h봉. 그 봉에 진입하면 봉 마감 시
조건이 뒤집혀도(변동성) 이미 들어간 상태 = 백테스트(마감봉만)와 불일치.
``ctx['live_run']`` 시 미완성 마지막 봉을 떼고 *마감봉* 으로만 평가한다.
backtest(live_run 없음)는 무변경 — byte-identical (기존 전략 테스트 68건이 보장).
"""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pandas as pd

from backtest.strategies.live_capitulation_bounce import LiveCapitulationBounce
from backtest.strategies.live_donchian_breakout_btcgate import (
    LiveDonchianBreakoutBtcGate,
)
from tests.backtest.test_live_capitulation_bounce import _HAMMER, _history
from tests.backtest.test_live_donchian_breakout_btcgate import (
    _breakout_history, _btc_up,
)


def _run(strat, ctx):
    coro = strat.on_bar(ctx)
    try:
        coro.send(None)
    except StopIteration as stop:
        return stop.value
    finally:
        coro.close()
    return None


def _cap_live_ctx(history, ts, symbol="BTCUSDT"):
    return {
        "ts": ts, "live_run": True,
        "market_snapshot": {"symbol": symbol, "history": history,
                            "price": float(history["close"].iloc[-1])},
        "factors": {},
    }


def _don_live_ctx(history, btc, ts, symbol="BTCUSDT"):
    return {
        "ts": ts, "live_run": True,
        "market_snapshot": {"symbol": symbol, "history": history,
                            "price": float(history["close"].iloc[-1]),
                            "universe_ohlcv": {"BTCUSDT": btc}},
        "factors": {},
    }


class TestCapitulationClosedBarGate:
    def test_forming_allowed_by_default(self):
        # 기본(SWING_CLOSED_BAR_GATE 미설정): forming 봉 진입 허용(분석상 동등~우세).
        s = LiveCapitulationBounce()
        h = _history(_HAMMER)
        last_open = pd.Timestamp(h.index[-1])
        sig = _run(s, _cap_live_ctx(h, last_open + timedelta(hours=1)))  # 형성중
        assert sig.action == "buy"  # forming hammer 진입 — 기본 동작

    def test_forming_bar_entry_blocked_when_gate_on(self, monkeypatch):
        # gate ON: 형성중 hammer 봉 떼고 *직전(평범)* 봉 평가 → 진입 안 함.
        monkeypatch.setenv("SWING_CLOSED_BAR_GATE", "1")
        s = LiveCapitulationBounce()
        h = _history(_HAMMER)
        last_open = pd.Timestamp(h.index[-1])
        sig = _run(s, _cap_live_ctx(h, last_open + timedelta(hours=1)))
        assert sig.action == "hold"

    def test_closed_bar_enters_when_gate_on(self, monkeypatch):
        # gate ON + 마지막 봉 이미 마감 → 그 봉(hammer) 평가 → 진입.
        monkeypatch.setenv("SWING_CLOSED_BAR_GATE", "1")
        s = LiveCapitulationBounce()
        h = _history(_HAMMER)
        last_open = pd.Timestamp(h.index[-1])
        sig = _run(s, _cap_live_ctx(h, last_open + timedelta(hours=4)))
        assert sig.action == "buy"

    def test_dedup_same_closed_bar(self):
        # 같은 마감봉에 두 번 평가 → 두 번째는 bar_dedup 으로 hold (재진입 차단).
        s = LiveCapitulationBounce()
        h = _history(_HAMMER)
        ts = pd.Timestamp(h.index[-1]) + timedelta(hours=4)
        first = _run(s, _cap_live_ctx(h, ts))
        second = _run(s, _cap_live_ctx(h, ts))
        assert first.action == "buy"
        assert second.action == "hold" and "dedup" in (second.reason or "")

    def test_backtest_unchanged_no_live_run(self):
        # live_run 없으면 (백테스트) 게이트 무관 — hammer 마지막봉 그대로 진입.
        from tests.backtest.test_live_capitulation_bounce import _ctx
        s = LiveCapitulationBounce()
        assert _run(s, _ctx(_history(_HAMMER))).action == "buy"


class TestDonchianClosedBarGate:
    def test_forming_breakout_allowed_by_default(self):
        # 기본: forming 돌파 진입 허용.
        s = LiveDonchianBreakoutBtcGate()
        h = _breakout_history()
        btc = _btc_up()
        last_open = pd.Timestamp(h.index[-1])
        sig = _run(s, _don_live_ctx(h, btc, last_open + timedelta(hours=1)))
        assert sig.action == "buy"

    def test_forming_breakout_blocked_when_gate_on(self, monkeypatch):
        # gate ON: 형성중 돌파봉 떼면 직전(돌파 전) 봉 → no_breakout.
        monkeypatch.setenv("SWING_CLOSED_BAR_GATE", "1")
        s = LiveDonchianBreakoutBtcGate()
        h = _breakout_history()
        btc = _btc_up()
        last_open = pd.Timestamp(h.index[-1])
        sig = _run(s, _don_live_ctx(h, btc, last_open + timedelta(hours=1)))
        assert sig.action == "hold"

    def test_closed_breakout_enters_when_gate_on(self, monkeypatch):
        monkeypatch.setenv("SWING_CLOSED_BAR_GATE", "1")
        s = LiveDonchianBreakoutBtcGate()
        h = _breakout_history()
        btc = _btc_up()
        last_open = pd.Timestamp(h.index[-1])
        sig = _run(s, _don_live_ctx(h, btc, last_open + timedelta(hours=4)))
        assert sig.action == "buy"
