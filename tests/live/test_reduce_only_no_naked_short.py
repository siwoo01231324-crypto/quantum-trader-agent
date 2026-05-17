"""#238 Item 7 — a long-only strategy's SELL must never open a naked short.

System invariant (user-confirmed): no strategy intentionally shorts. A
strategy `sell` always means "reduce/exit my long", never "open a short".
On Binance USDⓈ-M Futures a `sell` with no position OPENS a short — that is
exactly how the incident's naked -1 BTC short was created. Marking every
strategy SELL `reduce_only` makes the exchange itself refuse to
open/extend a short: it reduces an existing long or no-ops.

Chain: AsyncStrategyOrchestrator → OrderIntent.reduce_only →
intent_to_order_request → OrderRequest.reduce_only → broker reduceOnly.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from portfolio import AsyncStrategyOrchestrator
from src.portfolio.order_intent import OrderIntent
from src.live.conversion import intent_to_order_request
from risk.dsl import Policy

# #238 — BTCUSDT sizes against USDT equity. equity_usdt is supplied so the
# fraction→qty conversion yields a real qty; this test exercises the
# reduce_only composition (#238 Item 7), not sizing. (Was implicitly relying
# on the pre-#238 raw-fraction-as-qty behaviour.)
SNAP = {
    "symbol": "BTCUSDT", "price": 50_000.0,
    "equity_krw": 1_000_000.0, "equity_usdt": 1_000_000.0,
}


class _LongOnlySell:
    is_live_scanner: ClassVar[bool] = False

    def __init__(self, action: str) -> None:
        self._action = action

    def on_bar(self, ctx) -> Signal:
        return Signal(action=self._action, size=1.0, reason=f"{self._action}_sig")


def _run(strat):
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("s", strat)
    return asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), SNAP))


def test_order_intent_reduce_only_defaults_false():
    """Backward compatible: existing callers that omit the field are unchanged."""
    oi = OrderIntent(strategy_id="s", symbol="BTCUSDT", side="buy",
                     qty=0.1, reason="x")
    assert oi.reduce_only is False


def test_strategy_sell_intent_is_reduce_only():
    intents = _run(_LongOnlySell("sell"))
    assert len(intents) == 1
    assert intents[0].side == "sell"
    assert intents[0].reduce_only is True, (
        "a long-only strategy SELL must be reduce_only so it can never "
        "open a naked short on futures"
    )


def test_strategy_buy_intent_is_not_reduce_only():
    """Buys must stay normal — reduce_only would block legitimate new longs."""
    intents = _run(_LongOnlySell("buy"))
    assert len(intents) == 1
    assert intents[0].side == "buy"
    assert intents[0].reduce_only is False


def test_conversion_passes_reduce_only_through():
    intent = OrderIntent(strategy_id="momo", symbol="BTCUSDT", side="sell",
                         qty=0.5, reason="exit", reduce_only=True)
    req = intent_to_order_request(intent, idempotency_key="k1")
    assert req.reduce_only is True
    assert req.side.value == "SELL"


def test_conversion_default_intent_is_not_reduce_only_bit_identical():
    """An intent without the flag converts exactly as before (no behaviour drift)."""
    intent = OrderIntent(strategy_id="momo", symbol="BTCUSDT", side="buy",
                         qty=0.5, reason="entry")
    req = intent_to_order_request(intent, idempotency_key="k2")
    assert req.reduce_only is False
