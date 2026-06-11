"""Bitget DEMO TPSL 파라미터 실측 probe — 43011 정확한 원인 확정 (2026-06-11).

⚠️ DEMO 전용 (paptrading=1, BITGET_DEMO_* 크레덴셜). 실거래 아님 — 마인넷 자본
무관. 작은 SOLUSDT 숏을 데모에 열고, post-fill 손절(loss_plan)을 5가지 body 변형
으로 제출해 *각각의 정확한 응답/에러*를 찍는다. 끝나면 TPSL 취소 + 포지션 청산.

목적: post-fill place-tpsl-order 가 one-way 모드에서 거부되는 진짜 원인(holdSide
vs executePrice)을 추측 아닌 실측으로 확정 → 그 한 파라미터만 고친다.

실행:  python scripts/probe_bitget_tpsl_demo.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv


SYMBOL = "SOLUSDT"
SIZE = Decimal("0.5")          # ~$30-75 데모 명목. 최소 size/notional 여유.
LEVERAGE = 2


def _strip(v: str | None) -> str:
    return (v or "").strip().strip('"').strip("'")


async def _try(label: str, coro) -> None:
    """한 변형 제출 → 정확한 결과/에러 출력 (raise 흡수)."""
    try:
        res = await coro
        print(f"  [{label}] ✅ ACCEPTED → {res}")
    except Exception as err:  # noqa: BLE001 — probe 는 모든 에러를 기록
        print(f"  [{label}] ❌ {type(err).__name__}: {err}")


async def main() -> int:
    load_dotenv()
    from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter

    key = _strip(os.environ.get("BITGET_DEMO_API_KEY"))
    secret = _strip(os.environ.get("BITGET_DEMO_SECRET"))
    passphrase = _strip(os.environ.get("BITGET_DEMO_PASSPHRASE"))
    if not (key and secret and passphrase):
        print("BITGET_DEMO_API_KEY / SECRET / PASSPHRASE 필요 (.env)")
        return 1

    adapter = AsyncBitgetFuturesAdapter(
        api_key=key, secret=secret, passphrase=passphrase, paper=True,
    )
    client = adapter._client
    pt = client._product_type
    print(f"=== Bitget DEMO TPSL probe — productType={pt} symbol={SYMBOL} ===")

    opened = False
    try:
        # 1) one-way 모드 + 낮은 레버리지.
        await _try("set one_way_mode", client.set_position_mode(hedge=False))
        try:
            await client.set_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        except Exception as err:  # noqa: BLE001
            print(f"  set_leverage warn: {err}")

        # 2) 작은 숏 진입 (market).
        cid = f"probe-open-{int(time.time()*1000)}"
        resp = await client.place_order(
            symbol=SYMBOL, side="sell", order_type="market",
            size=SIZE, price=None, client_oid=cid,
        )
        opened = True
        print(f"  opened SHORT {SIZE} {SYMBOL} → {resp}")
        await asyncio.sleep(2.0)

        # 3) 포지션 mark 가격 읽기 → 숏 SL trigger = mark*1.02 (현재가 위).
        positions = await adapter.get_positions(symbol=SYMBOL)
        mark = None
        for p in positions:
            mark = getattr(p, "mark_price", None) or getattr(p, "markPrice", None)
        if mark is None:
            # fallback: 진입가 근처
            mark = Decimal(str(getattr(positions[0], "entry_price", "0"))) if positions else Decimal("0")
        mark = Decimal(str(mark))
        if not (mark > 0):
            print("  mark price 못 읽음 — probe 중단 (정리만)")
            return 1
        trig = (mark * Decimal("1.02")).quantize(Decimal("0.001"))
        print(f"  mark={mark} → SL trigger(숏, 현재가 위)={trig}")

        sl_trig = (mark * Decimal("1.02")).quantize(Decimal("0.001"))   # 숏 SL: 위
        tp_trig = (mark * Decimal("0.98")).quantize(Decimal("0.001"))   # 숏 TP: 아래

        print("\n--- 수정된 adapter.place_protective_order 검증 (one-way, 숏) ---")
        # _hedge_mode 를 one-way(False) 로 확정 (위 set_position_mode 가 set).
        adapter._hedge_mode = False
        # close_side="BUY" = 숏 포지션 청산 → 수정 매핑상 holdSide="sell" 기대.
        await _try("SL adapter.place_protective_order(BUY, STOP_MARKET)",
                   adapter.place_protective_order(
                       symbol=SYMBOL, side="BUY", qty=SIZE,
                       stop_price=sl_trig, kind="STOP_MARKET"))
        await _try("TP adapter.place_protective_order(BUY, TAKE_PROFIT_MARKET)",
                   adapter.place_protective_order(
                       symbol=SYMBOL, side="BUY", qty=SIZE,
                       stop_price=tp_trig, kind="TAKE_PROFIT_MARKET"))

    finally:
        print("\n--- 정리 (TPSL 취소 + 포지션 청산) ---")
        try:
            pend = await client.get_pending_tpsl_orders(symbol=SYMBOL)
            for o in (pend or []):
                oid = o.get("orderId") or o.get("planOrderId") or ""
                if oid:
                    try:
                        await client.cancel_tpsl_order(symbol=SYMBOL, order_id=oid)
                        print(f"  cancelled tpsl {oid}")
                    except Exception as err:  # noqa: BLE001
                        print(f"  cancel tpsl {oid} warn: {err}")
        except Exception as err:  # noqa: BLE001
            print(f"  pending tpsl 조회 warn: {err}")
        if opened:
            try:
                cid = f"probe-close-{int(time.time()*1000)}"
                await client.place_order(
                    symbol=SYMBOL, side="buy", order_type="market",
                    size=SIZE, price=None, client_oid=cid, reduce_only=True,
                )
                print(f"  closed (market buy reduce_only {SIZE})")
            except Exception as err:  # noqa: BLE001
                print(f"  ⚠️ 포지션 청산 실패 — 데모에서 수동 확인 필요: {err}")
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass
    print("\n=== probe 완료 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
