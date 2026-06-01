"""AirborneShortWhitelistRisk — SHORT + whitelist 게이트 검증.

기존 ``AirborneTraderRisk`` 의 모든 게이트는 super().evaluate() 가 처리하므로
본 테스트는 *새 2개 게이트* 만 집중 검증.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.live.airborne_fire_listener import FireRecord
from src.live.airborne_short_whitelist.risk import AirborneShortWhitelistRisk
from src.live.airborne_trader.config import AirborneTraderConfig


@dataclass
class _StubState:
    """AirborneTraderRisk 가 호출하는 메서드만 stub 구현."""
    is_kill: bool = False
    open_count: int = 0
    today_pnl: float = 0.0
    open_by_symbol: dict = field(default_factory=dict)
    last_stop_iso: dict = field(default_factory=dict)

    def is_kill_switch_active(self) -> bool:
        return self.is_kill

    def last_kill_switch_event(self) -> dict | None:
        return None

    def count_open(self) -> int:
        return self.open_count

    def find_open_by_symbol(self, sym: str):
        return self.open_by_symbol.get(sym)

    def last_stop_close_ts(self, sym: str):
        return self.last_stop_iso.get(sym)

    def realized_pnl_since(self, midnight_utc_iso: str) -> float:
        return self.today_pnl


def _now() -> datetime:
    # KST 11:30 = UTC 02:30 → kst_entry_hours={8,11,16,22} 에 11 포함
    # (fire.ts = now - 30s = UTC 02:29:30 → KST 11:29 → hour=11 통과)
    return datetime(2026, 6, 1, 2, 30, 0, tzinfo=timezone.utc)


def _fire(*, side: str = "short", symbol: str = "ARBUSDT",
          ts: datetime | None = None) -> FireRecord:
    return FireRecord(
        ts=ts or _now() - timedelta(seconds=30),
        symbol=symbol, side=side,
        fire_close=1.234, trigger=1.230,
    )


def _make_risk(active: list[str]) -> AirborneShortWhitelistRisk:
    cfg = AirborneTraderConfig(dry_run=True)
    state = _StubState()
    return AirborneShortWhitelistRisk(cfg, state, active_symbols=active)


# ── Gate -2: side filter ─────────────────────────────────────────────────


def test_long_side_rejected() -> None:
    risk = _make_risk(["ARBUSDT"])
    d = risk.evaluate(_fire(side="long"), now_utc=_now())
    assert d.ok is False
    assert d.reason.startswith("short_only:side=long")


def test_short_side_passes_gate_minus2() -> None:
    risk = _make_risk(["ARBUSDT"])
    d = risk.evaluate(_fire(side="short"), now_utc=_now())
    assert d.ok is True


def test_unknown_side_rejected() -> None:
    risk = _make_risk(["ARBUSDT"])
    d = risk.evaluate(_fire(side="weird"), now_utc=_now())
    assert d.ok is False
    assert "short_only" in d.reason


# ── Gate -1: whitelist filter ────────────────────────────────────────────


def test_symbol_not_whitelisted_rejected() -> None:
    risk = _make_risk(["ARBUSDT", "FETUSDT"])
    d = risk.evaluate(_fire(side="short", symbol="DOGEUSDT"), now_utc=_now())
    assert d.ok is False
    assert d.reason.startswith("not_whitelisted:DOGEUSDT")


def test_symbol_whitelisted_passes() -> None:
    risk = _make_risk(["ARBUSDT", "FETUSDT"])
    d = risk.evaluate(_fire(side="short", symbol="FETUSDT"), now_utc=_now())
    assert d.ok is True


def test_whitelist_lowercase_normalized() -> None:
    risk = _make_risk(["arbusdt"])  # lower
    d = risk.evaluate(_fire(side="short", symbol="ARBUSDT"), now_utc=_now())
    assert d.ok is True


def test_fire_lowercase_symbol_matches_uppercase_whitelist() -> None:
    risk = _make_risk(["ARBUSDT"])
    # FireRecord 는 이미 대문자가 표준이지만 방어적으로 lower 도 OK
    d = risk.evaluate(_fire(side="short", symbol="arbusdt"), now_utc=_now())
    assert d.ok is True


# ── 빈 whitelist ─────────────────────────────────────────────────────────


def test_empty_active_set_raises_on_init() -> None:
    cfg = AirborneTraderConfig(dry_run=True)
    state = _StubState()
    with pytest.raises(ValueError, match="active_symbols"):
        AirborneShortWhitelistRisk(cfg, state, active_symbols=[])


# ── 게이트 순서 확인 — 새 게이트가 기존 게이트보다 먼저 ───────────────


def test_short_gate_fires_before_kill_switch() -> None:
    """LONG fire 는 kill switch 가 활성화돼도 short_only 사유로 reject.
    (둘 다 reject 이지만 새 게이트가 더 빠르게 평가됨을 확인)
    """
    cfg = AirborneTraderConfig(dry_run=True)
    state = _StubState(is_kill=True)
    risk = AirborneShortWhitelistRisk(cfg, state, active_symbols=["ARBUSDT"])
    d = risk.evaluate(_fire(side="long"), now_utc=_now())
    assert d.ok is False
    # short_only 이 먼저 평가됐어야 함 (kill_switch reason 아님)
    assert d.reason.startswith("short_only")


def test_whitelist_gate_fires_before_kst_hour() -> None:
    """KST hour 가 entry 시간 아닐 때도 not_whitelisted 가 먼저."""
    risk = _make_risk(["ARBUSDT"])
    # KST 13시 = UTC 04시 → entry hours {8,11,16,22} 아님
    bad_hour = datetime(2026, 6, 1, 4, 0, 0, tzinfo=timezone.utc)
    d = risk.evaluate(
        _fire(side="short", symbol="DOGEUSDT", ts=bad_hour - timedelta(seconds=10)),
        now_utc=bad_hour,
    )
    assert d.ok is False
    assert d.reason.startswith("not_whitelisted")


# ── 기존 게이트 위임 동작 확인 ─────────────────────────────────────────


def test_kst_hour_still_enforced_via_super() -> None:
    """SHORT + whitelist 통과해도 KST hour 가 wrong 이면 super 가 reject."""
    risk = _make_risk(["ARBUSDT"])
    # KST 14시 = UTC 05시 → not in {8,11,16,22}
    bad = datetime(2026, 6, 1, 5, 0, 0, tzinfo=timezone.utc)
    d = risk.evaluate(
        _fire(side="short", symbol="ARBUSDT", ts=bad - timedelta(seconds=10)),
        now_utc=bad,
    )
    assert d.ok is False
    assert "kst_hour" in d.reason


def test_active_symbols_property() -> None:
    risk = _make_risk(["ARBUSDT", "FETUSDT", "uniusdt"])
    assert risk.active_symbols == frozenset({"ARBUSDT", "FETUSDT", "UNIUSDT"})
