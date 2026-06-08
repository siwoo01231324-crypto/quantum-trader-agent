"""데모 계좌에서 거래소 네이티브 preset TP/SL (진입 주문 첨부) 검증.

봇 전체를 끄지 않고, Bitget *데모* 계좌에 BTCUSDT 소액 주문 1건을
presetStopSurplusPrice/presetStopLossPrice 와 함께 넣어 **파라미터가 수락되는지**
확인한다. 끝나면 포지션·plan order 를 정리한다.

실행:  python scripts/verify_bitget_preset_tpsl_demo.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, ".")
sys.path.insert(0, "src")

# .env 로드 (autoload 없을 때 대비)
for _line in Path(".env").read_text(encoding="utf-8").splitlines():
    _line = _line.strip()
    if "=" in _line and not _line.startswith("#"):
        _k, _v = _line.split("=", 1)
        os.environ.setdefault(_k.strip(), _v.strip())

os.environ["BITGET_NATIVE_TPSL"] = "1"  # 게이트 강제 ON (이 스크립트 한정)

from src.brokers.base import OrderRequest, OrderType, Side, TimeInForce  # noqa: E402
from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter  # noqa: E402

SYMBOL = "BTCUSDT"
QTY = Decimal("0.001")


async def main() -> None:
    key = os.environ.get("BITGET_DEMO_API_KEY")
    sec = os.environ.get("BITGET_DEMO_SECRET")
    pw = os.environ.get("BITGET_DEMO_PASSPHRASE")
    if not (key and sec and pw):
        print("❌ 데모 자격증명 누락 (BITGET_DEMO_API_KEY/SECRET/PASSPHRASE)")
        return

    adapter = AsyncBitgetFuturesAdapter(
        api_key=key, secret=sec, passphrase=pw, paper=True,
    )
    print(f"데모 어댑터 구성 (paper=True, productType={adapter._product_type})")

    # 1) 단방향 모드 보장
    try:
        await adapter.ensure_position_mode(hedge=False)
        print("✅ position mode one-way 보장")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ position mode: {exc} (보유 포지션 있거나 이미 설정 — 계속)")

    # 2) 현재가 조회 (공개 ticker)
    try:
        data = await adapter._client._request(
            "GET", "/api/v2/mix/market/ticker",
            params={"symbol": SYMBOL, "productType": adapter._product_type},
            signed=False,
        )
        row = data[0] if isinstance(data, list) else data
        price = Decimal(str(row.get("lastPr") or row.get("markPrice")))
        print(f"현재가 {SYMBOL} = {price}")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ ticker 조회 실패: {exc}")
        return

    # 3) preset 가격 계산 (롱: TP +1%, SL -0.5%)
    tp = (price * Decimal("1.01")).quantize(Decimal("0.1"))
    sl = (price * Decimal("0.995")).quantize(Decimal("0.1"))
    print(f"preset → TP={tp} (+1%), SL={sl} (-0.5%)")

    req = OrderRequest(
        client_order_id="presetverify001",
        symbol=SYMBOL, side=Side.BUY, qty=QTY,
        order_type=OrderType.MARKET, price=None, tif=TimeInForce.IOC,
        reduce_only=False, preset_tp_price=tp, preset_sl_price=sl,
    )

    # 4) 주문 제출
    print("\n=== preset TP/SL 진입 주문 제출 ===")
    try:
        ack = await adapter.place_order(req)
        print(f"✅✅ 주문 수락됨! status={ack.status} broker_order_id={ack.broker_order_id}")
        print("   → preset 파라미터 정상. 마인넷에서 켜도 됨.")
    except Exception as exc:  # noqa: BLE001
        print(f"❌ 주문 거부/에러: {type(exc).__name__}: {exc}")
        print("   → 이 에러 코드를 보고 preset 파라미터를 고쳐야 함.")
        await adapter.aclose() if hasattr(adapter, "aclose") else None
        return

    # 5) 거래소 측 plan order(자동생성 TP/SL) 확인
    await asyncio.sleep(1.5)
    try:
        plans = await adapter._client.get_pending_tpsl_orders(symbol=SYMBOL)
        print(f"\n거래소 측 TP/SL plan order: {len(plans)}건")
        for p in plans[:4]:
            print(f"   - {p.get('planType')} trigger={p.get('triggerPrice')} "
                  f"orderId={p.get('orderId')}")
        if plans:
            print("   ✅ 진입과 함께 TP/SL 자동 생성 확인!")
        else:
            print("   ⚠️ plan order 안 보임 — preset 미반영일 수 있음 (수동 확인 권장)")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ plan order 조회 실패: {exc}")

    # 6) 정리 — 포지션 청산 + plan order 취소
    print("\n=== 정리 (포지션 청산) ===")
    try:
        close = OrderRequest(
            client_order_id="presetverifyclose1",
            symbol=SYMBOL, side=Side.SELL, qty=QTY,
            order_type=OrderType.MARKET, price=None, tif=TimeInForce.IOC,
            reduce_only=True,
        )
        await adapter.place_order(close)
        print("✅ 데모 포지션 청산 (reduce-only)")
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️ 청산 실패 (데모 대시보드에서 수동 정리): {exc}")


if __name__ == "__main__":
    asyncio.run(main())
