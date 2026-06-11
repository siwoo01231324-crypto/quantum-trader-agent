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
    hours=frozenset({1, 2, 3, 6, 7, 8, 23}),
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
        freshness_sec=kw.pop("freshness_sec", 600.0),
        interval_sec=kw.pop("interval_sec", 15.0),
    )
    return c, routed


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
    """floor(fire_ts,1h).KST.hour ∈ {1,2,3,6,7,8,23} → dispatch.

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
