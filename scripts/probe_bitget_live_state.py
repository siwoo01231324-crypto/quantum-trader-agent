"""Bitget LIVE 계정 상태 *읽기 전용* 조회 (2026-06-14).

⚠️ READ-ONLY. 주문/취소 일절 없음 — get_all_positions + get_pending_tpsl_orders 만.
목적: AVAX/BTC/DOGE 의 실제 포지션 + 거래소에 걸린 TP/SL plan order 를 그대로 찍어
SL 누락·잔존주문·double-entry 실상을 로그추론 없이 사실로 확정.

실행:  PYTHONIOENCODING=utf-8 PYTHONUTF8=1 python scripts/probe_bitget_live_state.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv

WATCH = ("AVAXUSDT", "BTCUSDT", "DOGEUSDT")


def _strip(v):
    return (v or "").strip().strip('"').strip("'")


async def main() -> int:
    load_dotenv()
    from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter

    key = _strip(os.environ.get("BITGET_API_KEY"))
    sec = _strip(os.environ.get("BITGET_API_SECRET"))
    pw = _strip(os.environ.get("BITGET_API_PASSPHRASE"))
    if not (key and sec and pw):
        print("BITGET_API_KEY/SECRET/PASSPHRASE 필요 (live)"); return 1

    adapter = AsyncBitgetFuturesAdapter(api_key=key, secret=sec, passphrase=pw, paper=False)
    try:
        print("=== LIVE 포지션 (전체) ===")
        positions = await adapter.get_protective_positions()
        if not positions:
            print("  (포지션 없음)")
        for p in positions:
            flag = "  <<<" if p["symbol"] in WATCH else ""
            print(f"  {p['symbol']:14} {p['hold_side']:5} qty={p['qty']} "
                  f"entry={p['entry']} mark={p['mark']} lev={p['leverage']} upl={p['upl']}{flag}")

        print("\n=== 걸린 TP/SL plan order ===")
        # 전역 조회 (symbol=None) — 일부 거래소는 symbol 필수라 watch 별로도 조회.
        try:
            allp = await adapter.list_open_protective_orders(symbol=None)
            print(f"  [전역] {len(allp)} 건")
            for o in allp:
                print(f"    {o}")
        except Exception as e:  # noqa: BLE001
            print(f"  [전역] 조회 실패({e}) — 종목별로 시도")

        for sym in WATCH:
            try:
                rows = await adapter.list_open_protective_orders(symbol=sym)
                print(f"  [{sym}] {len(rows)} 건")
                for o in rows:
                    # 핵심 필드만 요약
                    print(f"    planType={o.get('planType')} triggerPrice={o.get('triggerPrice')} "
                          f"size={o.get('size')} side={o.get('side') or o.get('holdSide')} "
                          f"oid={o.get('orderId')}")
            except Exception as e:  # noqa: BLE001
                print(f"  [{sym}] 조회 실패: {e}")
        return 0
    finally:
        try:
            await adapter.close()
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
