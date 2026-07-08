"""macross 진입 이력 — 거래소 수동 청산(태그없음) 귀속 (2026-07-08).

사용자가 거래소에서 직접 익절/손절하면 fill 이 strategy_id=None(봇 태그 없음)으로
기록된다. parse_macross_entries 가 이를 symbol+FIFO 로 열린 macross 숏에 청산으로
귀속(outcome="manual")시켜야 대시보드 "실제 진입" 에 손익이 반영된다. 단 다른 전략
태그 fill 이나 태그없는 SELL 은 macross 로 오귀속하면 안 된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dashboard.macross_entry_store import parse_macross_entries  # noqa: E402

_SID = "live-macross-regime-v1"


def _fill(sym, side, price, qty, ts, sid=_SID, tid=None):
    return {
        "event_type": "order_filled",
        "payload": {
            "strategy_id": sid, "symbol": sym, "side": side,
            "fill_price": price, "fill_qty": qty, "qty": qty,
            "trade_id": tid or f"{sym}-{side}-{ts}", "ts": ts,
        },
    }


def _write_wal(tmp_path, fills) -> str:
    run = tmp_path / "shadow-swing" / "run1"
    run.mkdir(parents=True)
    wal = run / "wal.jsonl"
    wal.write_text("\n".join(json.dumps(f) for f in fills), encoding="utf-8")
    return str(tmp_path / "shadow-swing*" / "*" / "wal.jsonl")


def test_manual_close_attributed_to_open_short(tmp_path):
    """태그없는(수동) BUY 가 열린 macross 숏을 청산 → outcome=manual + 손익."""
    glob = _write_wal(tmp_path, [
        _fill("BNBUSDT", "SELL", 583.65, 0.08, "2026-07-06T20:16:20+00:00"),
        # 수동 익절 (거래소 직접) — strategy_id 없음
        _fill("BNBUSDT", "buy", 577.43, 0.08, "2026-07-07T14:18:01+00:00", sid=None),
    ])
    rows = parse_macross_entries(wal_glob=glob)
    assert len(rows) == 1
    r = rows[0]
    assert r["status"] == "closed"
    assert r["outcome"] == "manual"
    assert r["exit_price"] == 577.43
    assert r["realized_usdt"] == round(0.08 * (583.65 - 577.43), 4)  # +0.4976 (숏 익절)


def test_bot_close_still_tp_sl_not_manual(tmp_path):
    """봇 청산(macross 태그)은 여전히 tp/sl 로 분류 (회귀 방지)."""
    glob = _write_wal(tmp_path, [
        _fill("BTCUSDT", "SELL", 63586.6, 0.001, "2026-07-06T20:08:01+00:00"),
        _fill("BTCUSDT", "buy", 62383.8, 0.001, "2026-07-06T21:00:00+00:00"),  # 태그 O
    ])
    rows = parse_macross_entries(wal_glob=glob)
    assert rows[0]["outcome"] in ("tp", "sl")
    assert rows[0]["outcome"] != "manual"


def test_other_strategy_buy_does_not_close_macross(tmp_path):
    """다른 전략(airborne) 태그 BUY 는 macross 숏을 청산하지 않는다 (오귀속 방지)."""
    glob = _write_wal(tmp_path, [
        _fill("ETHUSDT", "SELL", 1800.0, 0.1, "2026-07-06T20:00:00+00:00"),
        _fill("ETHUSDT", "buy", 1750.0, 0.1, "2026-07-06T21:00:00+00:00",
              sid="live-airborne-short-whitelist-v1"),
    ])
    rows = parse_macross_entries(wal_glob=glob)
    assert rows[0]["status"] == "open"  # airborne 청산은 macross 숏에 안 붙음


def test_untagged_sell_does_not_open_macross_entry(tmp_path):
    """태그없는 SELL 은 macross 진입이 아니다 (수동 신규 숏 등)."""
    glob = _write_wal(tmp_path, [
        _fill("XRPUSDT", "SELL", 0.5, 100.0, "2026-07-06T20:00:00+00:00", sid=None),
    ])
    rows = parse_macross_entries(wal_glob=glob)
    assert rows == []
