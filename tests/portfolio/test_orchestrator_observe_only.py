"""observe_only 관찰 전용 전략 HARD GUARD (2026-07-08).

macross 실거래 pause 중에도 "실 진입 타이밍" 신호를 수집하기 위한 모드.
observe_only 집합의 전략은:
  - 진입 신호(buy/sell)를 strategy_evaluated 로 기록만 하고
  - **실주문(OrderIntent) 0** — run_bar HARD GUARD
  - _live_entered 미변경 · _on_live_entry(알림) 미발동
관찰 아닌 전략은 100% 정상 매매 (레거시 보존).
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


def _ohlcv(symbol: str, n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": np.full(n, 1000.0)}, index=idx,
    )


class _SellScanner(LiveScannerMixin):
    shorts_allowed: ClassVar[bool] = True

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="sell", size=0.05, reason="death_short:regime=down")


def _snap():
    return {"symbol": None, "price": None, "equity_krw": 1e6, "equity_usdt": 1e6,
            "ohlcv_history": {"SOLUSDT": _ohlcv("SOLUSDT")}}


def test_observe_only_emits_no_order():
    """관찰 전용 전략 → OrderIntent 0 (실주문 하드가드)."""
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("macross", _SellScanner())
    orch._observe_only = {"macross"}
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    assert intents == []                    # 실주문 없음
    assert ("macross", "SOLUSDT") not in orch._live_entered  # 진입 마킹도 안 함


def test_observe_only_still_emits_strategy_evaluated_entry():
    """관찰 전용이라도 would-enter 신호는 strategy_evaluated 로 기록 (데이터 수집)."""
    events = []
    orch = AsyncStrategyOrchestrator(
        Policy(policy_version=1, name="t"),
        wal_observer=lambda ev: events.append(ev),
    )
    orch.register_strategy("macross", _SellScanner())
    orch._observe_only = {"macross"}
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    entry_evs = [
        e for e in events
        if e.event_type == "strategy_evaluated"
        and e.payload.get("decision") == "sell"
        and e.payload.get("strategy_id") == "macross"
    ]
    assert len(entry_evs) == 1
    # observe 표식이 reason 에 붙어 skip store 가 "관찰 진입포착" 으로 분류.
    assert "observe" in entry_evs[0].payload.get("reason", "")


def test_observe_only_no_alert_callback():
    """관찰 전용은 실진입이 아니므로 _on_live_entry(텔레그램) 미발동."""
    fired = []
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch._on_live_entry = lambda sid, sym, side: fired.append((sid, sym, side))
    orch.register_strategy("macross", _SellScanner())
    orch._observe_only = {"macross"}
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    assert fired == []                       # 알림 없음 (실진입 아님)


def test_non_observe_strategy_trades_normally():
    """관찰 집합에 없는 전략은 정상 진입 (레거시 보존)."""
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("macross", _SellScanner())
    # _observe_only 비어있음 (기본)
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    sol = [i for i in intents if i.symbol == "SOLUSDT"]
    assert len(sol) == 1
    assert sol[0].side == "sell"


def test_observe_only_entry_flows_to_macross_store(tmp_path):
    """E2E: observe_only 진입 신호가 wal_observer fan-out → MacrossSignalStore
    파일까지 실제로 기록된다 (2026-07-08 회귀 방지).

    #527 은 orchestrator emit·store ingest 를 각각 배선했으나, 프로덕션에서
    yaml orchestrator 의 _wal_observer 가 None 이라 emit 이 None 가드에 막혀
    수집 0 이었다(live_run._on_orchestrator_ready 에서 배선 누락). 이 테스트는
    orchestrator(_wal_observer 배선됨) → store.ingest → 파일 기록 전 사슬을 검증.
    """
    from src.dashboard.macross_signal_store import MacrossSignalStore

    store = MacrossSignalStore(tmp_path / "skipped_signals.jsonl")
    orch = AsyncStrategyOrchestrator(
        Policy(policy_version=1, name="t"),
        wal_observer=lambda ev: store.ingest(ev.event_type, ev.payload or {}),
    )
    # store 는 sid == "live-macross-regime-v1" 만 수집한다.
    orch.register_strategy("live-macross-regime-v1", _SellScanner())
    orch._observe_only = {"live-macross-regime-v1"}
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))

    rows = store.recent()
    entries = [r for r in rows if r.get("kind") == "entry"]
    assert len(entries) == 1, f"진입 신호가 store 에 기록돼야 함, got {rows}"
    assert entries[0]["decision"] == "sell"
    assert entries[0]["symbol"] == "SOLUSDT"


def test_observe_entry_carries_price_and_sl_tp(tmp_path):
    """관찰 진입 신호가 진입가 + SL/TP 라인까지 store 에 실려야 한다 (2026-07-09).

    데드크로스 관찰 신호를 대시보드가 "실제 진입" 뷰와 동일 형식(진입가/SL/TP)으로
    표시하려면, orchestrator 가 진입가(마지막 종가)를 실어보내고 store 가 SL/TP 를
    계산해 기록해야 한다.
    """
    from src.dashboard.macross_signal_store import MacrossSignalStore, _SL_PCT, _TP_PCT

    store = MacrossSignalStore(tmp_path / "sig.jsonl")
    orch = AsyncStrategyOrchestrator(
        Policy(policy_version=1, name="t"),
        wal_observer=lambda ev: store.ingest(ev.event_type, ev.payload or {}),
    )
    orch.register_strategy("live-macross-regime-v1", _SellScanner())
    orch._observe_only = {"live-macross-regime-v1"}
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))

    rows = store.recent()
    assert len(rows) == 1
    r = rows[0]
    entry = r["entry_price"]
    assert entry is not None and entry > 0          # 진입가 실림
    # 숏: SL 은 진입가 위(+2%), TP 는 아래(−12%).
    assert r["sl_price"] == round(entry * (1 + _SL_PCT), 8)
    assert r["tp_price"] == round(entry * (1 - _TP_PCT), 8)
    assert r["sl_price"] > entry > r["tp_price"]


def test_macross_store_records_entry_only_not_hold(tmp_path):
    """진입(buy/sell) 신호만 수집 — hold/스킵은 노이즈라 기록 안 함 (2026-07-08).

    사용자 요구: 대시보드엔 "실제로 진입했을 신호"만. orchestrator 가 hold reason 을
    action_hold 로 덮어써 스킵 분류가 불가하므로 hold 는 아예 수집하지 않는다.
    """
    from src.dashboard.macross_signal_store import MacrossSignalStore

    store = MacrossSignalStore(tmp_path / "sig.jsonl")
    sid = "live-macross-regime-v1"
    # hold(스킵/no_cross 잡음) → 기록 안 됨
    store.ingest("strategy_evaluated",
                 {"strategy_id": sid, "symbol": "BTCUSDT",
                  "decision": "hold", "reason": "action_hold"})
    # buy/sell(진입) → 기록됨
    store.ingest("strategy_evaluated",
                 {"strategy_id": sid, "symbol": "ETHUSDT",
                  "decision": "sell", "reason": "death_short|observe"})

    rows = store.recent()
    assert len(rows) == 1, f"진입 1건만 남아야 함, got {rows}"
    assert rows[0]["kind"] == "entry"
    assert rows[0]["symbol"] == "ETHUSDT"


def test_observe_only_mixed_roster():
    """관찰(macross)+실매매(capit) 혼재 — capit 만 주문, macross 는 기록만."""
    class _BuyScanner(LiveScannerMixin):
        async def on_bar(self, ctx) -> Signal:
            return Signal(action="buy", size=0.05, reason="long")

    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("capit", _BuyScanner())
    orch.register_strategy("macross", _SellScanner())
    orch._observe_only = {"macross"}
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    sids = {i.strategy_id for i in intents}
    assert sids == {"capit"}                 # capit 만 실주문, macross 는 제외
