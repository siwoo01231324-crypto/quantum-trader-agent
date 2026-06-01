"""Backtest stub 검증 — daemon 의 SHORT+whitelist 게이트 동작 미러링."""
from __future__ import annotations

import pytest

from src.backtest.strategies.live_airborne_short_whitelist_v1 import (
    IS_DAEMON_ONLY,
    LiveAirborneShortWhitelistV1,
    LiveAirborneShortWhitelistV1Config,
)


def test_daemon_only_flag() -> None:
    assert IS_DAEMON_ONLY is True
    assert LiveAirborneShortWhitelistV1.is_daemon_only is True


def test_strategy_id_matches_spec() -> None:
    assert LiveAirborneShortWhitelistV1.strategy_id == "live-airborne-short-whitelist-v1"
    assert LiveAirborneShortWhitelistV1.paradigm == "live-scanner"
    assert LiveAirborneShortWhitelistV1.side == "short"


def test_default_config_matches_spec() -> None:
    cfg = LiveAirborneShortWhitelistV1Config()
    assert cfg.retrace_ratio == 0.6
    assert cfg.bb_window == 20
    assert cfg.bb_std == 2.0
    assert cfg.min_close_margin == 0.001
    assert cfg.atr_body_mult == 0.3
    assert cfg.stop_loss_pct == 0.03
    assert cfg.take_profit_pct == 0.06
    assert cfg.side == "short"


def test_classvar_stop_tp_match_spec() -> None:
    assert LiveAirborneShortWhitelistV1.stop_loss_pct == 0.03
    assert LiveAirborneShortWhitelistV1.take_profit_pct == 0.06


def test_whitelist_normalized() -> None:
    s = LiveAirborneShortWhitelistV1(whitelist=["arbusdt", "FETUSDT"])
    assert s.whitelist == frozenset({"ARBUSDT", "FETUSDT"})


def test_empty_whitelist_raises() -> None:
    with pytest.raises(ValueError, match="비어"):
        LiveAirborneShortWhitelistV1(whitelist=[])


# ── is_eligible mirrors daemon gates ─────────────────────────────────────


def test_long_side_rejected() -> None:
    s = LiveAirborneShortWhitelistV1(whitelist=["ARBUSDT"])
    assert s.is_eligible(symbol="ARBUSDT", side="long") is False


def test_short_in_whitelist_accepted() -> None:
    s = LiveAirborneShortWhitelistV1(whitelist=["ARBUSDT", "FETUSDT"])
    assert s.is_eligible(symbol="ARBUSDT", side="short") is True
    assert s.is_eligible(symbol="FETUSDT", side="short") is True


def test_short_not_in_whitelist_rejected() -> None:
    s = LiveAirborneShortWhitelistV1(whitelist=["ARBUSDT"])
    assert s.is_eligible(symbol="DOGEUSDT", side="short") is False


def test_case_insensitive_symbol() -> None:
    s = LiveAirborneShortWhitelistV1(whitelist=["ARBUSDT"])
    assert s.is_eligible(symbol="arbusdt", side="short") is True
