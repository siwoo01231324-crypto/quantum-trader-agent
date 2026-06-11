"""Bitget DEMO TP/SL *발동(FIRING)* 검증 — 등록 말고 실제 청산되는지 (2026-06-12).

⚠️ DEMO 전용. 작은 SOL 숏 → TP/SL 라인을 현재가 *바로 옆*(±0.15%)에 걸고,
포지션이 실제로 자동청산되는지 폴링. 두 방식 비교:
  A) 포지션 전체 TPSL  — planType pos_profit/pos_loss (size 없음, holdSide)
  B) 부분 TPSL        — planType profit_plan/loss_plan (+size)  ← 현재 코드 방식

목적: "ACCEPTED" 가 아니라 "라인 닿으면 진짜 닫히나" 를 본다. 라이브에서 안 닫혀서
유저가 수동청산하던 문제의 정체 확정.

실행:  PYTHONIOENCODING=utf-8 python scripts/probe_bitget_tpsl_firing.py A
       (인자 A=포지션전체TPSL / B=부분TPSL, 기본 A)
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
SIZE = Decimal("0.5")
LEVERAGE = 2
MODE = (sys.argv[1] if len(sys.argv) > 1 else "A").upper()


def _strip(v):
    return (v or "").strip().strip('"').strip("'")


async def _pos_qty(adapter) -> Decimal:
    try:
        ps = await adapter.get_positions(symbol=SYMBOL)
        for p in ps:
            q = abs(Decimal(str(getattr(p, "qty", 0) or getattr(p, "size", 0) or 0)))
            if q > 0:
                return q
    except Exception as e:  # noqa: BLE001
        print(f"  pos 조회 warn: {e}")
    return Decimal("0")


async def main() -> int:
    load_dotenv()
    from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter

    key = _strip(os.environ.get("BITGET_DEMO_API_KEY"))
    sec = _strip(os.environ.get("BITGET_DEMO_SECRET"))
    pw = _strip(os.environ.get("BITGET_DEMO_PASSPHRASE"))
    if not (key and sec and pw):
        print("BITGET_DEMO_* 필요"); return 1
    adapter = AsyncBitgetFuturesAdapter(api_key=key, secret=sec, passphrase=pw, paper=True)
    c = adapter._client
    pt = c._product_type
    print(f"=== TP/SL FIRING 검증 (mode={MODE}, {SYMBOL}, demo {pt}) ===")

    opened = False
    try:
        await c.set_position_mode(hedge=False)
        try:
            await c.set_leverage(symbol=SYMBOL, leverage=LEVERAGE)
        except Exception:  # noqa: BLE001
            pass
        cid = f"fire-open-{int(time.time()*1000)}"
        await c.place_order(symbol=SYMBOL, side="sell", order_type="market",
                            size=SIZE, price=None, client_oid=cid)
        opened = True
        await asyncio.sleep(2.0)
        ps = await adapter.get_positions(symbol=SYMBOL)
        mark = Decimal(str(getattr(ps[0], "mark_price", 0) or getattr(ps[0], "entry_price", 0))) if ps else Decimal("0")
        if not (mark > 0):
            print("  mark 못 읽음"); return 1
        # 숏: SL = 현재가 위 0.06%, TP = 현재가 아래 0.06% (거의 확실히 닿게)
        sl = (mark * Decimal("1.0006")).quantize(Decimal("0.001"))
        tp = (mark * Decimal("0.9994")).quantize(Decimal("0.001"))
        print(f"  숏 {SIZE} 진입. mark={mark} → SL(위)={sl} TP(아래)={tp} (±0.15%, 곧 닿게)")

        ep = "/api/v2/mix/order/place-tpsl-order"
        if MODE == "C":
            # 실제 프로덕션 경로 — adapter.place_protective_order (whole-position).
            adapter._hedge_mode = False
            for label, kind, trig in [("SL", "STOP_MARKET", sl), ("TP", "TAKE_PROFIT_MARKET", tp)]:
                try:
                    oid = await adapter.place_protective_order(
                        symbol=SYMBOL, side="BUY", qty=SIZE, stop_price=trig, kind=kind)
                    print(f"  [adapter {label}] 등록 ✅ oid={oid}")
                except Exception as e:  # noqa: BLE001
                    print(f"  [adapter {label}] 등록 ❌ {e}")
        elif MODE == "A":
            # 포지션 전체 TPSL
            for label, plan, trig in [("pos_loss SL", "pos_loss", sl), ("pos_profit TP", "pos_profit", tp)]:
                body = {"marginCoin": "USDT", "productType": pt, "symbol": SYMBOL,
                        "planType": plan, "triggerPrice": str(trig), "triggerType": "mark_price",
                        "holdSide": "sell", "clientOid": f"fA-{plan}-{int(time.time()*1000)}"}
                try:
                    r = await c._request("POST", ep, body=body)
                    print(f"  [{label}] 등록 ✅ {r}")
                except Exception as e:  # noqa: BLE001
                    print(f"  [{label}] 등록 ❌ {e}")
        else:
            for label, plan, trig in [("loss_plan SL", "loss_plan", sl), ("profit_plan TP", "profit_plan", tp)]:
                body = {"marginCoin": "USDT", "productType": pt, "symbol": SYMBOL,
                        "planType": plan, "triggerPrice": str(trig), "triggerType": "mark_price",
                        "executePrice": "0", "holdSide": "sell", "size": str(SIZE),
                        "clientOid": f"fB-{plan}-{int(time.time()*1000)}"}
                try:
                    r = await c._request("POST", ep, body=body)
                    print(f"  [{label}] 등록 ✅ {r}")
                except Exception as e:  # noqa: BLE001
                    print(f"  [{label}] 등록 ❌ {e}")

        print(f"  --- 발동 폴링 (포지션 닫히나 + mark 추적, 최대 200초). SL={sl} TP={tp} ---")
        fired = False
        hi = lo = mark
        crossed_sl = crossed_tp = False
        for i in range(40):
            await asyncio.sleep(5)
            q = await _pos_qty(adapter)
            # 현재 mark
            try:
                ps2 = await adapter.get_positions(symbol=SYMBOL)
                m = Decimal(str(getattr(ps2[0], "mark_price", 0) or 0)) if ps2 else Decimal("0")
            except Exception:  # noqa: BLE001
                m = Decimal("0")
            if m > 0:
                hi = max(hi, m); lo = min(lo, m)
                if m >= sl: crossed_sl = True
                if m <= tp: crossed_tp = True
            print(f"  +{(i+1)*5}s  pos_qty={q}  mark={m}  (hi={hi} lo={lo})")
            if q == 0:
                print(f"  🎯 발동 확인 — 포지션 자동청산됨 ({(i+1)*5}초 내)")
                fired = True
                break
        if not fired:
            print(f"  결과: 자동청산 안 됨. SL({sl}) 도달={crossed_sl}, TP({tp}) 도달={crossed_tp}")
            if crossed_sl or crossed_tp:
                print("  ❌❌ 라인을 *넘었는데도* 발동 안 함 — 포지션TPSL이 청산 안 함 (확정)")
            else:
                print("  ⚠️ 가격이 라인에 안 닿음 — 불명확(가격 변동 부족)")
    finally:
        print("  --- 정리 ---")
        try:
            pend = await c.get_pending_tpsl_orders(symbol=SYMBOL)
            for o in (pend or []):
                oid = o.get("orderId") or o.get("planOrderId") or ""
                if oid:
                    try: await c.cancel_tpsl_order(symbol=SYMBOL, order_id=oid)
                    except Exception: pass  # noqa: BLE001
        except Exception: pass  # noqa: BLE001
        if opened and (await _pos_qty(adapter)) > 0:
            try:
                await c.place_order(symbol=SYMBOL, side="buy", order_type="market",
                                    size=SIZE, price=None,
                                    client_oid=f"fire-close-{int(time.time()*1000)}", reduce_only=True)
                print("  남은 포지션 청산")
            except Exception as e:  # noqa: BLE001
                print(f"  ⚠️ 청산 실패 — 데모 수동확인: {e}")
        try: await c.aclose()
        except Exception: pass  # noqa: BLE001
    print("=== 완료 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
