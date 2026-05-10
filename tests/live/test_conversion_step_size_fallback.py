"""Step-size resolver fallbacks for universe-wide live trading (#227 follow-up).

Verifies that ``get_step_size`` resolves KRX 6-digit codes and Binance USDT
pairs without explicit registry entries, while preserving the exact behaviour
of the existing whitelist for BTC/ETH/SOL.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.live.conversion import (
    SYMBOL_STEP_SIZES,
    _BINANCE_USDT_DEFAULT_STEP,
    get_step_size,
    intent_to_order_request,
)
from src.portfolio.order_intent import OrderIntent


class TestGetStepSize:
    def test_explicit_whitelist_overrides_everything(self):
        # SOLUSDT would match the Binance USDT fallback (0.001), but the
        # explicit entry says 1 — override must win.
        assert get_step_size("SOLUSDT") == Decimal("1")
        assert get_step_size("BTCUSDT") == Decimal("0.001")
        assert get_step_size("ETHUSDT") == Decimal("0.001")

    def test_krx_6_digit_code_returns_one(self):
        for code in ["005930", "000660", "035720", "035420", "000000", "999999"]:
            assert get_step_size(code) == Decimal("1"), f"KRX {code} step should be 1"

    def test_krx_5_digit_or_letters_falls_through(self):
        # 5 digits → not KRX shape
        assert get_step_size("00593") is None
        # contains letters → not KRX shape
        assert get_step_size("00593A") is None

    def test_binance_usdt_pair_uses_default_step(self):
        for sym in ["DOGEUSDT", "ADAUSDT", "MATICUSDT", "1000PEPEUSDT"]:
            assert get_step_size(sym) == _BINANCE_USDT_DEFAULT_STEP

    def test_unsupported_symbol_returns_none(self):
        assert get_step_size("EURUSD") is None
        assert get_step_size("AAPL") is None
        assert get_step_size("USDT") is None  # bare USDT (only 4 chars)
        assert get_step_size("") is None

    def test_explicit_registry_addition_works(self, monkeypatch):
        """Tests can extend SYMBOL_STEP_SIZES at runtime — same pattern that
        broker config wiring will use in production."""
        monkeypatch.setitem(SYMBOL_STEP_SIZES, "FOOUSDT", Decimal("10"))
        assert get_step_size("FOOUSDT") == Decimal("10")
        # KRX path still works
        assert get_step_size("005930") == Decimal("1")


class TestIntentToOrderRequest:
    def test_krx_intent_passes_quantization(self):
        intent = OrderIntent(
            strategy_id="live_rsi",
            symbol="005930",
            side="buy",
            qty=10.7,
            reason="test",
        )
        req = intent_to_order_request(intent, idempotency_key="k:1")
        # KRX step=1 → 10.7 ROUND_DOWN → 10
        assert req.qty == Decimal("10")
        assert req.symbol == "005930"

    def test_binance_intent_passes_quantization(self):
        intent = OrderIntent(
            strategy_id="live_rsi",
            symbol="DOGEUSDT",
            side="buy",
            qty=123.456789,
            reason="test",
        )
        req = intent_to_order_request(intent, idempotency_key="k:2")
        # Binance default step=0.001 → 123.456 (ROUND_DOWN at 3 decimal places)
        assert req.qty == Decimal("123.456")

    def test_truly_unsupported_still_raises(self):
        intent = OrderIntent(
            strategy_id="live_rsi",
            symbol="EURUSD",
            side="buy",
            qty=1.0,
            reason="test",
        )
        with pytest.raises(ValueError, match="Unsupported symbol"):
            intent_to_order_request(intent, idempotency_key="k:3")
