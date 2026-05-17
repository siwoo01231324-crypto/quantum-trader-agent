from __future__ import annotations
from decimal import Decimal, ROUND_DOWN
from src.brokers.base import OrderRequest, OrderType
from src.execution.base import Side, TimeInForce
from src.portfolio.order_intent import OrderIntent

# Explicit step-size registry for symbols where the live exchange step
# deviates from the paradigm fallback. Universe-wide live-scanner activation
# (#227) added the resolver below — KRX 6-digit codes and generic Binance
# USDT pairs fall through to deterministic defaults instead of being
# rejected as "Unsupported symbol".
SYMBOL_STEP_SIZES: dict[str, Decimal] = {
    "BTCUSDT": Decimal("0.001"),
    "ETHUSDT": Decimal("0.001"),
    "SOLUSDT": Decimal("1"),
}

# Default Binance USDT-pair step. Conservative — most large-cap perps use
# 0.001 or finer; high-priced majors use 0.001 (BTCUSDT/ETHUSDT). For exotic
# pairs override via SYMBOL_STEP_SIZES.
_BINANCE_USDT_DEFAULT_STEP = Decimal("0.001")


def get_step_size(symbol: str) -> Decimal | None:
    """Resolve the order-quantity step size for *symbol*.

    Priority:
      1. Explicit entry in ``SYMBOL_STEP_SIZES`` (overrides everything).
      2. KRX 6-digit numeric code → ``Decimal("1")``  (KRX 종목은 1주 단위).
      3. Binance USDT pair (``"...USDT"``) → ``_BINANCE_USDT_DEFAULT_STEP``.
      4. None — caller treats as "unsupported symbol".

    Universe-wide live-scanner activation (#227) requires this fallback so
    the 350 KRX + 30 Binance basket can route through ``intent_to_order_request``
    without an explicit registry entry per symbol. Binance per-symbol step
    refinement (e.g. via ``exchangeInfo`` polling) is a separate issue.
    """
    if symbol in SYMBOL_STEP_SIZES:
        return SYMBOL_STEP_SIZES[symbol]
    if len(symbol) == 6 and symbol.isdigit():
        return Decimal("1")
    if symbol.endswith("USDT") and len(symbol) > len("USDT"):
        return _BINANCE_USDT_DEFAULT_STEP
    return None


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
    - #227: KRX 6자리 + Binance USDT pair 는 ``get_step_size`` fallback 으로 자동 처리.
    """
    step = get_step_size(intent.symbol)
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
        # #238 Item 7 — carry the long-only-exit guard to the broker so a
        # "sell with no long" is no-opped, not turned into a naked short.
        reduce_only=intent.reduce_only,
    )
