"""Unit tests for AirborneTraderConfig."""
from __future__ import annotations

import pytest

from live.airborne_trader.config import AirborneTraderConfig


class TestDefaults:
    def test_defaults_are_valid(self):
        c = AirborneTraderConfig()
        assert c.position_usd == 200.0
        assert c.leverage == 10
        assert c.max_concurrent_positions == 10
        assert c.stop_loss_pct == 0.03
        assert c.take_profit_pct == 0.06
        assert c.kst_entry_hours == frozenset({8, 11, 16, 22})
        assert c.daily_loss_limit_usd == -200.0
        # 2026-05-28 — default: testnet 실 발주 (가짜 돈). dry_run=True 면 로그만.
        assert c.dry_run is False
        assert c.venue == "testnet"
        assert c.base_url.startswith("https://testnet.")
        assert c.cooldown_after_stop_sec == 900.0


class TestValidation:
    def test_position_usd_must_be_positive(self):
        with pytest.raises(ValueError, match="position_usd"):
            AirborneTraderConfig(position_usd=0)
        with pytest.raises(ValueError, match="position_usd"):
            AirborneTraderConfig(position_usd=-10)

    def test_leverage_minimum(self):
        with pytest.raises(ValueError, match="leverage"):
            AirborneTraderConfig(leverage=0)

    def test_max_concurrent_minimum(self):
        with pytest.raises(ValueError, match="max_concurrent_positions"):
            AirborneTraderConfig(max_concurrent_positions=0)

    def test_stop_loss_range(self):
        with pytest.raises(ValueError, match="stop_loss_pct"):
            AirborneTraderConfig(stop_loss_pct=0)
        with pytest.raises(ValueError, match="stop_loss_pct"):
            AirborneTraderConfig(stop_loss_pct=1.5)

    def test_daily_loss_must_be_negative(self):
        with pytest.raises(ValueError, match="daily_loss_limit_usd"):
            AirborneTraderConfig(daily_loss_limit_usd=0)
        with pytest.raises(ValueError, match="daily_loss_limit_usd"):
            AirborneTraderConfig(daily_loss_limit_usd=100)

    def test_invalid_kst_hours(self):
        with pytest.raises(ValueError, match="kst_entry_hours"):
            AirborneTraderConfig(kst_entry_hours=frozenset({24}))
        with pytest.raises(ValueError, match="kst_entry_hours"):
            AirborneTraderConfig(kst_entry_hours=frozenset({-1, 8}))


class TestFromEnv:
    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AIRBORNE_TRADER_POSITION_USD", "100")
        monkeypatch.setenv("AIRBORNE_TRADER_LEVERAGE", "5")
        monkeypatch.setenv("AIRBORNE_TRADER_MAX_POSITIONS", "5")
        monkeypatch.setenv("AIRBORNE_TRADER_DAILY_LOSS_USD", "-100")
        monkeypatch.setenv("AIRBORNE_TRADER_DRY_RUN", "false")
        c = AirborneTraderConfig.from_env()
        assert c.position_usd == 100.0
        assert c.leverage == 5
        assert c.max_concurrent_positions == 5
        assert c.daily_loss_limit_usd == -100.0
        assert c.dry_run is False

    def test_env_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AIRBORNE_TRADER_LEVERAGE", "not-a-number")
        c = AirborneTraderConfig.from_env()
        assert c.leverage == 10  # default

    def test_dry_run_default_is_false(self, monkeypatch):
        """2026-05-28 — default False (실 발주, venue=testnet 가짜 돈)."""
        monkeypatch.delenv("AIRBORNE_TRADER_DRY_RUN", raising=False)
        c = AirborneTraderConfig.from_env()
        assert c.dry_run is False
        # venue=testnet 기본 → testnet base_url
        assert c.venue == "testnet"
        assert c.base_url.startswith("https://testnet.")
