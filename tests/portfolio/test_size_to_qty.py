"""#238 — fraction→coin-qty conversion (the deeper -2019 Margin-insufficient cause).

`resolve_size` returns a *fraction of available equity* in [0, 1] (or a
Signal.size passthrough). Before this fix the orchestrator used that fraction
DIRECTLY as the coin quantity:

  - live-scanner size=0.05  →  ordered 0.05 coins (intended: 5% of equity)
  - momo sizing_mode:full   →  size=1.0  →  ordered 1.0 BTC literally (~$80k)

`size_to_qty` converts the fraction into a real coin quantity:

    qty_coins = (fraction * available_equity) / price

then applies exchange filters:
  - LOT_SIZE step ROUND_DOWN (reusing src.live.conversion.get_step_size)
  - MIN_NOTIONAL drop (Binance USDT pairs: conservative 5 USDT constant)
  - zero-qty drop
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from portfolio.sizing import size_to_qty, BINANCE_MIN_NOTIONAL_USDT


class TestSizeToQtyBasics:
    def test_fraction_times_equity_over_price(self):
        # 5% of 10_000 USDT = 500 USDT notional / 50_000 price = 0.01 BTC
        qty = size_to_qty(0.05, equity=10_000.0, price=50_000.0, symbol="BTCUSDT")
        assert qty == pytest.approx(0.01)

    def test_momo_full_is_full_equity_notional_not_one_coin(self):
        """momo sizing_mode:full → size=1.0 must mean 100% of equity, NOT 1 BTC.

        This is the root incident: a 1.0 fraction ordered 1.0 BTC (~$80k) and
        flooded -2019 Margin-insufficient. With 10_000 equity at 50_000 price
        the correct qty is 0.2 BTC (= 10_000 / 50_000), never 1.0.
        """
        qty = size_to_qty(1.0, equity=10_000.0, price=50_000.0, symbol="BTCUSDT")
        assert qty == pytest.approx(0.2)
        assert qty != 1.0

    def test_krx_integer_shares(self):
        """KRX 6-digit symbol → step 1 → whole-share ROUND_DOWN."""
        # 50% of 1_000_000 KRW = 500_000 / 70_000 price = 7.14 → 7 shares
        qty = size_to_qty(0.5, equity=1_000_000.0, price=70_000.0, symbol="005930")
        assert qty == 7.0

    def test_step_round_down_binance_default(self):
        """Binance default step 0.001 → ROUND_DOWN, never round-up."""
        # 0.123456789 BTC raw → 0.123 after 0.001 ROUND_DOWN
        qty = size_to_qty(
            1.0, equity=0.123456789 * 50_000.0, price=50_000.0, symbol="DOGEUSDT",
        )
        assert qty == pytest.approx(0.123)

    def test_zero_fraction_drops(self):
        assert size_to_qty(0.0, equity=10_000.0, price=50_000.0, symbol="BTCUSDT") is None

    def test_qty_rounds_to_zero_drops(self):
        """Tiny fraction whose coin qty floors below one step → drop (None)."""
        # 0.000001 * 10_000 / 50_000 = 2e-10 BTC → < 0.001 step → 0 → drop
        qty = size_to_qty(0.000001, equity=10_000.0, price=50_000.0, symbol="BTCUSDT")
        assert qty is None

    def test_krx_below_one_share_drops(self):
        # 0.0001 * 1_000_000 / 70_000 = 0.0014 shares → ROUND_DOWN 0 → drop
        qty = size_to_qty(0.0001, equity=1_000_000.0, price=70_000.0, symbol="005930")
        assert qty is None


class TestMinNotionalDrop:
    def test_binance_below_min_notional_drops(self):
        """Binance USDT pair: qty*price below the conservative 5 USDT floor → drop.

        A guaranteed-rejected order is the very class of bug #238 fixed — never
        emit it.
        """
        # 4 USDT notional (< 5 USDT min) at 50_000 price → ~0.00008 BTC
        qty = size_to_qty(
            1.0, equity=4.0, price=50_000.0, symbol="BTCUSDT",
        )
        assert qty is None

    def test_binance_at_or_above_min_notional_kept(self):
        # exactly the min-notional constant → kept (>= boundary)
        equity = float(BINANCE_MIN_NOTIONAL_USDT)
        qty = size_to_qty(1.0, equity=equity, price=10.0, symbol="DOGEUSDT")
        assert qty is not None
        assert qty * 10.0 >= float(BINANCE_MIN_NOTIONAL_USDT) - 1e-9

    def test_krx_has_no_min_notional_floor(self):
        """KRX is share-lot; we apply NO Binance-style notional floor.

        A single 005930 share (~70_000 KRW) is far above any sane floor anyway,
        but the rule is: KRX symbols are governed only by the 1-share step.
        """
        qty = size_to_qty(1.0, equity=70_000.0, price=70_000.0, symbol="005930")
        assert qty == 1.0


class TestGuards:
    def test_non_positive_price_drops(self):
        assert size_to_qty(0.5, equity=10_000.0, price=0.0, symbol="BTCUSDT") is None
        assert size_to_qty(0.5, equity=10_000.0, price=-1.0, symbol="BTCUSDT") is None

    def test_non_positive_equity_drops(self):
        assert size_to_qty(0.5, equity=0.0, price=50_000.0, symbol="BTCUSDT") is None
        assert size_to_qty(0.5, equity=-100.0, price=50_000.0, symbol="BTCUSDT") is None

    def test_unsupported_symbol_drops(self):
        """No step resolvable (EURUSD/AAPL) → drop, never raise in the hot path."""
        assert size_to_qty(0.5, equity=10_000.0, price=1.0, symbol="EURUSD") is None

    def test_fraction_clamped_to_one(self):
        """A fraction > 1 (defensive) is clamped to 1.0 (never over-allocate)."""
        clamped = size_to_qty(5.0, equity=10_000.0, price=50_000.0, symbol="BTCUSDT")
        full = size_to_qty(1.0, equity=10_000.0, price=50_000.0, symbol="BTCUSDT")
        assert clamped == full

    def test_returns_python_float(self):
        qty = size_to_qty(0.05, equity=10_000.0, price=50_000.0, symbol="BTCUSDT")
        assert isinstance(qty, float)
