"""#238 — orchestrator wires size_to_qty so OrderIntent.qty is real coin qty.

Before this fix `run_bar` used the resolve_size *fraction* directly as the
coin quantity (`qty == size`). Several existing tests assert that OLD BUG
(e.g. expecting qty == 0.05). Those are updated alongside this change and
documented as "was asserting pre-#238 raw-fraction bug".

Venue-aware equity:
  - KRX 6-digit symbol → ``market_snapshot["equity_krw"]``
  - Binance ``*USDT`` symbol → ``market_snapshot["equity_usdt"]``
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


def _orch() -> AsyncStrategyOrchestrator:
    return AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))


class _BuyFrac:
    is_live_scanner: ClassVar[bool] = False

    def __init__(self, size: float) -> None:
        self._size = size

    def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=self._size, reason="buy_sig")


class _ScannerBuy(LiveScannerMixin):
    def __init__(self, size: float) -> None:
        self._size = size

    async def on_bar(self, ctx) -> Signal:
        snap = ctx["market_snapshot"]
        return Signal(action="buy", size=self._size, reason=f"buy:{snap['symbol']}")


def _ohlcv(n: int = 30, price: float = 100.0) -> pd.DataFrame:
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": price, "high": price, "low": price, "close": price,
         "volume": 1000.0},
        index=idx,
    )


class TestBinanceEquityWiring:
    def test_binance_buy_qty_is_fraction_of_usdt_equity(self):
        """5% of 10_000 USDT @ 50_000 = 0.01 BTC (NOT 0.05 raw fraction)."""
        orch = _orch()
        orch.register_strategy("s", _BuyFrac(0.05))
        snap = {
            "symbol": "BTCUSDT", "price": 50_000.0,
            "equity_krw": 0.0, "equity_usdt": 10_000.0,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert len(intents) == 1
        assert intents[0].qty == pytest.approx(0.01)
        assert intents[0].qty != 0.05  # the OLD raw-fraction bug

    def test_momo_full_one_point_zero_is_not_one_coin(self):
        """momo sizing_mode:full size=1.0 → full equity notional, NOT 1.0 BTC."""
        orch = _orch()
        orch.register_strategy("momo", _BuyFrac(1.0))
        snap = {
            "symbol": "BTCUSDT", "price": 50_000.0,
            "equity_krw": 0.0, "equity_usdt": 10_000.0,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert len(intents) == 1
        assert intents[0].qty == pytest.approx(0.2)  # 10_000 / 50_000
        assert intents[0].qty != 1.0  # the root incident


class TestKrxEquityWiring:
    def test_krx_buy_uses_krw_equity_integer_shares(self):
        orch = _orch()
        orch.register_strategy("s", _BuyFrac(0.5))
        snap = {
            "symbol": "005930", "price": 70_000.0,
            "equity_krw": 1_000_000.0, "equity_usdt": 0.0,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert len(intents) == 1
        # 50% of 1_000_000 / 70_000 = 7.14 → 7 shares (step 1 ROUND_DOWN)
        assert intents[0].qty == 7.0


class TestDropPaths:
    def test_below_min_notional_emits_no_intent(self):
        """A guaranteed-rejected sub-min-notional order is dropped, not emitted."""
        orch = _orch()
        orch.register_strategy("s", _BuyFrac(1.0))
        snap = {
            "symbol": "BTCUSDT", "price": 50_000.0,
            "equity_krw": 0.0, "equity_usdt": 4.0,  # 4 USDT < 5 USDT min
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert intents == []

    def test_zero_equity_emits_no_intent(self):
        orch = _orch()
        orch.register_strategy("s", _BuyFrac(0.05))
        snap = {
            "symbol": "BTCUSDT", "price": 50_000.0,
            "equity_krw": 0.0, "equity_usdt": 0.0,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert intents == []


class TestLiveScannerWiring:
    def test_live_scanner_per_symbol_qty_converted(self):
        orch = _orch()
        orch.register_strategy("scanner", _ScannerBuy(0.05))
        universe = {"BTCUSDT": _ohlcv(price=50_000.0)}
        snap = {
            "symbol": None, "price": None,
            "equity_krw": 0.0, "equity_usdt": 10_000.0,
            "ohlcv_history": universe,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert len(intents) == 1
        # 5% of 10_000 USDT @ 50_000 = 0.01 BTC
        assert intents[0].qty == pytest.approx(0.01)


class TestSellPathPreservesReduceOnly:
    def test_sell_intent_still_reduce_only_after_conversion(self):
        """#238 Item 7 must compose: a SELL still carries reduce_only=True and
        a converted (non-raw-fraction) qty."""

        class _Sell:
            is_live_scanner: ClassVar[bool] = False

            def on_bar(self, ctx) -> Signal:
                return Signal(action="sell", size=0.5, reason="exit")

        orch = _orch()
        orch.register_strategy("s", _Sell())
        snap = {
            "symbol": "BTCUSDT", "price": 50_000.0,
            "equity_krw": 0.0, "equity_usdt": 10_000.0,
        }
        intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
        assert len(intents) == 1
        assert intents[0].side == "sell"
        assert intents[0].reduce_only is True
        # 50% of 10_000 / 50_000 = 0.1 BTC (converted, not raw 0.5)
        assert intents[0].qty == pytest.approx(0.1)
