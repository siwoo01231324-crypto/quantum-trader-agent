"""End-to-end smoke — airborne 매매 통로가 진짜 뚫려있는지.

PR #336/#337 의 회귀 두 건 (args._orchestrator NameError, _klines_to_dataframe
1h collapse) 은 **단위 테스트는 다 통과** 했지만 거래 0건이었다. 각 단위는
정상이고 wiring 의 한 노드가 깨졌기 때문. unit 으로는 못 잡는 path 회귀를
다음 노드들이 한 줄로 작동하는지 확인:

  ① fetch_universe_klines 가 1h 봉의 distinct index 보존 (v0.6.16 fix)
  ② LiveScannerMixin per-symbol dispatch (orchestrator.run_bar)
  ③ get_universe() 필터 (#337 Phase 3) 통과
  ④ LiveAirborneBbReversalKstHours 의 KST {7,8,16,20,22}시 gate (v2)
  ⑤ BB-reversal long signal emit → OrderIntent 생성

KST gate 시각이 아니면 hold, 맞으면 BUY 가 나오는 두 방향 검증.

KST 게이트 시각 안 기다리고 즉시 통로 검증 — sliding window 검증과 별개로
다음 회귀를 미리 잡는다.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours,
)
from portfolio._async_orchestrator import AsyncStrategyOrchestrator
from risk.dsl import Policy
from src.brokers.binance.universe_quote import _klines_to_dataframe


# ──────────────────────────────────────────────────────────────────────────
# Fixtures — 1h kline rows 와 BB-reversal long fire 패턴
# ──────────────────────────────────────────────────────────────────────────

def _binance_1h_rows(last_utc_iso: str, n_bars: int = 60) -> list[list]:
    """Binance /klines payload 모양 (12-tuple/row) — 1h, n_bars.

    마지막 3봉이 v1.2 airborne BB-reversal long fire 패턴:
      -3봉: lower-low close (BB lower 터치)
      -2봉: 더 낮은 lower-low (확장)
      -1봉: reversal close (그 직전 lower-low 봉 위로 강하게 close — long fire)
    """
    n = n_bars
    closes = np.linspace(100.0, 102.0, n).copy()
    opens = closes.copy()
    highs = closes + 0.5
    lows = closes - 0.5
    closes[-3], opens[-3], highs[-3], lows[-3] = 85.0, 102.0, 102.2, 84.0
    closes[-2], opens[-2], highs[-2], lows[-2] = 81.0, 85.0, 85.5, 80.0
    closes[-1], opens[-1], highs[-1], lows[-1] = 95.0, 81.0, 95.5, 80.0

    last_ms = int(pd.Timestamp(last_utc_iso, tz="UTC").value // 10**6)
    rows: list[list] = []
    for i in range(n):
        open_ms = last_ms - (n - 1 - i) * 3_600_000
        rows.append([
            open_ms,
            f"{opens[i]:.4f}",
            f"{highs[i]:.4f}",
            f"{lows[i]:.4f}",
            f"{closes[i]:.4f}",
            "10.0",
            open_ms + 3_599_999,
            "1000.0",
            42,
            "5.0",
            "500.0",
            "0",
        ])
    return rows


# ──────────────────────────────────────────────────────────────────────────
# Step ① — _klines_to_dataframe(1h) 가 distinct index 보존
# ──────────────────────────────────────────────────────────────────────────

def test_step1_1h_klines_preserve_hourly_index():
    """전제 — v0.6.16 fix 가 적용돼 1h fetch 시 24봉이 collapse 안 됨."""
    # 같은 날짜 안에 24봉 — collapse 회귀 시 nunique=1
    rows = _binance_1h_rows("2026-05-30T23:00:00", n_bars=24)
    df = _klines_to_dataframe(rows, interval="1h")
    assert df.index.nunique() == 24, (
        f"1h history 가 daily 로 collapse 됨. v0.6.16 fix 미적용. "
        f"distinct ts = {df.index.nunique()}"
    )


# ──────────────────────────────────────────────────────────────────────────
# Step ②/③ — orchestrator dispatch + universe filter
# ──────────────────────────────────────────────────────────────────────────

def _build_snapshot(symbol: str, last_utc: str, equity_usdt: float = 1000.0) -> dict:
    """live SnapshotBuilder 가 만들어주던 형태와 동등한 minimal market_snapshot."""
    rows = _binance_1h_rows(last_utc, n_bars=60)
    history = _klines_to_dataframe(rows, interval="1h")
    return {
        "ohlcv_history": {symbol: history},
        "equity_usdt": equity_usdt,
        "equity_krw": 0.0,
        "factors": {},
        "universe_factors": {},
    }


def _new_orchestrator() -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(policy=Policy(policy_version=1, name="smoke"))


# ──────────────────────────────────────────────────────────────────────────
# Step ④/⑤ — KST gate ON 시각엔 BUY emit, OFF 시각엔 hold
# ──────────────────────────────────────────────────────────────────────────

# KST 16:00 = UTC 07:00 → gate v2 ∈ {7,8,16,20,22} 에 포함
_GATE_ON_LAST_UTC = "2026-05-30T07:00:00"
# KST 13:00 = UTC 04:00 → gate v2 미포함
_GATE_OFF_LAST_UTC = "2026-05-30T04:00:00"

# airborne 의 universe 안에 들어있는 symbol — top-30 의 BTCUSDT 는 항상 포함.
_SYMBOL = "BTCUSDT"


@pytest.mark.asyncio
async def test_e2e_gate_on_kst16_emits_buy():
    """KST 16시 (gate v2 ON) + BB long fire pattern → BUY 시그널 + OrderIntent."""
    strat = LiveAirborneBbReversalKstHours()
    orch = _new_orchestrator()
    orch.register_strategy("live-airborne-bb-reversal-kst-hours", strat)

    snap = _build_snapshot(_SYMBOL, _GATE_ON_LAST_UTC)
    ts = pd.Timestamp(_GATE_ON_LAST_UTC, tz="UTC")
    intents = await orch.run_bar(ts=ts, market_snapshot=snap)

    # 1) intents 가 비어있지 않아야 (path 가 broker intent 까지 갔다는 증거)
    assert intents, (
        "KST 16시 BB long fire 인데 OrderIntent 0건. wiring 의 한 노드가 깨짐.\n"
        "  - strategy 가 hold 반환 (시그널 path 깨짐)\n"
        "  - 또는 orchestrator dispatch 가 universe 필터에서 BTCUSDT 빠짐\n"
        "  - 또는 risk gate (orchestrator 의 quarantine 등) 가 차단\n"
        "어느 쪽이든 거래 0건 회귀가 다시 일어났다는 뜻."
    )

    # 2) BUY 액션이어야 (sell 이거나 다른 action 이면 패턴 자체가 무효)
    intent = intents[0]
    side = getattr(intent, "side", None) or getattr(intent, "action", None)
    assert str(side).lower() in {"buy", "long"}, (
        f"BB long fire 패턴인데 intent side={side!r}. 시그널 방향 회귀."
    )

    # 3) 본 strategy 가 emit 한 intent (다른 잡힌 strategy 가 아니라)
    sid = getattr(intent, "strategy_id", None)
    assert sid == "live-airborne-bb-reversal-kst-hours", (
        f"intent.strategy_id={sid!r} — 다른 전략이 잡았거나 wiring 잘못."
    )


@pytest.mark.asyncio
async def test_e2e_gate_off_kst13_returns_hold():
    """KST 13시 (gate OFF) — 같은 패턴이라도 진입 시각 아니므로 OrderIntent 없음."""
    strat = LiveAirborneBbReversalKstHours()
    orch = _new_orchestrator()
    orch.register_strategy("live-airborne-bb-reversal-kst-hours", strat)

    snap = _build_snapshot(_SYMBOL, _GATE_OFF_LAST_UTC)
    ts = pd.Timestamp(_GATE_OFF_LAST_UTC, tz="UTC")
    intents = await orch.run_bar(ts=ts, market_snapshot=snap)

    # 같은 패턴이지만 KST 13시 — gate 차단. 본 strategy 의 intent 0개.
    airborne_intents = [
        i for i in intents
        if getattr(i, "strategy_id", "") == "live-airborne-bb-reversal-kst-hours"
    ]
    assert not airborne_intents, (
        f"KST 13시 (gate OFF) 인데 airborne intent {len(airborne_intents)}건 발생. "
        f"KST hour gate 깨짐."
    )


@pytest.mark.asyncio
async def test_e2e_universe_filter_excludes_unlisted_symbol():
    """get_universe() 안에 없는 symbol 은 dispatch 안 됨 — #337 Phase 3 가드."""
    strat = LiveAirborneBbReversalKstHours()
    orch = _new_orchestrator()
    orch.register_strategy("live-airborne-bb-reversal-kst-hours", strat)

    # 가짜 symbol — top-100 에 절대 없는 종목. dispatch 가 필터하면 intent 0.
    fake = "NOTREALUSDT__"
    snap = _build_snapshot(fake, _GATE_ON_LAST_UTC)
    ts = pd.Timestamp(_GATE_ON_LAST_UTC, tz="UTC")
    intents = await orch.run_bar(ts=ts, market_snapshot=snap)

    airborne_intents = [
        i for i in intents
        if getattr(i, "strategy_id", "") == "live-airborne-bb-reversal-kst-hours"
    ]
    assert not airborne_intents, (
        f"get_universe() 에 없는 {fake!r} 가 dispatch 됐다. "
        f"Phase 3 universe filter 깨짐."
    )


# ──────────────────────────────────────────────────────────────────────────
# Step ⑥ — broker 까지 가는 통로 (orchestrator intent → execute_intents → broker.place_order)
#
# 본 step 은 live_run.py 의 `run_shadow_loop` 가 매 cycle 마다 하는 일을 in-process
# 로 동등하게 재현. PaperBroker 가 받아 FILLED ack 가 떨어지면, daemon FIRE 가
# 떨어졌을 때 실제로 testnet 으로 발주가 나갈 거란 강한 신호.
# ──────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_e2e_intent_to_broker_filled(tmp_path):
    """KST gate ON + fire pattern → broker.place_order → FILLED ack.

    intent → execute_intents → PaperBroker.place_order 까지의 모든 wiring 노드
    (kill_switch, conversion, idempotency key, latency 기록) 한 사이클로 검증.
    """
    from datetime import datetime as _dt, timezone as _tz

    from prometheus_client import CollectorRegistry

    from src.execution.base import MarketState, Tick
    from src.execution.mock_matching import MockMatchingEngine
    from src.execution.paper_broker import PaperBroker
    from src.live.executor import execute_intents
    from src.live.wal import WAL
    from src.observability.metrics import Metrics
    from src.ops.kill_switch import KillSwitch

    # 1) airborne strategy → orchestrator dispatch → OrderIntent 생성
    strat = LiveAirborneBbReversalKstHours()
    orch = _new_orchestrator()
    orch.register_strategy("live-airborne-bb-reversal-kst-hours", strat)
    snap = _build_snapshot(_SYMBOL, _GATE_ON_LAST_UTC)
    ts = pd.Timestamp(_GATE_ON_LAST_UTC, tz="UTC")
    intents = await orch.run_bar(ts=ts, market_snapshot=snap)
    assert intents, "step 1 — orchestrator intent 생성 단계가 비어있음"

    # 2) PaperBroker + market state — execute_intents 가 거치는 모든 의존성 셋업
    wal = WAL(tmp_path / "wal.jsonl")
    ks = KillSwitch()
    me = MockMatchingEngine()
    broker = PaperBroker(wal=wal, kill_switch=ks, matching_engine=me)
    broker.update_market(MarketState(
        tick=Tick(
            symbol=_SYMBOL,
            bid=95.0,
            ask=95.1,
            last=95.0,
            volume=1000,
            ts=_dt.now(_tz.utc),
        ),
        adv=1_000_000.0,
    ))
    metrics = Metrics(registry=CollectorRegistry())

    # 3) intent → execute_intents → broker.place_order → ack
    acks = await execute_intents(
        intents, broker=broker, kill_switch=ks, wal=wal, metrics=metrics,
    )

    # 4) ack 검증 — FILLED 면 broker 까지 거래 path 가 진짜 뚫림
    assert acks, "execute_intents 가 ack 0건 반환 — broker 호출 안 일어남."
    statuses = [a.status for a in acks]
    assert any(s == "FILLED" for s in statuses), (
        f"broker.place_order 까지 갔지만 FILLED ack 없음. statuses={statuses!r}. "
        f"conversion / kill_switch / matching 단계 회귀."
    )

    # 5) WAL 에 order_acked 기록 확인 — 운영 시 dashboard 가 이걸 읽음
    wal_text = (tmp_path / "wal.jsonl").read_text(encoding="utf-8")
    assert "order_acked" in wal_text or "order_filled" in wal_text, (
        "broker FILLED 인데 WAL 에 order 이벤트 0건. dashboard 거래이력 카드 "
        "비어있던 사고와 동일 회귀."
    )
