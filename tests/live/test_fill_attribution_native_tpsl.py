"""L1 회귀 — 거래소 네이티브 TP/SL 청산 fill 의 symbol/strategy 귀속.

근본버그 (2026-06-14 진단): Bitget 네이티브 TP/SL 청산은 우리가 coid 를 등록하지
않은 plan order 로 거래소가 발동한다. fill_consumer 는 symbol/side/strategy 를
in-memory coid 맵(``resolve_order_context``)으로만 복원했기에, 청산 fill 의 coid
가 맵에 없어 ``order_filled`` 를 ``symbol=""`` 로 기록 → ``StrategyPositionStore``
replay 가 빈 symbol 을 drop → **청산이 store 에서 차감 안 됨** → 숏이 매 run 누적
(3~8× 인플레이션, AVAX/CL 처럼 청산된 종목도 store 엔 잔존).

fix:
  - ``BrokerFill`` 이 fill 자체의 symbol/side 를 싣는다 (Bitget instId/side).
  - coid 미해석 시 fill.symbol + *단독 보유* 전략으로 귀속
    (``StrategyPositionStore.sole_holder_strategy``). 0명(수동)·2명(다전략)→미귀속.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.brokers.bitget.async_ws import _parse_fill_from_order
from src.brokers.types import BrokerFill
from src.live.fill_consumer import _resolve_fill_attribution, run_bitget_fill_consumer
from src.live.strategy_position_store import StrategyPositionStore
from src.live.wal import WAL


def _fill(*, coid, oid, trade_id, qty, price="100.0", symbol="", side=""):
    return BrokerFill(
        parent_id=coid, broker_order_id=oid, client_order_id=coid,
        trade_id=trade_id, qty=Decimal(qty), price=Decimal(price),
        fee=Decimal("0"), fee_asset="USDT",
        ts=datetime.now(tz=timezone.utc), is_maker=False,
        symbol=symbol, side=side,
    )


async def _stream_from(fills):
    for f in fills:
        yield f


# ── A. BrokerFill 필드 ────────────────────────────────────────────────────────

def test_brokerfill_symbol_side_default_empty():
    """기존 생성부(symbol/side 미지정)는 byte-identical — 기본 ""."""
    f = _fill(coid="c", oid="o", trade_id="t", qty="1")
    assert f.symbol == "" and f.side == ""


# ── B. 파서가 instId/side 를 싣는다 ───────────────────────────────────────────

def test_bitget_parser_carries_symbol_and_side():
    o = {
        "status": "filled", "orderId": "o1", "tradeId": "t1",
        "clientOid": "", "instId": "SOLUSDT", "side": "BUY",
        "accBaseVolume": "10", "fillPrice": "100", "fee": "0", "feeCcy": "USDT",
    }
    fill = _parse_fill_from_order(o, seen=set(), acc=None)
    assert fill is not None
    assert fill.symbol == "SOLUSDT"
    assert fill.side == "buy"  # 소문자 정규화


# ── C. sole_holder_strategy 규칙 ──────────────────────────────────────────────

def test_sole_holder_strategy_rules():
    store = StrategyPositionStore()
    # 0명 (수동/외부 포지션, 예: ORDI) → None → 안 건드림
    assert store.sole_holder_strategy("SOLUSDT") is None
    # 1명 → 그 sid
    store.record_fill(strategy_id="sid1", symbol="SOLUSDT", side="sell", qty=Decimal("10"))
    assert store.sole_holder_strategy("SOLUSDT") == "sid1"
    # 2명 (다전략 동시보유) → None → 현행 유지(자동 귀속 추정 안 함)
    store.record_fill(strategy_id="sid2", symbol="SOLUSDT", side="sell", qty=Decimal("3"))
    assert store.sole_holder_strategy("SOLUSDT") is None
    # 한 전략이 flat 되면 다시 단독
    store.force_sync_position(strategy_id="sid2", symbol="SOLUSDT", qty=Decimal("0"))
    assert store.sole_holder_strategy("SOLUSDT") == "sid1"


# ── D. _resolve_fill_attribution ──────────────────────────────────────────────

def test_attribution_uses_fill_symbol_and_sole_holder():
    store = StrategyPositionStore()
    store.record_fill(strategy_id="sid1", symbol="SOLUSDT", side="sell", qty=Decimal("10"))
    close = _fill(coid="plan-x", oid="o", trade_id="t", qty="10", symbol="SOLUSDT", side="buy")
    assert _resolve_fill_attribution(None, close, store) == ("SOLUSDT", "buy", "sid1")


def test_attribution_manual_position_not_attributed():
    """보유자 0명(수동 ORDI) → strategy_id None (미귀속, store 안 건드림)."""
    store = StrategyPositionStore()
    f = _fill(coid="manual", oid="o", trade_id="t", qty="5", symbol="ORDIUSDT", side="sell")
    symbol, _side, sid = _resolve_fill_attribution(None, f, store)
    assert symbol == "ORDIUSDT" and sid is None


def test_attribution_prefers_coid_context_when_present():
    store = StrategyPositionStore()
    ctx = ("BTCUSDT", "sell", "sidA")
    f = _fill(coid="c", oid="o", trade_id="t", qty="1", symbol="ETHUSDT", side="buy")
    assert _resolve_fill_attribution(ctx, f, store) == ctx  # ctx 우선


# ── E. 엔드투엔드: 진입 + 네이티브 TP/SL 청산 → store 0 (인플레이션 차단) ──────

@pytest.mark.asyncio
async def test_native_tpsl_close_nets_store_to_zero(tmp_path: Path):
    """진입(coid 등록)→네이티브 TP/SL 청산(coid 미등록, fill.symbol 보유) → store 0.

    옛 버그였다면 청산이 symbol="" 로 기록·drop 돼 store 가 -10 으로 잔존(인플레).
    """
    store = StrategyPositionStore()
    # 진입 주문 coid context 등록 (executor 가 place 전에 하는 것).
    store.register_order_context(
        client_order_id="open1", symbol="SOLUSDT", side="sell", strategy_id="sid1",
    )
    # WAL observer 가 fill 을 store 로 fan-out (live_run 배선과 동일 시맨틱).
    wal = WAL(
        tmp_path / "wal.jsonl",
        observer=lambda ev: store.ingest_fill_event(ev.event_type, ev.payload),
    )
    stop = asyncio.Event()
    fills = [
        # 진입: coid 해석됨 → sid1 SOL -10
        _fill(coid="open1", oid="o1", trade_id="t1", qty="10", symbol="SOLUSDT", side="sell"),
        # 청산: 네이티브 TP/SL plan order — coid 미등록. fill.symbol + 단독보유 귀속.
        _fill(coid="plan-x", oid="o2", trade_id="t2", qty="10", symbol="SOLUSDT", side="buy"),
    ]
    await run_bitget_fill_consumer(
        lambda: _stream_from(fills), wal=wal, position_store=store, stop_event=stop,
    )
    # 진입 -10 + 청산 +10 = 0 (청산이 drop 안 됨).
    assert store.get_positions("sid1") == []  # flat


@pytest.mark.asyncio
async def test_native_tpsl_close_survives_cross_run_replay(tmp_path: Path):
    """fix 후 WAL 은 청산에 symbol/strategy 를 담아 cross-run replay 도 0 으로 net."""
    store = StrategyPositionStore()
    store.register_order_context(
        client_order_id="open1", symbol="SOLUSDT", side="sell", strategy_id="sid1",
    )
    wal = WAL(
        tmp_path / "wal.jsonl",
        observer=lambda ev: store.ingest_fill_event(ev.event_type, ev.payload),
    )
    stop = asyncio.Event()
    fills = [
        _fill(coid="open1", oid="o1", trade_id="t1", qty="10", symbol="SOLUSDT", side="sell"),
        _fill(coid="plan-x", oid="o2", trade_id="t2", qty="10", symbol="SOLUSDT", side="buy"),
    ]
    await run_bitget_fill_consumer(
        lambda: _stream_from(fills), wal=wal, position_store=store, stop_event=stop,
    )
    # 새 프로세스가 WAL 만 보고 복원 → 청산이 보존돼 0.
    fresh = StrategyPositionStore()
    fresh.replay_from_wal(tmp_path / "wal.jsonl")
    assert fresh.get_positions("sid1") == []
