"""Regression: 거래 정지 → 시작 토글 시 cross-run replay 가 두 번째 호출에서 SKIP 되어야 함.

Root cause (2026-05-21):
  ``_run_pipeline_attached`` 가 매 호출마다 ``replay_from_wal_dir`` 를 무조건
  실행. ``pnl_aggregator`` / ``position_store`` 는 ``_serve()`` 의 싱글톤이라
  첫 호출 후엔 이미 정확한 상태. 두 번째 호출의 replay 가 모든 fill 을 또
  더해서 realized PnL 누적 + position qty 인플레이션 → risk manager 가
  부풀린 qty 로 stop 발사. 실측 증거: PnL 78→218 (3 사이클), NEAR 133→399
  (3x), ZEC 0.343→1.029 (3x).

본 테스트는 두 번째 호출에서 ``replay_from_wal_dir`` 가 *호출되지 않음*
을 명시 검증.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


class _FakeStore:
    def __init__(self, warm_after_first: bool = True) -> None:
        self._warm = False
        self._warm_after_first = warm_after_first
        self.replay_calls = 0

    def all_positions(self) -> dict:
        return {"sid-a": [("BTCUSDT", 0.5)]} if self._warm else {}

    def replay_from_wal_dir(self, log_dir) -> int:
        self.replay_calls += 1
        if self._warm_after_first:
            self._warm = True
        return 7

    def replay_from_wal(self, *_a, **_kw) -> None:
        pass


class _FakeAggregator:
    def __init__(self, warm_after_first: bool = True) -> None:
        self._cum = 0.0
        self._warm_after_first = warm_after_first
        self.replay_calls = 0

    @property
    def realtime(self) -> float:
        return self._cum

    def replay_from_wal_dir(self, log_dir) -> int:
        self.replay_calls += 1
        if self._warm_after_first:
            self._cum = 78.0  # mimics realized PnL after first replay
        return 7

    def replay_from_wal(self, *_a, **_kw) -> None:
        pass


def _build_minimal_state_and_config(tmp_path: Path):
    state = MagicMock()
    state.timeline_broker = None
    state.account_info_provider = None

    config = MagicMock()
    config.wal_path = tmp_path / "run1" / "wal.jsonl"
    return state, config


@pytest.mark.asyncio
async def test_warm_singleton_skips_replay_on_second_call(monkeypatch, tmp_path):
    """첫 호출은 replay 실행, 두 번째 호출 (= 정지 후 다시 거래 시작) 은 SKIP."""
    from scripts import live_run

    # run_shadow_loop 가 실제 broker / network 안 타게 즉시 반환하는 stub.
    async def _noop_loop(*args, **kwargs):
        return None
    monkeypatch.setattr(live_run, "run_shadow_loop", _noop_loop)

    # balance provider 와이어링도 외부 의존 회피.
    monkeypatch.setattr(live_run, "_wire_balance_provider", lambda *a, **kw: None)

    store = _FakeStore(warm_after_first=True)
    agg = _FakeAggregator(warm_after_first=True)

    import logging
    logger = logging.getLogger("test")

    # 첫 호출 — 싱글톤 cold → replay 한 번 실행되어야 함.
    state, config = _build_minimal_state_and_config(tmp_path)
    await live_run._run_pipeline_attached(
        state, config, None, logger, 0.0,
        position_store=store, pnl_aggregator=agg,
    )
    assert store.replay_calls == 1, "first call must perform cross-run replay"
    assert agg.replay_calls == 1

    # 두 번째 호출 — 싱글톤 warm → replay 0 회 추가 (= total 1 그대로).
    state, config = _build_minimal_state_and_config(tmp_path)
    await live_run._run_pipeline_attached(
        state, config, None, logger, 0.0,
        position_store=store, pnl_aggregator=agg,
    )
    assert store.replay_calls == 1, (
        "second call must SKIP replay (warm guard) — was called "
        f"{store.replay_calls} times"
    )
    assert agg.replay_calls == 1, (
        "second call must SKIP replay (warm guard) — was called "
        f"{agg.replay_calls} times"
    )

    # 세 번째 호출도 SKIP 유지 — 사용자가 정지/시작 여러 번 토글해도 안전.
    state, config = _build_minimal_state_and_config(tmp_path)
    await live_run._run_pipeline_attached(
        state, config, None, logger, 0.0,
        position_store=store, pnl_aggregator=agg,
    )
    assert store.replay_calls == 1
    assert agg.replay_calls == 1


@pytest.mark.asyncio
async def test_cold_singleton_still_replays_on_first_call(monkeypatch, tmp_path):
    """qta.exe 첫 부팅 = singleton cold → replay 정상 실행 (regression 회피)."""
    from scripts import live_run

    async def _noop_loop(*args, **kwargs):
        return None
    monkeypatch.setattr(live_run, "run_shadow_loop", _noop_loop)
    monkeypatch.setattr(live_run, "_wire_balance_provider", lambda *a, **kw: None)

    # replay 후에도 cold 상태 유지하는 fake (이전 run 의 WAL 가 비어있어 fill 0
    # 인 케이스 시뮬레이션) — 그래도 첫 호출은 replay 시도해야 함.
    store = _FakeStore(warm_after_first=False)
    agg = _FakeAggregator(warm_after_first=False)

    import logging
    logger = logging.getLogger("test")

    state, config = _build_minimal_state_and_config(tmp_path)
    await live_run._run_pipeline_attached(
        state, config, None, logger, 0.0,
        position_store=store, pnl_aggregator=agg,
    )
    assert store.replay_calls == 1
    assert agg.replay_calls == 1
