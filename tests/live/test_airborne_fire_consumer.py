"""AirborneFireConsumer — 봉루프 decouple 발화 직접구동 (2026-06-11).

게이트(도착시각 KST hour) / side 필터 / freshness / dedup / BTC 필터 / universe
필터 / dispatch 호출을 synthetic fire 로 검증. 실제 orchestrator/store 대신
fake 를 주입(생성자 DI).

상세: docs/specs/airborne-fire-driven-consume.md.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

from live.airborne_fire_consumer import AirborneFireConsumer, AirborneStrategySpec


# ── fakes ────────────────────────────────────────────────────────────────────


class _FakeStore:
    """load_since 가 주어진 fire dict 들 중 since 이후를 ts 오름차순 반환."""

    def __init__(self, fires: list[dict]) -> None:
        self._fires = fires

    def load_since(self, since_utc: datetime) -> list[dict]:
        # 실제 AirborneFireStore 처럼 파싱 불가 ts 는 skip (never raise).
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
    """dispatch_fire_entry 호출을 기록 + 매 호출 OrderIntent-like 반환."""

    def __init__(self, *, return_intent: bool = True) -> None:
        self.calls: list = []
        self._return_intent = return_intent

    def dispatch_fire_entry(self, sid, symbol, side, *, price, ts, equity_usdt):
        self.calls.append({
            "sid": sid, "symbol": symbol, "side": side,
            "price": price, "ts": ts, "equity_usdt": equity_usdt,
        })
        if not self._return_intent:
            return None
        # OrderIntent-like — route 가 list 로 받기만 하면 됨.
        return {"sid": sid, "symbol": symbol, "side": side, "qty": 1.0}


class _FakeStrategyInstance:
    """dedup 공유용 fake — _ensure_dedup_loaded / _fired_bar_ts / _persist_dedup."""

    def __init__(self) -> None:
        self._fired_bar_ts: dict[str, str] = {}
        self.persisted = 0

    def _ensure_dedup_loaded(self) -> None:
        pass

    def _persist_dedup(self) -> None:
        self.persisted += 1


def _spec(
    sid="live-airborne-x",
    hours=frozenset({1, 2, 3, 5, 6, 7, 8, 23}),
    sides=frozenset({"long", "short"}),
    universe=None,
    btc_filter=False,
    instance=None,
) -> AirborneStrategySpec:
    return AirborneStrategySpec(
        id=sid,
        kst_entry_hours=hours,
        allowed_sides=sides,
        universe=universe,
        btc_filter=btc_filter,
        instance=instance if instance is not None else _FakeStrategyInstance(),
    )


def _consumer(store, orch, specs, **kw):
    routed: list = []

    async def _route(intents):
        routed.append(list(intents))

    c = AirborneFireConsumer(
        fire_store=store,
        orchestrator=orch,
        strategy_specs=specs,
        route_intents=_route,
        equity_provider=kw.pop("equity_provider", lambda: 10_000.0),
        btc_ohlcv_provider=kw.pop("btc_ohlcv_provider", None),
        notify=kw.pop("notify", None),
        freshness_sec=kw.pop("freshness_sec", 600.0),
        long_freshness_sec=kw.pop("long_freshness_sec", 90.0),
        # 테스트 기본은 빈 set — wall-clock 이 07시여도 기존 테스트가 안 깨지게.
        # short-block 전용 테스트만 명시적으로 hours 지정.
        short_block_hours=kw.pop("short_block_hours", frozenset()),
        interval_sec=kw.pop("interval_sec", 15.0),
        klines_fetcher=kw.pop("klines_fetcher", None),
    )
    return c, routed


def _stub_fetcher(chg_pct, range_pct=0.0):
    """24h 변화 chg_pct% + 1h 변동폭 range_pct% 합성 1h df fetcher (필터 테스트용).
    chg_pct=None → None 반환(미상장/실패 → fail-open 검증). range_pct 기본 0(변동성
    필터 미발동)이라 기존 모멘텀 테스트 불변."""
    async def f(symbol):
        if chg_pct is None:
            return None
        base = 100.0
        closes = [base] * 25 + [base * (1 + chg_pct / 100.0)]  # [-25]=base,[-1]=last
        highs = [c * (1 + range_pct / 200.0) for c in closes]
        lows = [c * (1 - range_pct / 200.0) for c in closes]  # (high-low)/close = range_pct%
        return pd.DataFrame({
            "open": closes, "high": highs, "low": lows,
            "close": closes, "volume": [0.0] * 26,
        })
    return f


def _fire(symbol, side, ts, fire_close=100.0):
    return {"ts": ts, "symbol": symbol, "side": side,
            "fire_close": fire_close, "trigger": fire_close * 0.99}


def _recent_iso(minutes_ago=1, *, hour_utc=None):
    """now 근처 fire ts. hour_utc 지정 시 그 UTC hour 의 정시 (게이트 테스트용)."""
    now = datetime.now(timezone.utc)
    if hour_utc is not None:
        # 오늘 날짜의 hour_utc 정시 — freshness 안에 들도록 now 가 그 hour 근처일 때만.
        ts = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
        return ts.isoformat()
    return (now - timedelta(minutes=minutes_ago)).isoformat()


# ── (a) 도착시각 게이트 ────────────────────────────────────────────────────────


def test_arrival_hour_gate_in_set_dispatches():
    """floor(fire_ts,1h).KST.hour ∈ {1,2,3,5,6,7,8,23} → dispatch.

    KST 8시 = UTC 23시. fire ts 를 *방금* (now − 1m) 으로 두고, KST hour 를
    그 ts 에서 계산해 게이트 집합에 그 hour 가 들어가도록 spec 을 맞춘다.
    """
    ts = _recent_iso(minutes_ago=1)
    fire_kst_hour = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, routed = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({fire_kst_hour}))],
    )
    entered = asyncio.run(c.sweep_once())
    assert entered == 1
    assert len(orch.calls) == 1
    assert routed and routed[0]


def test_arrival_hour_out_of_set_not_dispatched():
    ts = _recent_iso(minutes_ago=1)
    fire_kst_hour = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    # 게이트 집합에서 fire hour 를 제외.
    bad_hours = frozenset({(fire_kst_hour + 5) % 24})
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=bad_hours)],
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


# ── (b) side 필터 ──────────────────────────────────────────────────────────────


def test_side_filter_short_whitelist_rejects_long():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_side_filter_short_whitelist_accepts_short():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch,
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
    )
    assert asyncio.run(c.sweep_once()) == 1
    assert orch.calls[0]["side"] == "short"


# ── (c) freshness ──────────────────────────────────────────────────────────────


def test_stale_fire_skipped():
    """fire 가 freshness_sec 보다 오래됐으면 진입 안 함 (재시작 backlog 차단)."""
    old_ts = (datetime.now(timezone.utc) - timedelta(seconds=1200)).isoformat()
    h = int(pd.Timestamp(old_ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", old_ts)]), orch,
        [_spec(hours=frozenset({h}))], freshness_sec=600.0,
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


# ── (d) dedup ──────────────────────────────────────────────────────────────────


def test_dedup_same_symbol_bar_not_entered_twice():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    inst = _FakeStrategyInstance()
    orch = _FakeOrch()
    spec = _spec(hours=frozenset({h}), instance=inst)
    c, _ = _consumer(_FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [spec])

    assert asyncio.run(c.sweep_once()) == 1
    # dedup 마크 됨 — bar_open 키 기록.
    assert inst._fired_bar_ts.get("SOLUSDT") is not None
    # 두 번째 sweep — 같은 fire → dedup 차단.
    assert asyncio.run(c.sweep_once()) == 0
    assert len(orch.calls) == 1


def test_dedup_key_matches_onbar_consume_bar_open():
    """dedup 키 값 = str(floor(fire_ts,1h)−1h) — on_bar consume 의 closed_ts 와 동일."""
    ts = _recent_iso(minutes_ago=1)
    fire_ts = pd.Timestamp(ts)
    if fire_ts.tzinfo is None:
        fire_ts = fire_ts.tz_localize("UTC")
    expected = str(fire_ts.floor("1h") - pd.Timedelta(hours=1))
    h = int(fire_ts.tz_convert("Asia/Seoul").floor("1h").hour)
    inst = _FakeStrategyInstance()
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), instance=inst)],
    )
    asyncio.run(c.sweep_once())
    assert inst._fired_bar_ts["SOLUSDT"] == expected


# ── (e) BTC 필터 ───────────────────────────────────────────────────────────────


def _btc_downtrend_hist() -> pd.DataFrame:
    """200h EMA 아래 close — _btc_is_downtrend True."""
    idx = pd.date_range("2026-01-01", periods=210, freq="1h", tz="UTC")
    close = [200.0] * 205 + [100.0] * 5  # 급락 → 마지막 close 가 EMA 한참 아래
    return pd.DataFrame(
        {"open": close, "high": close, "low": close,
         "close": close, "volume": [1.0] * 210}, index=idx,
    )


# ── AIRBORNE_NO_ENTRY_FILTERS 마스터 토글 (2026-06-22, 무필터 raw 검증) ──────────
# 켜면 타임게이트·btc_downtrend·숏차단시각·고변동·펌핑·폭락 전부 우회.
# freshness/universe/capital 은 유지. 기본 off = 현행 byte-identical.


def test_no_entry_filters_bypasses_hour_gate(monkeypatch):
    """토글 ON → 게이트 밖 시간도 진입."""
    monkeypatch.setenv("AIRBORNE_NO_ENTRY_FILTERS", "1")
    ts = _recent_iso(minutes_ago=1)
    fk = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad = frozenset({(fk + 5) % 24})  # fire hour 를 게이트에서 제외
    orch = _FakeOrch()
    c, routed = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=bad)],
    )
    assert asyncio.run(c.sweep_once()) == 1  # 게이트 밖인데도 진입
    assert len(orch.calls) == 1


def test_no_entry_filters_bypasses_btc_downtrend(monkeypatch):
    """토글 ON → BTC 하락추세 + btc_filter 여도 롱 진입."""
    monkeypatch.setenv("AIRBORNE_NO_ENTRY_FILTERS", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), btc_filter=True)],
        btc_ohlcv_provider=lambda: _btc_downtrend_hist(),
    )
    assert asyncio.run(c.sweep_once()) == 1  # 하락추세인데도 진입


def test_no_entry_filters_bypasses_momentum_and_vol(monkeypatch):
    """토글 ON → 폭락(-30%)·고변동(10%/h) 롱도 진입 (crash/vol 필터 0 처리)."""
    monkeypatch.setenv("AIRBORNE_NO_ENTRY_FILTERS", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}))],
        klines_fetcher=_stub_fetcher(-30.0, range_pct=10.0),  # 폭락+고변동
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_no_entry_filters_default_off_keeps_gate(monkeypatch):
    """env 미설정(기본 off) → 게이트 그대로 차단 (현행 불변)."""
    monkeypatch.delenv("AIRBORNE_NO_ENTRY_FILTERS", raising=False)
    ts = _recent_iso(minutes_ago=1)
    fk = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad = frozenset({(fk + 5) % 24})
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=bad)],
    )
    assert asyncio.run(c.sweep_once()) == 0  # 게이트 차단 = 기본 동작


# ── AIRBORNE_TIME_GATE_ONLY (2026-06-25, 무필터+타임게이트만 복원) ──────────────
# 콘텐츠 필터(btc/숏차단시각/고변동/펌핑/폭락)는 우회하되 타임게이트는 유지.
# no_entry_filters 와 달리 *시간대 밖 fire 는 여전히 차단*.


def test_time_gate_only_blocks_out_of_hour(monkeypatch):
    """TIME_GATE_ONLY ON → 게이트 밖 시간은 여전히 차단 (no_entry_filters 와 차이)."""
    monkeypatch.setenv("AIRBORNE_TIME_GATE_ONLY", "1")
    monkeypatch.delenv("AIRBORNE_NO_ENTRY_FILTERS", raising=False)
    ts = _recent_iso(minutes_ago=1)
    fk = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad = frozenset({(fk + 5) % 24})  # fire hour 를 게이트에서 제외
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=bad)],
    )
    assert asyncio.run(c.sweep_once()) == 0  # 타임게이트 살아있음 → 차단
    assert orch.calls == []


def test_time_gate_only_enters_in_hour(monkeypatch):
    """TIME_GATE_ONLY ON → 게이트 안 시간은 진입."""
    monkeypatch.setenv("AIRBORNE_TIME_GATE_ONLY", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=frozenset({h}))],
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_block_long_full_skips_long(monkeypatch):
    """AIRBORNE_BLOCK_LONG=1 → 롱 전면 차단(숏은 영향 없음). 2026-06-29."""
    monkeypatch.setenv("AIRBORNE_BLOCK_LONG", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=frozenset({h}))],
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_block_long_does_not_affect_short(monkeypatch):
    monkeypatch.setenv("AIRBORNE_BLOCK_LONG", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch, [_spec(hours=frozenset({h}))],
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_long_block_hours_skips_only_that_hour(monkeypatch):
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    monkeypatch.setenv("AIRBORNE_LONG_BLOCK_HOURS", str(h))
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=frozenset({h}))],
    )
    assert asyncio.run(c.sweep_once()) == 0


def test_time_gate_only_bypasses_btc_downtrend(monkeypatch):
    """TIME_GATE_ONLY ON → 게이트 안이면 BTC 하락추세 롱필터는 우회(진입)."""
    monkeypatch.setenv("AIRBORNE_TIME_GATE_ONLY", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), btc_filter=True)],
        btc_ohlcv_provider=lambda: _btc_downtrend_hist(),
    )
    assert asyncio.run(c.sweep_once()) == 1  # 하락추세인데도 진입 (콘텐츠 필터 우회)


def test_time_gate_only_bypasses_momentum_and_vol(monkeypatch):
    """TIME_GATE_ONLY ON → 게이트 안이면 폭락(-30%)·고변동(10%/h) 롱도 진입."""
    monkeypatch.setenv("AIRBORNE_TIME_GATE_ONLY", "1")
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}))],
        klines_fetcher=_stub_fetcher(-30.0, range_pct=10.0),  # 폭락+고변동
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_no_entry_filters_precedence_over_time_gate_only(monkeypatch):
    """둘 다 ON → no_entry_filters 우선(타임게이트까지 우회 = 게이트 밖도 진입)."""
    monkeypatch.setenv("AIRBORNE_NO_ENTRY_FILTERS", "1")
    monkeypatch.setenv("AIRBORNE_TIME_GATE_ONLY", "1")
    ts = _recent_iso(minutes_ago=1)
    fk = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad = frozenset({(fk + 5) % 24})
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=bad)],
    )
    assert asyncio.run(c.sweep_once()) == 1  # 게이트 밖인데도 진입 = no_entry 우선


# ── 개별 필터 ENV 토글 (2026-06-25, AIRBORNE_FILTER_*) ─────────────────────────
# 6 필터(time_gate/btc_downtrend/short_block/high_vol/short_pump/long_crash) 각각
# AIRBORNE_FILTER_<NAME>=1/0. 매크로 기본값을 개별 토글이 덮어씀.


def test_filter_time_gate_only_plus_btc_restored(monkeypatch):
    """TIME_GATE_ONLY 위에 BTC 필터만 개별 복원 → 게이트 안인데 BTC하락 롱 차단."""
    monkeypatch.setenv("AIRBORNE_TIME_GATE_ONLY", "1")
    monkeypatch.setenv("AIRBORNE_FILTER_BTC_DOWNTREND", "1")  # btc 만 다시 ON
    monkeypatch.delenv("AIRBORNE_NO_ENTRY_FILTERS", raising=False)
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), btc_filter=True)],
        btc_ohlcv_provider=lambda: _btc_downtrend_hist(),
    )
    assert asyncio.run(c.sweep_once()) == 0  # btc 필터만 살아남 → 하락추세 롱 차단
    assert orch.calls == []


def test_filter_individual_disable_time_gate_only(monkeypatch):
    """기본(전부 ON)에서 타임게이트만 개별 OFF → 게이트 밖 진입, BTC필터는 여전히 작동."""
    monkeypatch.delenv("AIRBORNE_NO_ENTRY_FILTERS", raising=False)
    monkeypatch.delenv("AIRBORNE_TIME_GATE_ONLY", raising=False)
    monkeypatch.setenv("AIRBORNE_FILTER_TIME_GATE", "0")  # 타임게이트만 끔
    ts = _recent_iso(minutes_ago=1)
    fk = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad = frozenset({(fk + 5) % 24})
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=bad)],
    )
    assert asyncio.run(c.sweep_once()) == 1  # 타임게이트 꺼져 게이트 밖도 진입


def test_filter_btc_disable_alone(monkeypatch):
    """기본(전부 ON)에서 BTC 필터만 개별 OFF → 게이트 안 BTC하락 롱 진입 (나머지 ON)."""
    monkeypatch.delenv("AIRBORNE_NO_ENTRY_FILTERS", raising=False)
    monkeypatch.delenv("AIRBORNE_TIME_GATE_ONLY", raising=False)
    monkeypatch.setenv("AIRBORNE_FILTER_BTC_DOWNTREND", "0")  # btc 만 끔
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), btc_filter=True)],
        btc_ohlcv_provider=lambda: _btc_downtrend_hist(),
    )
    assert asyncio.run(c.sweep_once()) == 1  # btc 필터만 꺼져 하락추세 롱 진입


def test_filter_high_vol_disable_alone(monkeypatch):
    """기본(전부 ON)에서 고변동 필터만 OFF → 고변동 코인 진입 (폭락 필터는 ON 유지)."""
    monkeypatch.delenv("AIRBORNE_NO_ENTRY_FILTERS", raising=False)
    monkeypatch.delenv("AIRBORNE_TIME_GATE_ONLY", raising=False)
    monkeypatch.setenv("AIRBORNE_FILTER_HIGH_VOL", "0")  # 고변동만 끔
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}))],
        klines_fetcher=_stub_fetcher(0.0, range_pct=10.0),  # 고변동(10%/h)만, 폭락無
    )
    assert asyncio.run(c.sweep_once()) == 1  # 고변동 필터 꺼져 진입


def test_filter_default_all_on_unchanged(monkeypatch):
    """매크로·개별 토글 전부 미설정 → 6개 다 ON (현행 production 불변)."""
    for k in ("AIRBORNE_NO_ENTRY_FILTERS", "AIRBORNE_TIME_GATE_ONLY",
              "AIRBORNE_FILTER_TIME_GATE", "AIRBORNE_FILTER_BTC_DOWNTREND"):
        monkeypatch.delenv(k, raising=False)
    ts = _recent_iso(minutes_ago=1)
    fk = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad = frozenset({(fk + 5) % 24})
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch, [_spec(hours=bad)],
    )
    assert asyncio.run(c.sweep_once()) == 0  # 타임게이트 ON → 차단 (현행 동작)


# ── cross-airborne 봉 dedup (2026-06-23, 순차 재진입 차단) ──────────────────────
# 한 종목-봉 fire 는 airborne 전 전략 통틀어 1회만 진입. A 진입·청산 후 B 가 같은
# fire 재진입하던 사고(DEXE) 차단. #471(_live_entered 동시보유)을 봉 단위로 보완.


def test_cross_airborne_bar_dedup_blocks_second_strategy_same_fire():
    """같은 fire(종목-봉)를 두 전략이 순차 진입 못 함 — A 청산 후에도 B 차단."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    # 두 airborne spec — 같은 hour 게이트, 같은 fire 를 둘 다 받음.
    c, _ = _consumer(
        _FakeStore([_fire("DEXEUSDT", "short", ts)]), orch,
        [_spec(sid="live-airborne-a", hours=frozenset({h})),
         _spec(sid="live-airborne-b", hours=frozenset({h}))],
    )
    entered = asyncio.run(c.sweep_once())
    assert entered == 1, "같은 종목-봉은 한 전략만 진입 (cross-airborne 봉 dedup)"
    assert len(orch.calls) == 1


def test_cross_airborne_bar_dedup_persists_across_sweeps():
    """A 진입(첫 sweep) 후 다음 sweep 에서 B 가 같은 fire 재진입 시도해도 차단."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    store = _FakeStore([_fire("DEXEUSDT", "short", ts)])
    orch = _FakeOrch()
    c, _ = _consumer(
        store, orch,
        [_spec(sid="live-airborne-a", hours=frozenset({h})),
         _spec(sid="live-airborne-b", hours=frozenset({h}))],
    )
    assert asyncio.run(c.sweep_once()) == 1   # 첫 sweep — a 진입, _entered_bar 마크
    # 같은 fire 가 store 에 남아 다음 sweep 에 재평가돼도 _entered_bar 가 차단.
    # (consumer 가 _entered_bar 를 보유 → A 청산 여부와 무관하게 봉 단위 1회.)
    again = asyncio.run(c.sweep_once())        # 두번째 sweep — 같은 fire 재평가
    assert again == 0, "같은 종목-봉 fire 는 다음 sweep 에도 재진입 차단(순차)"


def test_btc_downtrend_blocks_long_not_short():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    btc = _btc_downtrend_hist()

    # long — BTC 하락추세 + btc_filter → 차단.
    orch_l = _FakeOrch()
    c_l, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch_l,
        [_spec(hours=frozenset({h}), btc_filter=True)],
        btc_ohlcv_provider=lambda: btc,
    )
    assert asyncio.run(c_l.sweep_once()) == 0
    assert orch_l.calls == []

    # short — BTC 하락추세여도 진입 (short 은 BTC filter 무관).
    orch_s = _FakeOrch()
    c_s, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch_s,
        [_spec(hours=frozenset({h}), btc_filter=True)],
        btc_ohlcv_provider=lambda: btc,
    )
    assert asyncio.run(c_s.sweep_once()) == 1


# ── (f) universe 필터 ──────────────────────────────────────────────────────────


def test_universe_filter_excludes_unlisted():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("NOTINUNIVERSEUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), universe=frozenset({"SOLUSDT", "DOGEUSDT"}))],
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_universe_filter_includes_listed():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), universe=frozenset({"SOLUSDT"}))],
    )
    assert asyncio.run(c.sweep_once()) == 1


# ── dispatch 인자 + intent None 경로 ──────────────────────────────────────────


def test_dispatch_args_carry_fire_price_and_equity():
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts, fire_close=42.5)]), orch,
        [_spec(hours=frozenset({h}))], equity_provider=lambda: 5_555.0,
    )
    asyncio.run(c.sweep_once())
    call = orch.calls[0]
    assert call["price"] == pytest.approx(42.5)
    assert call["equity_usdt"] == pytest.approx(5_555.0)


def test_intent_none_does_not_route_or_mark_dedup():
    """dispatch 가 None (사이징 drop 등) → route 안 함 + dedup 미기록 → 재시도 가능."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    inst = _FakeStrategyInstance()
    orch = _FakeOrch(return_intent=False)
    c, routed = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), instance=inst)],
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert routed == []
    assert inst._fired_bar_ts == {}  # dedup 미기록 → 다음 sweep 재시도


def test_bad_fire_does_not_kill_sweep():
    """깨진 fire(잘못된 ts / 0 price)가 sweep 전체를 죽이지 않음."""
    good_ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(good_ts).tz_convert("Asia/Seoul").floor("1h").hour)
    fires = [
        {"ts": "not-a-date", "symbol": "BADUSDT", "side": "long", "fire_close": 1.0},
        _fire("ZEROUSDT", "long", good_ts, fire_close=0.0),  # price 0 → skip
        _fire("SOLUSDT", "long", good_ts),  # 정상
    ]
    orch = _FakeOrch()
    c, _ = _consumer(_FakeStore(fires), orch, [_spec(hours=frozenset({h}))])
    assert asyncio.run(c.sweep_once()) == 1
    assert orch.calls[0]["symbol"] == "SOLUSDT"


# ── (g) 시간게이트 진입 스킵 텔레그램 알림 ───────────────────────────────────


def test_hour_gate_skip_notifies():
    """시간게이트 밖 발화 → notify 호출 + 메시지에 KST hour/심볼/방향 포함."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad_hours = frozenset({(h + 5) % 24})  # fire hour 제외
    msgs: list[str] = []
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch,
        [_spec(hours=bad_hours, sides=frozenset({"short"}))],
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []
    assert len(msgs) == 1
    assert "SOLUSDT" in msgs[0]
    assert "미진입" in msgs[0]
    assert "시간게이트" in msgs[0]
    assert "숏" in msgs[0]


def test_hour_gate_skip_notify_dedup_once_per_fire():
    """같은 발화는 매 sweep 재평가돼도 알림 1회만."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad_hours = frozenset({(h + 5) % 24})
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), _FakeOrch(),
        [_spec(hours=bad_hours, sides=frozenset({"short"}))],
        notify=msgs.append,
    )
    asyncio.run(c.sweep_once())
    asyncio.run(c.sweep_once())
    assert len(msgs) == 1  # 두 번째 sweep 은 dedup


def test_hour_gate_skip_aggregates_per_hour_side():
    """같은 hour/side 다수 발화 → 한 메시지로 집계(N건 + 심볼 나열)."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad_hours = frozenset({(h + 5) % 24})
    msgs: list[str] = []
    fires = [_fire(s, "short", ts) for s in ("AAA", "BBB", "CCC")]
    c, _ = _consumer(
        _FakeStore(fires), _FakeOrch(),
        [_spec(hours=bad_hours, sides=frozenset({"short"}))],
        notify=msgs.append,
    )
    asyncio.run(c.sweep_once())
    assert len(msgs) == 1
    assert "3건" in msgs[0]
    for s in ("AAA", "BBB", "CCC"):
        assert s in msgs[0]


def test_entry_notifies_ground_truth():
    """진입 성공한 발화는 '✅ 실진입' ground-truth 알림 (2026-06-20 — 실거래 일치)."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), _FakeOrch(),
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 1
    assert len(msgs) == 1
    assert "실진입" in msgs[0]
    assert "SOLUSDT" in msgs[0]


def test_entry_notify_dedup_once_per_fire():
    """같은 진입 발화는 매 sweep 재평가돼도 '실진입' 알림 1회만."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), _FakeOrch(),
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        notify=msgs.append,
    )
    asyncio.run(c.sweep_once())
    asyncio.run(c.sweep_once())
    assert len(msgs) == 1  # 두 번째 sweep 은 dedup (이미진입)


def test_universe_block_notifies_with_reason():
    """universe 로 막힌 건 '❌ 미진입 ... 유니버스밖' 사유 알림 (2026-06-20 신규)."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("NOTINUSDT", "short", ts)]), _FakeOrch(),
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}),
               universe=frozenset({"SOLUSDT"}))],
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert len(msgs) == 1
    assert "미진입" in msgs[0]
    assert "유니버스밖" in msgs[0]
    assert "NOTINUSDT" in msgs[0]


# ── (h) 롱 전용 짧은 freshness (정각 빠른 매수 / stale 늦은진입 차단) ──────────


def _aged_fire(symbol, side, *, age_sec, hour_kst=None):
    """now−age_sec 의 fire. hour_kst 지정 시 그 KST 시각 정시로 맞춤(게이트용)."""
    now = datetime.now(timezone.utc)
    ts = now - timedelta(seconds=age_sec)
    return _fire(symbol, side, ts.isoformat())


def test_long_stale_beyond_long_freshness_skipped():
    """롱은 long_freshness_sec 넘으면 진입 안 함 (BTC 지연 후 묵은 가격 진입 차단)."""
    f = _aged_fire("SOLUSDT", "long", age_sec=120)  # 2분 — 90s 초과
    h = int(pd.Timestamp(f["ts"]).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([f]), orch, [_spec(hours=frozenset({h}))],
        long_freshness_sec=90.0, freshness_sec=600.0,
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_long_fresh_within_long_freshness_enters():
    """봉마감 직후(≤90s) 롱은 진입 (BTC 통과 시 즉시)."""
    f = _aged_fire("SOLUSDT", "long", age_sec=40)
    h = int(pd.Timestamp(f["ts"]).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([f]), orch, [_spec(hours=frozenset({h}))],
        long_freshness_sec=90.0,
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_short_not_affected_by_long_freshness():
    """숏은 long cap 무관 — 기존 freshness(600s) 적용 (재시작 backlog 보호)."""
    f = _aged_fire("SOLUSDT", "short", age_sec=300)  # 5분: long cap 초과지만 short OK
    h = int(pd.Timestamp(f["ts"]).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([f]), orch, [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        long_freshness_sec=90.0, freshness_sec=600.0,
    )
    assert asyncio.run(c.sweep_once()) == 1
    assert orch.calls[0]["side"] == "short"


# ── (i) 숏 차단 시간대 (07시 상승추세 가드) ──────────────────────────────────


def test_short_block_hour_blocks_short_keeps_long():
    """차단 시각의 SHORT 는 진입 안 함 + 알림, 같은 시각 LONG 은 그대로 진입."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    # SHORT @ 차단시각 → 차단
    msgs: list[str] = []
    orch_s = _FakeOrch()
    c_s, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch_s,
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        short_block_hours=frozenset({h}), notify=msgs.append,
    )
    assert asyncio.run(c_s.sweep_once()) == 0
    assert orch_s.calls == []
    assert msgs and "SOLUSDT" in msgs[0]
    # LONG @ 같은 시각 → 통과 (가드는 short 전용)
    orch_l = _FakeOrch()
    c_l, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "long", ts)]), orch_l,
        [_spec(hours=frozenset({h}), sides=frozenset({"long", "short"}))],
        short_block_hours=frozenset({h}),
    )
    assert asyncio.run(c_l.sweep_once()) == 1
    assert orch_l.calls[0]["side"] == "long"


def test_short_not_blocked_at_other_hours():
    """차단 집합 밖 시각의 SHORT 는 정상 진입."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    other = frozenset({(h + 5) % 24})  # fire 시각 제외
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch,
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        short_block_hours=other,
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_short_block_default_is_kst_7():
    """ctor short_block_hours=None → 기본 {7} (KST 07시 유럽장 상승추세 가드)."""
    c = AirborneFireConsumer(
        fire_store=_FakeStore([]), orchestrator=_FakeOrch(),
        strategy_specs=[_spec()], route_intents=lambda i: None,
        equity_provider=lambda: 1.0,
    )
    assert c._short_block_hours == frozenset({7})


# ── run_loop ─────────────────────────────────────────────────────────────────


def test_run_loop_stops_on_event():
    """run_loop 이 stop_event set 시 즉시 종료 (interval 안 기다림)."""
    orch = _FakeOrch()
    c, _ = _consumer(_FakeStore([]), orch, [_spec()], interval_sec=0.05)

    async def _drive():
        stop = asyncio.Event()
        task = asyncio.create_task(c.run_loop(stop))
        await asyncio.sleep(0.12)  # 몇 sweep 돌게
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_drive())  # timeout 없이 끝나면 OK


def test_run_loop_absorbs_sweep_exception():
    """sweep 예외가 run_loop 을 죽이지 않음."""
    class _BoomStore:
        def load_since(self, since):
            raise RuntimeError("boom")

    c, _ = _consumer(_BoomStore(), _FakeOrch(), [_spec()], interval_sec=0.05)

    async def _drive():
        stop = asyncio.Event()
        task = asyncio.create_task(c.run_loop(stop))
        await asyncio.sleep(0.12)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(_drive())


# ── (g) 모멘텀 진입 필터 (2026-06-17) ────────────────────────────────────────


def _mom_setup(side, chg_pct):
    """side 발화 1건 + chg_pct% 변화 stub fetcher 로 consumer 구성. 게이트 통과
    되게 fire hour 를 spec 에 맞춤."""
    ts = _recent_iso(minutes_ago=1)
    fire_kst_hour = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, routed = _consumer(
        _FakeStore([_fire("SOLUSDT", side, ts)]), orch,
        [_spec(hours=frozenset({fire_kst_hour}), sides=frozenset({side}))],
        klines_fetcher=_stub_fetcher(chg_pct),
    )
    return c, orch, routed


def test_momentum_skips_pumped_short():
    """직전24h +35% 펌핑 토큰 숏 → momentum_pump skip (기본 임계 +30%, 2026-06-22)."""
    c, orch, _ = _mom_setup("short", 35.0)
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_momentum_allows_25pct_pump_after_relax():
    """2026-06-22 완화: +25% (옛 임계 20 초과, 새 임계 30 미만) 숏 → 이제 진입.

    펌핑 후 BB되돌림 숏(전략 핵심)이 옛 +20 임계에 죽던 TP 승자를 복구."""
    c, orch, _ = _mom_setup("short", 25.0)
    assert asyncio.run(c.sweep_once()) == 1
    assert len(orch.calls) == 1


def test_momentum_allows_mild_short():
    """직전24h +5% (임계 미만) 숏 → 정상 진입."""
    c, orch, _ = _mom_setup("short", 5.0)
    assert asyncio.run(c.sweep_once()) == 1
    assert len(orch.calls) == 1


def test_momentum_skips_crashed_long():
    """직전24h -15% 폭락 토큰 롱 → momentum_crash skip (기본 임계 -10%)."""
    c, orch, _ = _mom_setup("long", -15.0)
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_momentum_allows_mild_long():
    """직전24h -5% (임계 미만) 롱 → 정상 진입."""
    c, orch, _ = _mom_setup("long", -5.0)
    assert asyncio.run(c.sweep_once()) == 1


def test_momentum_failopen_when_no_data():
    """fetcher 가 None(미상장/실패) → fail-open, 진입 허용."""
    c, orch, _ = _mom_setup("short", None)
    assert asyncio.run(c.sweep_once()) == 1


def test_momentum_off_without_fetcher():
    """fetcher 미주입 → 모멘텀 비활성, +25% 펌핑도 그대로 진입."""
    ts = _recent_iso(minutes_ago=1)
    fire_kst_hour = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch,
        [_spec(hours=frozenset({fire_kst_hour}), sides=frozenset({"short"}))],
    )  # klines_fetcher 미주입
    assert asyncio.run(c.sweep_once()) == 1


# ── (h) 변동성 필터 (2026-06-17) ────────────────────────────────────────────


def test_vol_filter_skips_high_vol_coin():
    """평균 1h 변동폭 8% (>임계 5%) 코인 → high_volatility skip (양방향)."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SIRENUSDT", "short", ts)]), orch,
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        klines_fetcher=_stub_fetcher(0.0, range_pct=8.0),  # 24h 변화 0, 변동폭 8%
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert orch.calls == []


def test_vol_filter_allows_low_vol_coin():
    """평균 1h 변동폭 2% (<임계 5%) → 정상 진입."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), orch,
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        klines_fetcher=_stub_fetcher(0.0, range_pct=2.0),
    )
    assert asyncio.run(c.sweep_once()) == 1


def test_vol_filter_applies_to_long_too():
    """변동성 필터는 양방향 — 고변동 코인 롱도 skip."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    orch = _FakeOrch()
    c, _ = _consumer(
        _FakeStore([_fire("HUSDT", "long", ts)]), orch,
        [_spec(hours=frozenset({h}), sides=frozenset({"long"}))],
        klines_fetcher=_stub_fetcher(0.0, range_pct=10.0),
    )
    assert asyncio.run(c.sweep_once()) == 0


# ── (i) ground-truth 미진입 사유 라벨 + 진입/미진입 동시 통지 ─────────────────


def test_momentum_crash_notifies_reason_label():
    """폭락 롱 skip → '❌ 미진입 ... 폭락' 사유 라벨 통지."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("EPICUSDT", "long", ts)]),
        _FakeOrch(),
        [_spec(hours=frozenset({h}), sides=frozenset({"long"}))],
        klines_fetcher=_stub_fetcher(-23.0),
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert len(msgs) == 1
    assert "미진입" in msgs[0]
    assert "폭락" in msgs[0]
    assert "EPICUSDT" in msgs[0]


def test_vol_filter_notifies_reason_label():
    """고변동 코인 skip → '❌ 미진입 ... 고변동' 사유 라벨 통지."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("SIRENUSDT", "short", ts)]),
        _FakeOrch(),
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        klines_fetcher=_stub_fetcher(0.0, range_pct=8.0),
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 0
    assert len(msgs) == 1
    assert "고변동" in msgs[0]
    assert "SIRENUSDT" in msgs[0]


def test_entry_and_skip_both_notified_one_sweep():
    """한 sweep 에 진입 1 + 미진입 1 → '✅ 실진입' / '❌ 미진입' 두 메시지."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    bad_h = (h + 5) % 24
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([
            _fire("GOODUSDT", "short", ts),   # hour 통과 → 진입
            _fire("LATEUSDT", "short", ts),   # 같은 ts 라도 spec hour 밖이면 미진입
        ]),
        _FakeOrch(),
        # spec1: GOOD/LATE 둘 다 hour 통과시키되 universe 로 LATE 만 차단
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}),
               universe=frozenset({"GOODUSDT"}))],
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 1
    joined = "\n".join(msgs)
    assert "실진입" in joined and "GOODUSDT" in joined
    assert "미진입" in joined and "LATEUSDT" in joined and "유니버스밖" in joined
