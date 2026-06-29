"""AirborneFireConsumer 당일 손익 정지 게이트 3종 (2026-06-27).

오전에 번 걸 오후·밤에 토해내는 패턴(24일~ 반복) 방어용 가드:
  1. 당일 이익목표(profit lock) — pnl ≥ +N% of equity → 당일 정지
  2. 고점 반납(give-back lock) — 당일 고점이익의 X% 반납 → 정지
  3. 당일 손실한도(loss lock) — pnl ≤ -N% of equity → 정지
전부 % of equity, KST 자정 리셋, 신규진입만 차단(미청산 TP/SL 유지),
다음날 자동 재개. provider 미주입/equity≤0 이면 fail-open(거래 허용).

상세: docs/specs/airborne-daily-pnl-guards.md.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from live.airborne_fire_consumer import AirborneFireConsumer, AirborneStrategySpec

# KST 2026-06-27 14:00 == UTC 05:00.
_NOW = datetime(2026, 6, 27, 5, 0, 0, tzinfo=timezone.utc)


# ── fakes ──────────────────────────────────────────────────────────────────


class _FakeStore:
    def __init__(self, fires: list[dict]) -> None:
        self._fires = fires

    def load_since(self, since_utc: datetime) -> list[dict]:
        out = []
        for f in self._fires:
            try:
                ts = pd.Timestamp(str(f["ts"]).replace("Z", "+00:00"))
            except (ValueError, TypeError):
                continue
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            if ts.to_pydatetime() >= since_utc:
                out.append(f)
        return sorted(out, key=lambda r: str(r.get("ts", "")))


class _FakeOrch:
    def __init__(self) -> None:
        self.calls: list = []

    def dispatch_fire_entry(self, sid, symbol, side, *, price, ts, equity_usdt):
        self.calls.append({"sid": sid, "symbol": symbol, "side": side})
        return {"sid": sid, "symbol": symbol, "side": side, "qty": 1.0}


class _FakeInstance:
    def __init__(self) -> None:
        self._fired_bar_ts: dict[str, str] = {}

    def _ensure_dedup_loaded(self) -> None:
        pass

    def _persist_dedup(self) -> None:
        pass


def _spec(hours=frozenset(range(24))) -> AirborneStrategySpec:
    return AirborneStrategySpec(
        id="live-airborne-x",
        kst_entry_hours=hours,
        allowed_sides=frozenset({"long", "short"}),
        universe=None,
        btc_filter=False,
        instance=_FakeInstance(),
    )


def _build(*, daily_provider=None, equity=1000.0, fires=None):
    routed: list = []

    async def _route(intents):
        routed.append(list(intents))

    orch = _FakeOrch()
    c = AirborneFireConsumer(
        fire_store=_FakeStore(fires or []),
        orchestrator=orch,
        strategy_specs=[_spec()],
        route_intents=_route,
        equity_provider=(lambda: equity),
        short_block_hours=frozenset(),
        daily_pnl_provider=daily_provider,
    )
    return c, orch, routed


def _fire(symbol, side, ts, fire_close=100.0):
    return {"ts": ts, "symbol": symbol, "side": side,
            "fire_close": fire_close, "trigger": fire_close * 0.99}


# ── 1. 이익목표 (profit lock) ─────────────────────────────────────────────────


def test_profit_lock_triggers_at_target(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_TARGET_PCT", "3.5")
    c, _, _ = _build(daily_provider=lambda: 35.0, equity=1000.0)  # +3.5%
    reason = c._evaluate_daily_halt(_NOW)
    assert reason is not None and "이익목표" in reason


def test_profit_lock_below_target_allows(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_TARGET_PCT", "3.5")
    c, _, _ = _build(daily_provider=lambda: 34.0, equity=1000.0)  # +3.4%
    assert c._evaluate_daily_halt(_NOW) is None


# ── 2. 당일 손실한도 (loss lock) ───────────────────────────────────────────────


def test_loss_lock_triggers(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_LOSS_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_LOSS_LIMIT_PCT", "3.0")
    c, _, _ = _build(daily_provider=lambda: -30.0, equity=1000.0)  # -3.0%
    reason = c._evaluate_daily_halt(_NOW)
    assert reason is not None and "손실한도" in reason


def test_loss_lock_above_limit_allows(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_LOSS_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_LOSS_LIMIT_PCT", "3.0")
    c, _, _ = _build(daily_provider=lambda: -29.0, equity=1000.0)  # -2.9%
    assert c._evaluate_daily_halt(_NOW) is None


# ── 3. 고점 반납 (give-back lock) ──────────────────────────────────────────────


def test_giveback_triggers_after_peak(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_PCT", "40")
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_ARM_PCT", "1.0")
    cell = {"v": 20.0}  # +2% 고점 (arm 1% 도달)
    c, _, _ = _build(daily_provider=lambda: cell["v"], equity=1000.0)
    # 고점 형성 — trigger=20*0.6=12, 현재 20>12 → 아직 허용.
    assert c._evaluate_daily_halt(_NOW) is None
    # 12 이하로 반납 → 정지.
    cell["v"] = 11.0
    reason = c._evaluate_daily_halt(_NOW)
    assert reason is not None and "고점반납" in reason


def test_giveback_not_armed_below_arm_pct(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_ARM_PCT", "1.0")
    cell = {"v": 5.0}  # +0.5% — arm(1%) 미달
    c, _, _ = _build(daily_provider=lambda: cell["v"], equity=1000.0)
    assert c._evaluate_daily_halt(_NOW) is None
    cell["v"] = 0.0  # 반납했지만 고점이 애초에 arm 미달 → 미발동
    assert c._evaluate_daily_halt(_NOW) is None


def test_giveback_peak_resets_next_kst_day(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_PCT", "40")
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_ARM_PCT", "1.0")  # 리셋 로직 검증용 저임계
    cell = {"v": 20.0}
    c, _, _ = _build(daily_provider=lambda: cell["v"], equity=1000.0)
    c._evaluate_daily_halt(_NOW)            # day1 고점 20
    cell["v"] = 11.0
    assert c._evaluate_daily_halt(_NOW) is not None  # day1 반납 → 정지
    # 다음 KST 일 — peak 리셋(=현재 11). trigger=6.6, 11>6.6 → 허용.
    next_day = _NOW + timedelta(days=1)
    assert c._evaluate_daily_halt(next_day) is None


def test_giveback_arm_default_is_3pct(monkeypatch):
    # arm 미설정 → 코드 기본값 3.0 (1.0→3.0, 2026-06-28 사고 수정).
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_LOCK", "1")
    c, _, _ = _build(daily_provider=lambda: 0.0, equity=1000.0)
    assert c._giveback_arm_pct == 3.0


def test_giveback_default_ignores_small_peak_2026_06_28(monkeypatch):
    # 2026-06-28 사고 재현: +1.2% 고점→+0.4% 반납이 기본 arm(3.0)에선 미발동.
    # (arm 1.0 이던 시절엔 여기서 종일 정지 latch 됐었음.)
    monkeypatch.setenv("AIRBORNE_DAILY_GIVEBACK_LOCK", "1")  # arm 기본 3.0
    cell = {"v": 12.0}  # +1.2% of 1000 — arm(3.0) 미달
    c, _, _ = _build(daily_provider=lambda: cell["v"], equity=1000.0)
    assert c._evaluate_daily_halt(_NOW) is None  # 미무장
    cell["v"] = 4.0  # +0.4% (40%+ 반납했지만 애초에 미무장)
    assert c._evaluate_daily_halt(_NOW) is None  # 여전히 미발동


# ── fail-open / off ────────────────────────────────────────────────────────


def test_no_provider_fail_open(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    c, _, _ = _build(daily_provider=None, equity=1000.0)
    assert c._evaluate_daily_halt(_NOW) is None


def test_zero_equity_fail_open(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    c, _, _ = _build(daily_provider=lambda: 999.0, equity=0.0)
    assert c._evaluate_daily_halt(_NOW) is None


def test_all_guards_off_allows(monkeypatch):
    # 토글 미설정(전부 기본 OFF) — provider 있어도 항상 허용.
    c, _, _ = _build(daily_provider=lambda: 999.0, equity=1000.0)
    assert c._evaluate_daily_halt(_NOW) is None


def test_macro_enables_profit_and_giveback_not_loss(monkeypatch):
    # 매크로는 이익목표+고점반납 2종만 ON. 손실한도는 명시 opt-in.
    monkeypatch.setenv("AIRBORNE_DAILY_GUARDS", "1")
    c, _, _ = _build(daily_provider=lambda: 60.0, equity=1000.0)  # +6% > target 5%
    assert c._f_profit_lock and c._f_giveback_lock
    assert not c._f_daily_loss_lock
    assert c._evaluate_daily_halt(_NOW) is not None  # 이익목표(기본 5%) 발동


def test_profit_target_default_is_5pct(monkeypatch):
    # target 미설정 → 코드 기본값 5.0 (3.5→5.0, 2026-06-28 사용자 상향).
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    c, _, _ = _build(daily_provider=lambda: 0.0, equity=1000.0)
    assert c._daily_profit_target_pct == 5.0
    # +4% 미발동, +5% 발동
    c4, _, _ = _build(daily_provider=lambda: 40.0, equity=1000.0)
    assert c4._evaluate_daily_halt(_NOW) is None
    c5, _, _ = _build(daily_provider=lambda: 50.0, equity=1000.0)
    assert c5._evaluate_daily_halt(_NOW) is not None


def test_macro_does_not_trip_loss_limit(monkeypatch):
    # 매크로만 켠 상태에선 큰 손실이어도 손실한도로는 정지 안 함.
    monkeypatch.setenv("AIRBORNE_DAILY_GUARDS", "1")
    c, _, _ = _build(daily_provider=lambda: -80.0, equity=1000.0)  # -8%
    assert c._evaluate_daily_halt(_NOW) is None


def test_loss_lock_explicit_optin_still_works(monkeypatch):
    # 손실한도는 명시 opt-in 으로는 여전히 작동.
    monkeypatch.setenv("AIRBORNE_DAILY_LOSS_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_LOSS_LIMIT_PCT", "3.0")
    c, _, _ = _build(daily_provider=lambda: -40.0, equity=1000.0)  # -4%
    reason = c._evaluate_daily_halt(_NOW)
    assert reason is not None and "손실한도" in reason


# ── sweep 통합: 정지 시 진입 전면 skip ─────────────────────────────────────────


def _fresh_long_fire():
    ts = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    return _fire("SOLUSDT", "long", ts)


def test_sweep_halts_blocks_entry(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_TARGET_PCT", "3.5")
    c, orch, routed = _build(
        daily_provider=lambda: 50.0, equity=1000.0,  # +5% → 정지
        fires=[_fresh_long_fire()],
    )
    entered = asyncio.run(c.sweep_once())
    assert entered == 0
    assert orch.calls == []


def test_sweep_enters_when_guard_not_tripped(monkeypatch):
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_LOCK", "1")
    monkeypatch.setenv("AIRBORNE_DAILY_PROFIT_TARGET_PCT", "3.5")
    c, orch, _ = _build(
        daily_provider=lambda: 0.0, equity=1000.0,  # 0% → 허용
        fires=[_fresh_long_fire()],
    )
    entered = asyncio.run(c.sweep_once())
    assert entered == 1
    assert len(orch.calls) == 1
