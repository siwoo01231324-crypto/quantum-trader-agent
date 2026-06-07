"""Unit tests for scripts/airborne_alert_daemon — dispatcher + cooldown + payload."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import airborne_alert_daemon as daemon  # noqa: E402
from brokers.binance.market_ws import KlineEvent  # noqa: E402
from signals.airborne_bb_reversal import AirborneSetup  # noqa: E402


def _make_kline_event(*, symbol="BTCUSDT", interval="1h", close=95.0, open_time_ms=1_700_000_000_000):
    return KlineEvent(
        symbol=symbol, interval=interval,
        open_time=open_time_ms,
        close_time=open_time_ms + 3_599_999,
        open=91.0, high=96.0, low=88.0,
        close=close, volume=1000.0,
        is_closed=True,
    )


def _make_5m_history(n: int = 5, ascending: bool = True) -> pd.DataFrame:
    closes = list(np.linspace(94, 95, n)) if ascending else list(np.linspace(95, 94, n))
    idx = pd.date_range("2026-01-01", periods=n, freq="5min")
    return pd.DataFrame(
        {"open": closes, "high": closes, "low": closes, "close": closes, "volume": [1.0] * n},
        index=idx,
    )


def test_dispatch_fire_calls_notify_with_payload():
    state = daemon.SymbolState()
    state.history_5m = _make_5m_history(ascending=True)
    ev = _make_kline_event(close=95.0)
    setup = AirborneSetup(breakout_index=42, base=98.0, extreme=88.0)

    captured: list = []

    def spy(level, title, body, fields):
        captured.append((level, title, body, fields))

    dispatched = daemon.dispatch_fire(
        symbol="BTCUSDT", side="long", state=state, ev=ev,
        setup=setup, trigger=92.0, dry_run=False, notify_fn=spy,
    )

    assert dispatched is True
    assert len(captured) == 1
    level, title, body, fields = captured[0]
    assert level == "info"
    # 2026-06-04 — title 이모지 + 한글 본문 으로 개편.
    assert "롱" in title and "BTCUSDT" in title and "1시간봉" in title
    assert "🟢" in title or "⬆️" in title  # long 이모지
    assert "진입가(40% 되돌림): 92" in body
    assert "돌파 시작가: 98" in body and "최저점: 88" in body  # long → 최저점
    assert "🤖 봇 진입 가능성" in body  # NOTICE 섹션
    assert fields["symbol"] == "BTCUSDT"
    assert fields["side"] == "long"
    assert fields["fire_close"] == "95"
    assert fields["trigger"] == "92"
    assert fields["base"] == "98"
    assert fields["extreme"] == "88"
    assert fields["5m_preview"] == "ascending"
    assert "rejected" in fields["note"].lower()


def test_dispatch_fire_dry_run_does_not_call_notify():
    state = daemon.SymbolState()
    state.history_5m = _make_5m_history()
    ev = _make_kline_event()
    setup = AirborneSetup(breakout_index=0, base=100.0, extreme=88.0)

    spy_calls: list = []
    dispatched = daemon.dispatch_fire(
        symbol="BTCUSDT", side="long", state=state, ev=ev,
        setup=setup, trigger=92.0, dry_run=True,
        notify_fn=lambda *a, **kw: spy_calls.append(a),
    )
    assert dispatched is True
    assert spy_calls == []  # dry_run → stdout, no notify


def test_dispatch_fire_cooldown_suppresses_second_fire():
    state = daemon.SymbolState()
    state.history_5m = _make_5m_history()
    setup = AirborneSetup(breakout_index=0, base=100.0, extreme=88.0)
    t0 = 1_700_000_000_000
    spy_calls: list = []

    # First fire
    d1 = daemon.dispatch_fire(
        symbol="BTCUSDT", side="long", state=state,
        ev=_make_kline_event(open_time_ms=t0),
        setup=setup, trigger=92.0, dry_run=False,
        notify_fn=lambda *a, **kw: spy_calls.append(a),
    )
    # Second fire 1h later — INSIDE cooldown (4h window)
    d2 = daemon.dispatch_fire(
        symbol="BTCUSDT", side="long", state=state,
        ev=_make_kline_event(open_time_ms=t0 + 3_600_000),
        setup=setup, trigger=92.0, dry_run=False,
        notify_fn=lambda *a, **kw: spy_calls.append(a),
    )
    # Third fire >4h later — OUTSIDE cooldown
    d3 = daemon.dispatch_fire(
        symbol="BTCUSDT", side="long", state=state,
        ev=_make_kline_event(open_time_ms=t0 + 4 * 3_600_000),
        setup=setup, trigger=92.0, dry_run=False,
        notify_fn=lambda *a, **kw: spy_calls.append(a),
    )

    assert d1 is True
    assert d2 is False  # cooldown
    assert d3 is True
    assert len(spy_calls) == 2


def test_dispatch_fire_cooldown_long_short_independent():
    """long fire should NOT cool down short fires of the same symbol (different sides)."""
    state = daemon.SymbolState()
    state.history_5m = _make_5m_history()
    setup = AirborneSetup(breakout_index=0, base=100.0, extreme=88.0)
    t0 = 1_700_000_000_000
    calls: list = []

    daemon.dispatch_fire(
        symbol="BTCUSDT", side="long", state=state,
        ev=_make_kline_event(open_time_ms=t0),
        setup=setup, trigger=92.0, dry_run=False,
        notify_fn=lambda *a, **kw: calls.append(a),
    )
    # 1h later: short fire — different side, must NOT be cooled down
    dispatched = daemon.dispatch_fire(
        symbol="BTCUSDT", side="short", state=state,
        ev=_make_kline_event(open_time_ms=t0 + 3_600_000),
        setup=setup, trigger=110.0, dry_run=False,
        notify_fn=lambda *a, **kw: calls.append(a),
    )
    assert dispatched is True
    assert len(calls) == 2


def test_evaluate_and_dispatch_warmup_returns_no_fire():
    """Insufficient history (<22 bars) → both False."""
    state = daemon.SymbolState()
    state.history_1h = pd.DataFrame({
        "open": [100], "high": [100], "low": [99], "close": [100], "volume": [1.0],
    }, index=pd.date_range("2026-01-01", periods=1, freq="1h"))
    ev = _make_kline_event()
    spy_calls: list = []
    long_fired, short_fired = daemon.evaluate_and_dispatch(
        symbol="BTCUSDT", state=state, ev=ev, dry_run=False,
        notify_fn=lambda *a, **kw: spy_calls.append(a),
    )
    assert (long_fired, short_fired) == (False, False)
    assert spy_calls == []


def test_five_min_trend_preview():
    asc = _make_5m_history(n=5, ascending=True)
    dsc = _make_5m_history(n=5, ascending=False)
    mixed = asc.copy()
    mixed.iloc[-2, mixed.columns.get_loc("close")] = mixed["close"].iloc[-1] + 10  # zig-zag
    assert daemon._five_min_trend_preview(asc, lookback=3) == "ascending"
    assert daemon._five_min_trend_preview(dsc, lookback=3) == "descending"
    assert daemon._five_min_trend_preview(mixed, lookback=3) == "mixed"
    assert daemon._five_min_trend_preview(asc.iloc[:1], lookback=3) == "n/a"


def test_append_bar_replaces_existing_open_time():
    state = daemon.SymbolState()
    ev1 = _make_kline_event(close=95.0, open_time_ms=1_700_000_000_000)
    state.history_1h = daemon._append_bar(state.history_1h, ev1, max_bars=10)
    assert len(state.history_1h) == 1
    # Same open_time, updated close (e.g., re-emit of confirmed bar)
    ev1b = _make_kline_event(close=96.0, open_time_ms=1_700_000_000_000)
    state.history_1h = daemon._append_bar(state.history_1h, ev1b, max_bars=10)
    assert len(state.history_1h) == 1
    assert state.history_1h["close"].iloc[-1] == 96.0


def test_append_bar_evicts_oldest_beyond_max():
    state = daemon.SymbolState()
    for i in range(5):
        ev = _make_kline_event(open_time_ms=1_700_000_000_000 + i * 3_600_000)
        state.history_1h = daemon._append_bar(state.history_1h, ev, max_bars=3)
    assert len(state.history_1h) == 3


# =============================================================================
# compute_universe_diff (Task #13 — universe refresh)
# =============================================================================

def test_diff_added_removed_unchanged():
    added, removed, unchanged = daemon.compute_universe_diff(
        ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        ["ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT"],
    )
    assert added == ["DOGEUSDT", "XRPUSDT"]
    assert removed == ["BTCUSDT"]
    assert unchanged == ["ETHUSDT", "SOLUSDT"]


def test_diff_first_cycle_empty_prev():
    """First refresh: prev=[] → everything is 'added', nothing removed/unchanged."""
    added, removed, unchanged = daemon.compute_universe_diff(
        [], ["BTCUSDT", "ETHUSDT"],
    )
    assert added == ["BTCUSDT", "ETHUSDT"]
    assert removed == []
    assert unchanged == []


def test_diff_universe_unchanged():
    """Same universe → no added/removed, everything unchanged."""
    universe = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    added, removed, unchanged = daemon.compute_universe_diff(universe, universe)
    assert added == []
    assert removed == []
    assert unchanged == universe


def test_diff_universe_fully_replaced():
    added, removed, unchanged = daemon.compute_universe_diff(
        ["BTCUSDT", "ETHUSDT"],
        ["DOGEUSDT", "XRPUSDT"],
    )
    assert added == ["DOGEUSDT", "XRPUSDT"]
    assert removed == ["BTCUSDT", "ETHUSDT"]
    assert unchanged == []


def test_diff_preserves_curr_ordering_for_added_and_unchanged():
    """added/unchanged follow curr ordering (caller may rely on this for log readability)."""
    added, removed, unchanged = daemon.compute_universe_diff(
        ["B", "A"],
        ["C", "A", "B", "D"],
    )
    # curr-order traversal: C(new), A(old), B(old), D(new)
    assert added == ["C", "D"]
    assert unchanged == ["A", "B"]
    assert removed == []


def test_diff_removed_follows_prev_ordering():
    added, removed, unchanged = daemon.compute_universe_diff(
        ["A", "B", "C", "D"],
        ["B"],
    )
    assert removed == ["A", "C", "D"]  # prev order, minus B
    assert unchanged == ["B"]
    assert added == []


# =============================================================================
# REST polling mode — Korean-IP region-block safe path
# =============================================================================

import asyncio as _asyncio  # noqa: E402
import datetime as _dt  # noqa: E402

import pytest  # noqa: E402


def test_next_polling_wakeup_before_boundary():
    """05:00:25 → 05:00:30 (same hour boundary +30s)."""
    now = _dt.datetime(2026, 5, 21, 5, 0, 25, tzinfo=_dt.timezone.utc)
    assert daemon._next_polling_wakeup(now) == _dt.datetime(
        2026, 5, 21, 5, 0, 30, tzinfo=_dt.timezone.utc,
    )


def test_next_polling_wakeup_at_boundary():
    """05:00:30 exact → next hour 06:00:30 (strictly after now_dt)."""
    now = _dt.datetime(2026, 5, 21, 5, 0, 30, tzinfo=_dt.timezone.utc)
    assert daemon._next_polling_wakeup(now) == _dt.datetime(
        2026, 5, 21, 6, 0, 30, tzinfo=_dt.timezone.utc,
    )


def test_next_polling_wakeup_after_boundary():
    """05:00:35 → 06:00:30 (next hour)."""
    now = _dt.datetime(2026, 5, 21, 5, 0, 35, tzinfo=_dt.timezone.utc)
    assert daemon._next_polling_wakeup(now) == _dt.datetime(
        2026, 5, 21, 6, 0, 30, tzinfo=_dt.timezone.utc,
    )


def test_next_polling_wakeup_mid_hour():
    """05:30:00 → 06:00:30 (skip to next boundary)."""
    now = _dt.datetime(2026, 5, 21, 5, 30, 0, tzinfo=_dt.timezone.utc)
    assert daemon._next_polling_wakeup(now) == _dt.datetime(
        2026, 5, 21, 6, 0, 30, tzinfo=_dt.timezone.utc,
    )


def test_next_polling_wakeup_microsecond_precision():
    """05:00:30.000001 → next hour (the boundary instant has already passed)."""
    now = _dt.datetime(2026, 5, 21, 5, 0, 30, 1, tzinfo=_dt.timezone.utc)
    assert daemon._next_polling_wakeup(now) == _dt.datetime(
        2026, 5, 21, 6, 0, 30, tzinfo=_dt.timezone.utc,
    )


def test_run_daemon_rejects_unknown_mode():
    """Invalid mode arg should raise ValueError (no silent fallthrough)."""
    with pytest.raises(ValueError, match="unknown mode"):
        _asyncio.run(daemon.run_daemon(top_n=5, mode="bogus"))


# ── 2026-06-04 알림 가독성 개선 (이모지 / 한글 / 봇 전략 안내) ────────────────


def test_kst_hour_from_open_time_returns_korean_time():
    # 2026-01-01 00:00 UTC = 2026-01-01 09:00 KST
    open_time_ms = int(pd.Timestamp("2026-01-01T00:00:00", tz="UTC").value // 1_000_000)
    assert daemon._kst_hour_from_open_time(open_time_ms) == 9


def test_strategy_notice_long_fires_kst_hours_only_at_gate():
    # KST 7시 LONG — v3 게이트 내 진입 예정, short-whitelist 는 LONG 미지원
    notice = daemon._format_strategy_notice(
        side="long", kst_hour=7, symbol="BTCUSDT",
    )
    assert "kst-hours" in notice
    assert "✅ 진입 예정" in notice
    assert "LONG 미지원" in notice or "숏 전용" in notice


def test_strategy_notice_long_blocked_outside_gate():
    # KST 16시 — v3 게이트 밖 (v2 에선 통과, v3 에선 차단)
    notice = daemon._format_strategy_notice(
        side="long", kst_hour=16, symbol="BTCUSDT",
    )
    assert "❌" in notice
    assert "1/2/3/6/7/8/23" in notice


def test_strategy_notice_short_at_kst_8_only_kst_hours_eligible():
    # KST 8 — kst-hours 통과, short-whitelist 는 8시 제외
    notice = daemon._format_strategy_notice(
        side="short", kst_hour=8, symbol="RIFUSDT",  # whitelist 가정
    )
    assert "kst-hours (양방향): ✅ 진입 예정" in notice
    # short-whitelist 는 화이트리스트 외 (BTCUSDT 같은 종목) 또는 8시 게이트 외
    assert "❌" in notice.split("short-whitelist")[1]


def test_dispatch_fire_title_has_short_emoji_and_korean():
    spy_calls: list[tuple[str, str, str, dict]] = []

    def spy(level: str, title: str, body: str, fields: dict) -> None:
        spy_calls.append((level, title, body, fields))

    state = daemon.SymbolState()
    ev = _make_kline_event(symbol="RIFUSDT", close=0.0857)
    setup = AirborneSetup(base=0.0900, extreme=0.0913, breakout_index=8)

    daemon.dispatch_fire(
        symbol="RIFUSDT", side="short", state=state, ev=ev,
        setup=setup, trigger=0.0907, dry_run=False, notify_fn=spy,
    )

    assert len(spy_calls) == 1
    _, title, body, _ = spy_calls[0]
    assert "숏" in title and "RIFUSDT" in title
    assert "🔴" in title or "⬇️" in title
    assert "🤖 봇 진입 가능성" in body
    assert "현재가:" in body and "진입가(40% 되돌림):" in body
    assert "최고점:" in body  # short → 최고점 label


# ── #380: 심볼 정규화 + 화이트리스트 매칭 ────────────────────────────────────
def test_norm_symbol_strips_1000_multiplier():
    """Binance '1000SHIBUSDT' → Bitget/whitelist 단위 'SHIBUSDT'."""
    assert daemon._norm_symbol("1000SHIBUSDT") == "SHIBUSDT"
    assert daemon._norm_symbol("SHIBUSDT") == "SHIBUSDT"
    assert daemon._norm_symbol("1000PEPEUSDT") == "PEPEUSDT"
    # 프리픽스 유지 종목도 양쪽 정규화로 일관 (1000LUNC↔LUNC)
    assert daemon._norm_symbol("1000LUNCUSDT") == "LUNCUSDT"
    # 짧은 심볼 / 정상 심볼은 그대로
    assert daemon._norm_symbol("BTCUSDT") == "BTCUSDT"


def test_in_trading_universe_matches_across_1000_prefix(monkeypatch):
    """#380 회귀 — fire 가 '1000SHIBUSDT'(Binance)로 와도 top-100 의
    'SHIBUSDT'(Bitget)와 정규화 매칭. 이전엔 직접 비교라 '외 종목' 오알림."""
    import portfolio.bitget_top_dynamic as btd
    monkeypatch.setattr(btd, "get_top_n_symbols", lambda n=100: ["SHIBUSDT", "BTCUSDT", "ETHUSDT"])
    assert daemon._in_trading_universe("1000SHIBUSDT") is True
    assert daemon._in_trading_universe("BTCUSDT") is True
    # top-100 밖 종목은 False
    assert daemon._in_trading_universe("NOTREALUSDT") is False


def test_in_trading_universe_fetch_failure_is_permissive(monkeypatch):
    """#380 — top-100 조회 실패 시 보수적으로 True (오알림보다 누락-경고 회피)."""
    import portfolio.bitget_top_dynamic as btd

    def _boom(n=100):
        raise RuntimeError("net down")

    monkeypatch.setattr(btd, "get_top_n_symbols", _boom)
    assert daemon._in_trading_universe("ANYUSDT") is True


def test_short_wl_gate_is_24h():
    """#380 — short-whitelist 게이트가 24시간 (제외시간 없음)."""
    assert len(daemon._KST_HOURS_SHORT_WL) == 24
    for h in (4, 6, 7, 8, 13):  # 이전 제외시간도 이제 포함
        assert h in daemon._KST_HOURS_SHORT_WL


def test_short_wl_notice_in_top100_shows_enter(monkeypatch):
    """#380 통합 — top-100 에 든 '1000SHIBUSDT' short fire 안내는 '진입 예정',
    'TOP100 외 종목' 안 뜸."""
    import portfolio.bitget_top_dynamic as btd
    monkeypatch.setattr(btd, "get_top_n_symbols", lambda n=100: ["SHIBUSDT"])
    notice = daemon._format_strategy_notice(
        side="short", kst_hour=18, symbol="1000SHIBUSDT",
    )
    assert "TOP100 외 종목" not in notice
    assert "✅ 진입 예정" in notice
