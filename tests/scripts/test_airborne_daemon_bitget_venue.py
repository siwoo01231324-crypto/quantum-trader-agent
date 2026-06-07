"""Bitget venue 단위 테스트 — airborne_alert_daemon (#airborne-bitget-venue).

실거래 트레이더(broker=bitget-demo)가 Bitget top-100 을 거래하는데 알림 데몬은
Binance fapi top-100 을 봐서 알림과 실거래의 유니버스/가격이 어긋나던 문제.
``--venue bitget`` 이면 데몬이 Bitget top-100 + Bitget REST candles 로 fire 를
평가하게 한다. 검증:

  ① --venue 인자 파싱 (CLI > env > 기본 binance)
  ② venue=bitget 시 유니버스가 get_top_n_symbols 호출 (mock)
  ③ Bitget bars → 데몬 history 어댑터가 올바른 OHLCV DataFrame 생성
  ④ venue=binance 면 기존 경로 그대로 (회귀 가드)
  ⑤ venue=bitget + mode=ws 는 명확히 거부
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

import airborne_alert_daemon as daemon  # noqa: E402
from brokers.bitget.market_ws import KlineEvent as BitgetKlineEvent  # noqa: E402


# ── ① --venue argparse 파싱 ──────────────────────────────────────────────────


def _run_sync(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_venue_default_is_binance(monkeypatch):
    """--venue 미지정 + env 없음 → binance (기존 경로 보존)."""
    monkeypatch.delenv("AIRBORNE_VENUE", raising=False)
    seen: dict = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(daemon, "_run_polling_loop", fake_loop)
    monkeypatch.setattr(daemon.asyncio, "run", _run_sync)
    daemon.main(["--dry-run", "--top-n", "3"])
    assert seen.get("venue") == "binance"


def test_venue_cli_bitget(monkeypatch):
    """--venue bitget → _run_polling_loop 가 venue='bitget' 으로 호출됨."""
    monkeypatch.delenv("AIRBORNE_VENUE", raising=False)
    seen: dict = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(daemon, "_run_polling_loop", fake_loop)
    monkeypatch.setattr(daemon.asyncio, "run", _run_sync)
    daemon.main(["--venue", "bitget", "--dry-run", "--top-n", "5"])
    assert seen.get("venue") == "bitget"


def test_venue_env_fallback(monkeypatch):
    """AIRBORNE_VENUE=bitget (CLI 미지정) → bitget."""
    monkeypatch.setenv("AIRBORNE_VENUE", "bitget")
    seen: dict = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(daemon, "_run_polling_loop", fake_loop)
    monkeypatch.setattr(daemon.asyncio, "run", _run_sync)
    daemon.main(["--dry-run", "--top-n", "5"])
    assert seen.get("venue") == "bitget"


def test_venue_cli_overrides_env(monkeypatch):
    """CLI --venue binance 가 env AIRBORNE_VENUE=bitget 를 이김."""
    monkeypatch.setenv("AIRBORNE_VENUE", "bitget")
    seen: dict = {}

    async def fake_loop(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(daemon, "_run_polling_loop", fake_loop)
    monkeypatch.setattr(daemon.asyncio, "run", _run_sync)
    daemon.main(["--venue", "binance", "--dry-run", "--top-n", "5"])
    assert seen.get("venue") == "binance"


# ── ② venue=bitget 시 유니버스가 get_top_n_symbols 호출 ──────────────────────


def test_compute_universe_bitget_calls_get_top_n(monkeypatch):
    """venue=bitget → portfolio.bitget_top_dynamic.get_top_n_symbols(top_n)."""
    calls: list[int] = []

    def fake_top(n):
        calls.append(n)
        return ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

    import portfolio.bitget_top_dynamic as bt  # noqa: PLC0415
    monkeypatch.setattr(bt, "get_top_n_symbols", fake_top)

    universe = _run_sync(daemon._compute_universe(
        venue="bitget", top_n=3, rest_base_url="https://unused",
    ))
    assert calls == [3]
    assert universe == ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_compute_universe_binance_uses_snapshot(monkeypatch):
    """venue=binance → fetch_futures_24h_snapshot + top_n_by_volume (회귀 가드)."""
    snap_called: list = []

    async def fake_snap(*, base_url):
        snap_called.append(base_url)
        return {"sentinel": True}

    def fake_top_n(snap, *, n):
        assert snap == {"sentinel": True}
        return ["AAAUSDT", "BBBUSDT"][:n]

    monkeypatch.setattr(daemon, "fetch_futures_24h_snapshot", fake_snap)
    monkeypatch.setattr(daemon, "top_n_by_volume", fake_top_n)

    universe = _run_sync(daemon._compute_universe(
        venue="binance", top_n=2, rest_base_url="https://fapi.test",
    ))
    assert snap_called == ["https://fapi.test"]
    assert universe == ["AAAUSDT", "BBBUSDT"]


# ── ③ Bitget bars → 데몬 history 어댑터 ──────────────────────────────────────


def _bitget_bar(*, open_time, o, h, l, c, v):
    return BitgetKlineEvent(
        symbol="BTCUSDT", interval="1h",
        open_time=open_time, close_time=open_time + 3_599_999,
        open=Decimal(str(o)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)),
        volume=Decimal(str(v)), closed=True,
    )


def test_bitget_bars_to_history_builds_ohlcv_dataframe():
    """Bitget [KlineEvent] → UTC-indexed float OHLCV DataFrame."""
    t0 = 1_700_000_000_000
    bars = [
        _bitget_bar(open_time=t0, o=100, h=101, l=99, c=100.5, v=10),
        _bitget_bar(open_time=t0 + 3_600_000, o=100.5, h=102, l=100, c=101.5, v=20),
    ]
    df = daemon._bitget_bars_to_history(bars)
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert len(df) == 2
    # float (Decimal 아님)
    assert df["close"].dtype == float
    assert df["close"].iloc[-1] == 101.5
    assert df["volume"].iloc[0] == 10.0
    # UTC DatetimeIndex
    assert str(df.index.tz) == "UTC"
    assert df.index[0] == pd.Timestamp(t0, unit="ms", tz="UTC")


def test_bitget_bars_to_history_sorts_ascending():
    """Bitget candles 가 최신→과거로 와도 open_time 오름차순 정렬."""
    t0 = 1_700_000_000_000
    bars = [
        _bitget_bar(open_time=t0 + 3_600_000, o=2, h=2, l=2, c=2, v=2),  # 최신 먼저
        _bitget_bar(open_time=t0, o=1, h=1, l=1, c=1, v=1),
    ]
    df = daemon._bitget_bars_to_history(bars)
    assert df["close"].iloc[0] == 1.0  # 과거 봉이 먼저
    assert df["close"].iloc[-1] == 2.0
    assert df.index[0] < df.index[-1]


def test_bitget_bars_to_history_empty():
    """빈 bars → 빈 DataFrame (warmup 으로 자연 skip)."""
    df = daemon._bitget_bars_to_history([])
    assert df.empty
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]


def test_bootstrap_history_venue_bitget_adapts(monkeypatch):
    """venue=bitget → bitget bootstrap_history (interval 별) → {sym:{iv:DataFrame}}."""
    t0 = 1_700_000_000_000

    async def fake_bitget_bootstrap(*, symbols, interval, limit, paper):
        # interval 별로 1 봉씩 반환
        return {
            s: [_bitget_bar(open_time=t0, o=1, h=2, l=0.5, c=1.5, v=9)]
            for s in symbols
        }

    import brokers.bitget.market_ws as bws  # noqa: PLC0415
    monkeypatch.setattr(bws, "bootstrap_history", fake_bitget_bootstrap)

    out = _run_sync(daemon._bootstrap_history_venue(
        venue="bitget", symbols=["BTCUSDT", "ETHUSDT"],
        rest_base_url="https://unused",
    ))
    assert set(out.keys()) == {"BTCUSDT", "ETHUSDT"}
    for sym in ("BTCUSDT", "ETHUSDT"):
        assert set(out[sym].keys()) == {"1h", "5m"}
        df = out[sym]["1h"]
        assert isinstance(df, pd.DataFrame)
        assert df["close"].iloc[-1] == 1.5
        assert str(df.index.tz) == "UTC"


def test_bootstrap_history_venue_binance_passthrough(monkeypatch):
    """venue=binance → 기존 bootstrap_history 그대로 호출 (회귀 가드)."""
    called: dict = {}

    async def fake_binance_bootstrap(*, symbols, intervals, limit_per_interval, base_url):
        called["symbols"] = symbols
        called["intervals"] = intervals
        called["base_url"] = base_url
        return {s: {"1h": pd.DataFrame(), "5m": pd.DataFrame()} for s in symbols}

    monkeypatch.setattr(daemon, "bootstrap_history", fake_binance_bootstrap)
    out = _run_sync(daemon._bootstrap_history_venue(
        venue="binance", symbols=["BTCUSDT"], rest_base_url="https://fapi.test",
    ))
    assert called["base_url"] == "https://fapi.test"
    assert called["intervals"] == ("1h", "5m")
    assert set(out["BTCUSDT"].keys()) == {"1h", "5m"}


# ── ④ _bootstrap_into_states venue 분기 (회귀 + bitget) ──────────────────────


def test_bootstrap_into_states_bitget(monkeypatch):
    """venue=bitget → states 에 Bitget history 가 seed 됨."""
    t0 = 1_700_000_000_000

    async def fake_venue_boot(*, venue, symbols, rest_base_url):
        assert venue == "bitget"
        df = daemon._bitget_bars_to_history(
            [_bitget_bar(open_time=t0, o=1, h=2, l=0.5, c=1.5, v=9)]
        )
        return {s: {"1h": df, "5m": df} for s in symbols}

    monkeypatch.setattr(daemon, "_bootstrap_history_venue", fake_venue_boot)
    states: dict = {}
    _run_sync(daemon._bootstrap_into_states(
        ["BTCUSDT"], states, rest_base_url="https://unused", venue="bitget",
    ))
    assert "BTCUSDT" in states
    assert not states["BTCUSDT"].history_1h.empty
    assert states["BTCUSDT"].history_1h["close"].iloc[-1] == 1.5


def test_bootstrap_into_states_binance_default(monkeypatch):
    """venue 미지정 → binance (기존 경로). 회귀 가드."""
    seen: list = []

    async def fake_venue_boot(*, venue, symbols, rest_base_url):
        seen.append(venue)
        return {s: {"1h": pd.DataFrame(), "5m": pd.DataFrame()} for s in symbols}

    monkeypatch.setattr(daemon, "_bootstrap_history_venue", fake_venue_boot)
    states: dict = {}
    _run_sync(daemon._bootstrap_into_states(
        ["BTCUSDT"], states, rest_base_url="https://fapi.test",
    ))
    assert seen == ["binance"]


# ── ⑤ venue=bitget + mode=ws 거부 ───────────────────────────────────────────


def test_run_daemon_rejects_bitget_ws():
    with pytest.raises(ValueError, match="polling"):
        _run_sync(daemon.run_daemon(top_n=5, mode="ws", venue="bitget"))


def test_run_daemon_rejects_unknown_venue():
    with pytest.raises(ValueError, match="unknown venue"):
        _run_sync(daemon.run_daemon(top_n=5, mode="polling", venue="bogus"))
