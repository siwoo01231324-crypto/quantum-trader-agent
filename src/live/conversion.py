from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from src.brokers.base import OrderRequest, OrderType
from src.execution.base import Side, TimeInForce
from src.portfolio.order_intent import OrderIntent

SYMBOL_STEP_SIZES: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.001"),
    "ETHUSDT": Decimal("0.001"),
    "SOLUSDT": Decimal("1"),
}


def intent_to_order_request(
    intent: OrderIntent,
    *,
    idempotency_key: str,
    order_type: OrderType = OrderType.MARKET,
) -> OrderRequest:
    """OrderIntent.qty (float) → OrderRequest.qty (Decimal) 변환 단일 지점.

    - 변환 규칙: Decimal(str(intent.qty)).quantize(symbol_step, ROUND_DOWN)
    - Decimal(float) 직접 호출 금지 (부동소수점 오염).
    - 미등록 심볼 → ValueError.
    """
    step = SYMBOL_STEP_SIZES.get(intent.symbol)
    if step is None:
        raise ValueError(f"Unsupported symbol for live trading: {intent.symbol}")
    qty = Decimal(str(intent.qty)).quantize(step, rounding=ROUND_DOWN)
    side = Side.BUY if intent.side == "buy" else Side.SELL
    return OrderRequest(
        client_order_id=idempotency_key,
        symbol=intent.symbol,
        side=side,
        qty=qty,
        order_type=order_type,
        price=None,
        tif=TimeInForce.GTC,
    )
