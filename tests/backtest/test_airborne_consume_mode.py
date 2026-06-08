"""consume 모드 — 트레이더가 데몬 발화를 그대로 따라 진입 (거래=알림 100%).

자체 airborne 평가 대신 history.jsonl 발화를 소비. AIRBORNE_CONSUME_DAEMON_FIRES=1.
"""
from __future__ import annotations

import pandas as pd
import pytest

from backtest.strategies.live_airborne_bb_reversal_kst_hours import (
    LiveAirborneBbReversalKstHours as KH,
)
from backtest.strategies.live_airborne_short_whitelist_v1 import (
    LiveAirborneShortWhitelistV1 as WL,
)

# _HIST: 17..22:00Z, ts=22:27 → 마감봉 closed=21:00Z(KST 6시, 게이트 내).
# 데몬 매칭 = floor(fire_ts)==22:00Z.
_HIST = pd.DataFrame(
    {"open": 1.0, "high": 1.0, "low": 1.0, "close": [1, 2, 3, 4, 5, 6]},
    index=pd.date_range("2026-06-07T10:00:00Z", periods=6, freq="1h"),
)
_CTX = {"ts": pd.Timestamp("2026-06-07T15:27:00Z"), "live_run": True,
        "market_snapshot": {"symbol": "X", "history": _HIST}}


class _Store:
    def __init__(self, fires): self._f = fires
    @property
    def path(self): return type("P", (), {"exists": staticmethod(lambda: True)})()
    def load_since(self, s): return self._f


def _setup(strat, tmp_path, fires):
    strat._dedup_path = lambda: tmp_path / "d.json"
    strat._get_fire_store = lambda: _Store(fires)


_SHORT_FIRE = [{"symbol": "X", "side": "short", "ts": "2026-06-07T15:00:30+00:00"}]
_LONG_FIRE = [{"symbol": "X", "side": "long", "ts": "2026-06-07T15:00:30+00:00"}]


@pytest.mark.asyncio
async def test_consume_short_whitelist_enters_daemon_short(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRBORNE_CONSUME_DAEMON_FIRES", "1")
    wl = WL(); _setup(wl, tmp_path, _SHORT_FIRE)
    sig = await wl.on_bar(_CTX)
    assert sig.action == "sell" and "consume_daemon_fire:short" in sig.reason


@pytest.mark.asyncio
async def test_consume_short_whitelist_no_fire_holds(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRBORNE_CONSUME_DAEMON_FIRES", "1")
    wl = WL(); _setup(wl, tmp_path, [])
    sig = await wl.on_bar(_CTX)
    assert sig.action == "hold" and "no_daemon_fire" in sig.reason


@pytest.mark.asyncio
async def test_consume_short_whitelist_ignores_long_fire(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRBORNE_CONSUME_DAEMON_FIRES", "1")
    wl = WL(); _setup(wl, tmp_path, _LONG_FIRE)  # short-wl 은 long 안 받음
    sig = await wl.on_bar(_CTX)
    assert sig.action == "hold"


@pytest.mark.asyncio
async def test_consume_dedup_once_per_bar(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRBORNE_CONSUME_DAEMON_FIRES", "1")
    wl = WL(); _setup(wl, tmp_path, _SHORT_FIRE)
    s1 = await wl.on_bar(_CTX)
    s2 = await wl.on_bar(_CTX)
    assert s1.action == "sell"
    assert s2.action == "hold" and "already_entered_bar" in s2.reason


@pytest.mark.asyncio
async def test_consume_kst_hours_enters_daemon_short(tmp_path, monkeypatch):
    monkeypatch.setenv("AIRBORNE_CONSUME_DAEMON_FIRES", "1")
    kh = KH(btc_trend_filter_enabled=False); _setup(kh, tmp_path, _SHORT_FIRE)
    sig = await kh.on_bar(_CTX)
    assert sig.action == "sell"


@pytest.mark.asyncio
async def test_consume_off_uses_own_eval(tmp_path, monkeypatch):
    """consume OFF → 기존 자체평가 path (게이트 모드). env 0."""
    monkeypatch.setenv("AIRBORNE_CONSUME_DAEMON_FIRES", "0")
    wl = WL(); _setup(wl, tmp_path, _SHORT_FIRE)
    sig = await wl.on_bar(_CTX)
    # _HIST 는 short fire 패턴 아님 → 자체평가는 hold (consume 아님)
    assert sig.action == "hold" and "consume_daemon_fire" not in (sig.reason or "")
