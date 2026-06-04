"""Regression — cross-run replay 가 disabled 전략의 옛 fill 을 무시.

사고 (2026-06-05 BEATUSDT / TRXUSDT):
  1. 옛 cand-c-* 전략의 buy/sell fill 이 logs/live/*/wal.jsonl 에 누적
  2. production.yaml 에서 그 전략을 commented 처리 (disabled)
  3. 재시작 시 `replay_from_wal_dir` 가 모든 WAL 의 fill 을 복원 →
     position_store 에 옛 cand-c-* 의 net_qty 가 살아남
  4. LivePositionRiskManager 가 그 부풀린 qty 로 청산 발주 →
     broker over-shoot → LONG / SHORT 뒤집기 (실 -41 USDT 손실)

Fix: `replay_from_wal*` 에 ``allowed_strategy_ids: set[str] | None`` 추가.
  - None (default) = 모든 sid 복원 (byte-identical, 기존 동작)
  - set = 그 set 의 sid 만 복원, 외 sid 의 ack/fill 은 skip

본 테스트:
  1. None → 기존 동작 byte-identical (disabled 전략 fill 도 복원)
  2. set 주어지면 그 set 외 sid 의 fill skip
  3. 같은 동작이 position_store + pnl_aggregator 양쪽에서 일관
  4. live_run.py 의 _active_strategy_ids 가 production.yaml 의 active id set 만 반환
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))

from src.live.pnl_aggregator import PnLAggregator
from src.live.strategy_position_store import StrategyPositionStore


# ── helper: WAL 1개 생성 (disabled 전략 + active 전략 fill 섞어서) ────────

def _write_wal(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _fill(sid: str, sym: str, side: str, qty: float, px: float,
          ts: str = "2026-06-04T12:00:00+00:00") -> dict:
    return {
        "ts": ts,
        "event_type": "order_filled",
        "schema_version": 1,
        "payload": {
            "strategy_id": sid,
            "symbol": sym,
            "side": side,
            "fill_qty": qty,
            "fill_price": px,
            "client_order_id": f"{sid}-{sym}-{side}-{int(qty*1000)}",
            "fee": 0.0,
            "ts": ts,
        },
    }


# ── position_store ──────────────────────────────────────────────────────────

def test_store_none_filter_keeps_all_sids_byte_identical(tmp_path):
    """allowed_strategy_ids=None → 모든 sid 복원 (기존 동작)."""
    wal = tmp_path / "run1" / "wal.jsonl"
    _write_wal(wal, [
        _fill("active-strategy", "BTCUSDT", "buy", 1.0, 50000.0),
        _fill("disabled-cand-c-2026-05-20-foo", "TRXUSDT", "buy", 1000.0, 0.3),
    ])
    store = StrategyPositionStore()
    store.replay_from_wal(wal)  # None default
    positions = store.all_positions()
    sids = set(positions.keys())
    assert "active-strategy" in sids
    assert "disabled-cand-c-2026-05-20-foo" in sids, (
        "None filter 면 disabled 전략 fill 도 복원돼야 byte-identical 가드 충족"
    )


def test_store_allowed_set_skips_disabled_fills(tmp_path):
    """allowed_strategy_ids={...} → 그 set 외 sid 는 skip — 본 PR 핵심 fix."""
    wal = tmp_path / "run1" / "wal.jsonl"
    _write_wal(wal, [
        _fill("active-strategy", "BTCUSDT", "buy", 1.0, 50000.0),
        _fill("disabled-cand-c-2026-05-20-foo", "TRXUSDT", "buy", 1000.0, 0.3),
        _fill("disabled-cand-c-2026-05-20-foo", "BEATUSDT", "sell", 500.0, 1.5),
    ])
    store = StrategyPositionStore()
    store.replay_from_wal(
        wal, allowed_strategy_ids={"active-strategy"},
    )
    positions = store.all_positions()
    sids = set(positions.keys())
    assert "active-strategy" in sids
    assert "disabled-cand-c-2026-05-20-foo" not in sids, (
        "disabled 전략의 fill 은 store 에 안 들어가야 함 (BEATUSDT/TRX 사고 차단)"
    )


def test_store_replay_dir_propagates_filter(tmp_path):
    """replay_from_wal_dir → replay_from_wal 으로 filter 전달."""
    (tmp_path / "logs" / "live").mkdir(parents=True, exist_ok=True)
    wal1 = tmp_path / "logs" / "live" / "run1" / "wal.jsonl"
    wal2 = tmp_path / "logs" / "live" / "run2" / "wal.jsonl"
    _write_wal(wal1, [_fill("active", "BTCUSDT", "buy", 1.0, 50000.0)])
    _write_wal(wal2, [_fill("disabled-cand-c", "ETHUSDT", "buy", 5.0, 3000.0)])

    store = StrategyPositionStore()
    n = store.replay_from_wal_dir(
        tmp_path / "logs" / "live", allowed_strategy_ids={"active"},
    )
    assert n == 2  # 두 WAL 다 scan
    sids = set(store.all_positions().keys())
    assert sids == {"active"}, f"filter 가 dir replay 에 안 전파됨: got {sids}"


# ── pnl_aggregator (같은 패턴) ──────────────────────────────────────────────

def test_pnl_none_filter_keeps_all_sids(tmp_path):
    """PnL aggregator 도 None → byte-identical."""
    wal = tmp_path / "run1" / "wal.jsonl"
    _write_wal(wal, [
        _fill("active", "BTCUSDT", "buy", 1.0, 50000.0),
        _fill("active", "BTCUSDT", "sell", 1.0, 51000.0),  # +1000 realized
        _fill("disabled", "ETHUSDT", "buy", 1.0, 3000.0),
        _fill("disabled", "ETHUSDT", "sell", 1.0, 3100.0),  # +100 realized
    ])
    agg = PnLAggregator()
    agg.replay_from_wal(wal)
    assert agg.realtime > 0, "양쪽 sid realized 모두 누적"


def test_pnl_allowed_set_skips_disabled(tmp_path):
    """PnL aggregator filter — disabled 의 realized 가 통계에서 빠짐."""
    wal = tmp_path / "run1" / "wal.jsonl"
    _write_wal(wal, [
        _fill("active", "BTCUSDT", "buy", 1.0, 50000.0),
        _fill("active", "BTCUSDT", "sell", 1.0, 51000.0),
        _fill("disabled", "ETHUSDT", "buy", 1.0, 3000.0),
        _fill("disabled", "ETHUSDT", "sell", 1.0, 3100.0),
    ])
    agg_filtered = PnLAggregator()
    agg_filtered.replay_from_wal(wal, allowed_strategy_ids={"active"})

    agg_all = PnLAggregator()
    agg_all.replay_from_wal(wal)

    assert agg_filtered.realtime != agg_all.realtime, (
        "filter 가 PnL aggregator 에 적용 안 됨 — 같은 결과 나옴"
    )


# ── live_run.py 의 _active_strategy_ids helper ─────────────────────────────

def test_active_strategy_ids_yaml_parse(tmp_path):
    """production.yaml 의 active (uncommented) id 만 set 으로 반환."""
    yaml_path = tmp_path / "production.yaml"
    yaml_path.write_text("""
strategies:
  - id: active-one
    class: foo.Bar
    kwargs: {}
  # - id: disabled-cand-c
  #   class: baz.Qux
  - id: active-two
    class: foo.Two
""", encoding="utf-8")
    # import via scripts/live_run helper
    import importlib.util as _u
    spec = _u.spec_from_file_location(
        "_live_run_test_mod", str(_REPO / "scripts" / "live_run.py"),
    )
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)

    sids = mod._active_strategy_ids(yaml_path)
    assert sids == {"active-one", "active-two"}, (
        f"production.yaml 의 commented entry 가 active set 에 들어옴: {sids}"
    )


def test_active_strategy_ids_missing_file_returns_empty():
    """파일 없으면 빈 set (caller 가 None 으로 fallback)."""
    import importlib.util as _u
    spec = _u.spec_from_file_location(
        "_live_run_test_mod2", str(_REPO / "scripts" / "live_run.py"),
    )
    mod = _u.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sids = mod._active_strategy_ids(Path("/nonexistent/path.yaml"))
    assert sids == set()
