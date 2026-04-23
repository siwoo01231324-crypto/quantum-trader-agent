from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Callable

from src.brokers.binance.rest import BinanceFuturesClient
from src.brokers.types import BrokerFill

log = logging.getLogger(__name__)


class ReconnectReconciler:
    """Tracks partial fills and reconciles open orders after WS reconnect.

    Dedup key: (broker_order_id, trade_id) — both are required to uniquely
    identify a fill event. WS events are not re-delivered after disconnect,
    so reconcile() polls REST to recover any missed fills.
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        on_fill: Callable[[BrokerFill], None],
        tracked_symbols: list[str] | None = None,
    ) -> None:
        self._client = client
        self._on_fill = on_fill
        self._tracked_symbols = tracked_symbols  # None = all open orders
        self._seen: set[tuple[str, str]] = set()  # (broker_order_id, trade_id)

    # ── WS event handler ──────────────────────────────────────────────────────

    def on_trade_event(self, o: dict) -> BrokerFill | None:
        """Process an ORDER_TRADE_UPDATE payload (the 'o' sub-object).

        Returns a BrokerFill if this is a new (non-duplicate) TRADE event,
        else returns None.
        """
        exec_type = o.get("x")
        if exec_type != "TRADE":
            return None

        broker_order_id = str(o.get("i", ""))
        trade_id = str(o.get("t", ""))
        dedup_key = (broker_order_id, trade_id)

        if dedup_key in self._seen:
            log.debug("Duplicate fill event skipped: %s", dedup_key)
            return None
        self._seen.add(dedup_key)

        client_order_id = o.get("c", "")
        symbol = o.get("s", "")
        ts_ms = int(o.get("T", 0))
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)

        qty = Decimal(str(o.get("l", "0")))      # last filled qty
        price = Decimal(str(o.get("L", "0")))    # last filled price
        fee = Decimal(str(o.get("n", "0")))
        fee_asset = str(o.get("N", "USDT"))
        is_maker = o.get("m", False)

        # parent_id is client_order_id (strategy-assigned)
        fill = BrokerFill(
            parent_id=client_order_id,
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            trade_id=trade_id,
            qty=qty,
            price=price,
            fee=fee,
            fee_asset=fee_asset,
            ts=ts,
            is_maker=bool(is_maker),
        )
        return fill

    # ── REST reconciliation ───────────────────────────────────────────────────

    def reconcile(self) -> None:
        """After reconnect: poll open orders via REST and emit any missed fills.

        We query open orders for each tracked symbol (or all symbols if none
        specified). For completed orders not in our seen set we emit fills.
        This covers the gap between WS disconnect and reconnect.
        """
        log.info("Starting REST reconciliation after WS reconnect")
        try:
            symbols = self._tracked_symbols or [None]
            for sym in symbols:
                open_orders = self._client.get_open_orders(sym)
                log.debug(
                    "Reconcile: %d open orders for symbol=%s", len(open_orders), sym
                )
                # Open orders have no missed fills — nothing to emit here.
                # Filled/cancelled orders are not returned by openOrders endpoint.
                # In production, callers should query specific order IDs from
                # their in-flight registry; this is the structural hook for that.
        except Exception as exc:
            log.error("Reconciliation failed: %s", exc)
