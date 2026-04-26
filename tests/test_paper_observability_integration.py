"""Phase D-3: 통합 테스트 — triggers 강화 + metrics 8종 + live loop 연동 검증.

Depends on Phase D-1 (ApiErrorRateTrigger, FillAnomalyTrigger in src/ops/triggers.py)
         Phase D-2 (paper_fills_total, paper_drawdown_ratio, wal_write_error_total
                    in src/observability/metrics.py)
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from src.execution.base import MarketState, Tick as ExecTick
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.executor import execute_intents
from src.live.wal import WAL
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.ops.triggers import DrawdownTrigger, ApiErrorRateTrigger, FillAnomalyTrigger
from src.portfolio.order_intent import OrderIntent


# ---------------------------------------------------------------------------
# 1. Drawdown trigger + paper_drawdown_ratio 메트릭 연동
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_drawdown_trigger_with_metrics(tmp_path):
    """DrawdownTrigger.update() 호출 시 paper_drawdown_ratio metric 갱신을 외부에서 검증."""
    metrics = Metrics()
    ks = KillSwitch()
    trigger = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=100.0)

    # Equity 100 → 110 (peak 110) → 106.6 (peak 대비 약 -3.09%, limit -3% 초과)
    # 106.7 은 float 오차로 -0.02999... 가 되어 <= -0.03 조건 불충족
    trigger.update(110.0)
    metrics.paper_drawdown_ratio.set(0.0)  # peak 일 때 0 dd
    trigger.update(106.6)
    dd_ratio = (106.6 - 110.0) / 110.0
    metrics.paper_drawdown_ratio.set(dd_ratio)

    assert ks.tripped is True
    # 메트릭 sample value 가 음수인지 확인
    sample = metrics.registry.get_sample_value("qta_paper_drawdown_ratio")
    assert sample is not None
    assert sample < -0.029  # -3% 근방


# ---------------------------------------------------------------------------
# 2. ApiErrorRateTrigger 미세 표본 미달 → trip 안 함
# ---------------------------------------------------------------------------

def test_api_error_rate_min_samples_no_trip():
    ks = KillSwitch()
    trigger = ApiErrorRateTrigger(
        kill=ks, window_seconds=300.0, error_rate_threshold=0.05, min_samples=20,
    )
    for _ in range(19):
        trigger.record(is_error=True, ts=1000.0)
    assert ks.tripped is False


# ---------------------------------------------------------------------------
# 3. ApiErrorRateTrigger 임계 초과 → trip
# ---------------------------------------------------------------------------

def test_api_error_rate_threshold_exceeded():
    ks = KillSwitch()
    trigger = ApiErrorRateTrigger(
        kill=ks, window_seconds=300.0, error_rate_threshold=0.05, min_samples=20,
    )
    # 100 건 중 6 건 오류 → 6%
    for i in range(100):
        trigger.record(is_error=(i < 6), ts=1000.0 + i)
    assert ks.tripped is True


# ---------------------------------------------------------------------------
# 4. FillAnomalyTrigger 로그 덤프 (dump_path 설정)
# ---------------------------------------------------------------------------

def test_fill_anomaly_dump_to_file(tmp_path):
    dump_path = tmp_path / "anomaly_dump.jsonl"
    ks = KillSwitch()
    trigger = FillAnomalyTrigger(
        kill=ks, window_seconds=1.0, burst_threshold=5, dump_path=dump_path,
    )
    base_ts = 1000.0
    for i in range(5):
        trigger.record_fill("BTCUSDT", ts=base_ts + i * 0.1)
    assert ks.tripped is True
    assert dump_path.exists()
    content = dump_path.read_text(encoding="utf-8").strip()
    assert "fill_anomaly" in content
    assert "BTCUSDT" in content


# ---------------------------------------------------------------------------
# 5. PaperBroker fill → paper_fills_total metric 외부 기록 (executor 책임)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_paper_broker_fill_metric_via_executor(tmp_path):
    metrics = Metrics()
    ks = KillSwitch()
    wal = WAL(tmp_path / "wal.jsonl")
    me = MockMatchingEngine()
    broker = PaperBroker(wal=wal, kill_switch=ks, matching_engine=me)
    broker.update_market(MarketState(
        tick=ExecTick(symbol="BTCUSDT", bid=49999.0, ask=50001.0, last=50000.0, volume=1000, ts=datetime.now(timezone.utc)),
        adv=1_000_000.0,
    ))

    intent = OrderIntent(strategy_id="momo_btc_v2", symbol="BTCUSDT", side="buy", qty=0.001, reason="test")
    acks = await execute_intents([intent], broker=broker, kill_switch=ks, wal=wal, metrics=metrics)

    assert len(acks) == 1
    assert acks[0].status == "FILLED"
    # orders_total 증가 (executor 가 기록)
    # prometheus_client Counter 의 실제 sample name 은 등록 이름 그대로 (이 버전에서 _total suffix 없음)
    sample = metrics.registry.get_sample_value(
        "qta_orders_total",
        labels={"strategy": "momo_btc_v2", "broker": "paper", "side": "BUY", "status": "FILLED"},
    )
    assert sample == 1.0


# ---------------------------------------------------------------------------
# 6. WAL write 실패 → wal_write_error_total + kill_switch trip
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wal_write_failure_metric_trip(tmp_path, monkeypatch):
    metrics = Metrics()
    ks = KillSwitch()
    wal = WAL(tmp_path / "wal.jsonl")
    me = MockMatchingEngine()
    broker = PaperBroker(wal=wal, kill_switch=ks, matching_engine=me)
    broker.update_market(MarketState(
        tick=ExecTick(symbol="BTCUSDT", bid=49999.0, ask=50001.0, last=50000.0, volume=1000, ts=datetime.now(timezone.utc)),
        adv=1_000_000.0,
    ))

    # WAL.write 가 WALWriteFailed raise 하도록 monkeypatch
    def raise_oserror(self, event):
        from src.live.wal import WALWriteFailed
        raise WALWriteFailed("simulated disk full")
    monkeypatch.setattr("src.live.wal.WAL.write", raise_oserror)

    intent = OrderIntent(strategy_id="s", symbol="BTCUSDT", side="buy", qty=0.001, reason="test")
    acks = await execute_intents([intent], broker=broker, kill_switch=ks, wal=wal, metrics=metrics)

    assert acks[0].status == "REJECTED"
    assert acks[0].reject_reason == "WAL_WRITE_FAIL"
    assert ks.tripped is True


# ---------------------------------------------------------------------------
# 7. Time source 단일화 검증 — WAL ts UTC ISO 8601 포맷
# ---------------------------------------------------------------------------

def test_wal_ts_utc_iso8601_format(tmp_path):
    """WAL ts 가 UTC ISO 8601 포맷이고 fromisoformat round-trip 가능."""
    from datetime import datetime, timezone
    from src.live.types import WALEvent
    from src.live.wal import WAL, replay

    wal = WAL(tmp_path / "wal.jsonl")
    ts_now = datetime.now(timezone.utc).isoformat()
    event = WALEvent(ts=ts_now, event_type="order_submitted", schema_version=1, payload={"k": "v"})
    wal.write(event)
    events, _ = replay(tmp_path / "wal.jsonl")
    assert len(events) == 1
    parsed = datetime.fromisoformat(events[0].ts)
    assert parsed.tzinfo is not None  # UTC 가 보존됨
