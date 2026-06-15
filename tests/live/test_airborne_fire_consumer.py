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
        notify=kw.pop("notify", None),
        freshness_sec=kw.pop("freshness_sec", 600.0),
        long_freshness_sec=kw.pop("long_freshness_sec", 90.0),
        # 테스트 기본은 빈 set — wall-clock 이 07시여도 기존 테스트가 안 깨지게.
        # short-block 전용 테스트만 명시적으로 hours 지정.
        short_block_hours=kw.pop("short_block_hours", frozenset()),
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
    assert f"{h:02d}시" in msgs[0]
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


def test_no_notify_when_entered():
    """진입 성공한 발화는 스킵 알림 안 함."""
    ts = _recent_iso(minutes_ago=1)
    h = int(pd.Timestamp(ts).tz_convert("Asia/Seoul").floor("1h").hour)
    msgs: list[str] = []
    c, _ = _consumer(
        _FakeStore([_fire("SOLUSDT", "short", ts)]), _FakeOrch(),
        [_spec(hours=frozenset({h}), sides=frozenset({"short"}))],
        notify=msgs.append,
    )
    assert asyncio.run(c.sweep_once()) == 1
    assert msgs == []


def test_no_notify_when_universe_blocks_not_hour():
    """universe 로 막힌 건 시간 사유 아님 → 스킵 알림 안 함 (오탐 방지)."""
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
    assert msgs == []  # hour 는 통과했고 universe 가 binding → 알림 X


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
