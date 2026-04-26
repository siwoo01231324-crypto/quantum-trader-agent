"""Phase E-3: 롤백 트리거 3종 injection 테스트.

시나리오:
1. WS 재연결 실패 2회 → kill-switch trip
2. 체결 누락 1건 → reconciler 감지
3. Sharpe 괴리 > 0.5 → shadow_report fail
4. DrawdownTrigger injection
5. ApiErrorRateTrigger injection
6. FillAnomalyTrigger injection
7. Exit Criteria 자동 검증 통합
"""
from __future__ import annotations

import asyncio
import pytest
from datetime import datetime, timezone, date
from decimal import Decimal
from pathlib import Path

import pandas as pd

from src.live.types import WALEvent
from src.live.wal import WAL, replay
from src.ops.kill_switch import KillSwitch
from src.ops.triggers import DrawdownTrigger, ApiErrorRateTrigger, FillAnomalyTrigger
from scripts.shadow_report import (
    compare_sharpe,
    CompareConditions,
    verify_exit_criteria,
    FillRecord,
)


# ---------------------------------------------------------------------------
# Scenario 1: WS 재연결 실패 2회 → kill-switch trip (수동 시뮬)
# ---------------------------------------------------------------------------

def test_ws_reconnect_failure_triggers_kill_switch():
    """WS 단절 2회 시 운영자가 kill_switch.trip 을 수동/자동 호출하는 시나리오."""
    ks = KillSwitch()
    fail_count = 0
    for _ in range(2):
        fail_count += 1
        if fail_count >= 2:
            ks.trip(reason=f"ws_reconnect_failed x{fail_count}", source="auto:ws")
    assert ks.tripped is True
    last = ks.last_event()
    assert last is not None
    assert "ws" in last.source


# ---------------------------------------------------------------------------
# Scenario 2: 체결 누락 1건 → reconciler 가 감지
# ---------------------------------------------------------------------------

def test_fill_missing_detected_by_replay(tmp_path):
    """WAL 에 order_submitted 만 있고 order_filled 없는 경우 — 누락으로 감지."""
    wal_path = tmp_path / "wal.jsonl"
    wal = WAL(wal_path)

    # order_submitted 만 기록 (fill 없음)
    wal.write(WALEvent(
        ts=datetime.now(timezone.utc).isoformat(),
        event_type="order_submitted",
        schema_version=1,
        payload={
            "client_order_id": "abc",
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "0.001",
            "strategy_id": "test",
        },
    ))
    # order_filled 누락

    events, _ = replay(wal_path)
    submitted_ids = {e.payload["client_order_id"] for e in events if e.event_type == "order_submitted"}
    filled_ids = {e.payload["client_order_id"] for e in events if e.event_type == "order_filled"}
    missing = submitted_ids - filled_ids
    assert "abc" in missing  # 누락 감지


# ---------------------------------------------------------------------------
# Scenario 3: Sharpe 괴리 > 0.5 → shadow_report 가 fail 처리
# ---------------------------------------------------------------------------

def test_sharpe_divergence_fails_report():
    """shadow_report.compare_sharpe 가 threshold 초과 시 passed=False."""
    # shadow returns: 일정한 양의 평균
    shadow = pd.Series([0.01, 0.02, 0.015, 0.018, 0.022])
    # backtest returns: 음의 평균 (Sharpe 큰 차이)
    backtest = pd.Series([-0.01, -0.02, -0.015, -0.018, -0.022])

    cond = CompareConditions(
        data_source="binance_futures_usdtm",
        slippage_model="zero_slip",
        taker_fee_bps=5.0,
        sizing_method="resolve_size_v1",
    )
    result = compare_sharpe(shadow, backtest, cond, cond, threshold=0.5)
    assert result["passed"] is False
    assert result["diff"] > 0.5


# ---------------------------------------------------------------------------
# Scenario 1b: DrawdownTrigger injection (peak tracking)
# ---------------------------------------------------------------------------

def test_drawdown_injection_trips():
    """현실 시나리오: equity 100 → 110 (peak) → 95 (peak 대비 -13.6%, limit -3% 초과) → trip."""
    ks = KillSwitch()
    trigger = DrawdownTrigger(kill=ks, limit=-0.03, starting_equity=100.0)
    trigger.update(110.0)  # peak
    trigger.update(95.0)   # peak 대비 -13.6%
    assert ks.tripped is True


# ---------------------------------------------------------------------------
# Scenario 2b: API 오류율 injection
# ---------------------------------------------------------------------------

def test_api_error_rate_injection_trips():
    """100건 중 6건 오류 (6%) → trip."""
    ks = KillSwitch()
    trigger = ApiErrorRateTrigger(kill=ks, error_rate_threshold=0.05, min_samples=20)
    base_ts = 1000.0
    for i in range(100):
        trigger.record(is_error=(i < 6), ts=base_ts + i)
    assert ks.tripped is True


# ---------------------------------------------------------------------------
# Scenario 3b: 이상 체결 패턴 injection
# ---------------------------------------------------------------------------

def test_fill_anomaly_injection_trips(tmp_path):
    """1초 내 동일 심볼 5건 → trip + 로그 덤프 파일 생성."""
    dump = tmp_path / "anomaly.jsonl"
    ks = KillSwitch()
    trigger = FillAnomalyTrigger(kill=ks, window_seconds=1.0, burst_threshold=5, dump_path=dump)
    for i in range(5):
        trigger.record_fill("BTCUSDT", ts=1000.0 + i * 0.1)
    assert ks.tripped is True
    assert dump.exists()
    content = dump.read_text(encoding="utf-8")
    assert "BTCUSDT" in content


# ---------------------------------------------------------------------------
# Exit Criteria 자동 검증 통합 테스트
# ---------------------------------------------------------------------------

def test_verify_exit_criteria_partial(tmp_path):
    """5종 항목 중 일부 미달 → 정확한 dict 반환."""
    fills = [FillRecord(
        ts=datetime.now(timezone.utc),
        strategy_id="s",
        symbol="BTCUSDT",
        side="BUY",
        qty=Decimal("0.001"),
        price=Decimal("50000"),
        fees=Decimal("0.025"),
        fee_asset="USDT",
    )]
    pnl = pd.Series([0.01], index=pd.to_datetime([date(2026, 4, 26)]))
    result = verify_exit_criteria(
        fills,
        pnl,
        sharpe_compare_passed=False,   # Sharpe 미달
        ws_reconnect_count=1,          # 통과
        lag_over_500ms_ratio=0.03,     # 통과
        kill_switch_tests_passed=True,
    )
    assert result["WS 단절 자동 재연결 정상 (≥1회)"] is True
    assert result["시세 lag > 500ms 발생률 < 5%"] is True
    assert result["백테스트 Sharpe vs Shadow Sharpe 차이 ≤ 0.3"] is False
    assert result["kill-switch 자동 트리거 3종 테스트 통과"] is True
