"""LiveTurtleTrendDaily 단위 테스트 — 진입/warmup/경계/청산(트레일링·ATR스톱)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.strategies.live_turtle_trend_daily import LiveTurtleTrendDaily


def _daily_index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="1D", tz="UTC")


def _uptrend_breakout(n: int = 260) -> pd.DataFrame:
    """200MA 위 + 마지막 봉이 20봉 신고가 돌파하는 상승추세 OHLCV."""
    idx = _daily_index(n)
    # 완만한 상승 후 마지막 봉 급등(돌파)
    base = np.linspace(100.0, 140.0, n)
    close = base.copy()
    close[-1] = base[-2] + 8.0  # 20봉 신고가 명확 돌파
    high = close + 0.5
    high[-1] = close[-1] + 0.5
    low = close - 0.5
    return pd.DataFrame(
        {"open": close, "high": high, "low": low, "close": close,
         "volume": np.full(n, 1000.0)},
        index=idx,
    )


def _flat(n: int = 260, price: float = 50.0) -> pd.DataFrame:
    idx = _daily_index(n)
    c = np.full(n, price)
    return pd.DataFrame(
        {"open": c, "high": c + 0.1, "low": c - 0.1, "close": c,
         "volume": np.full(n, 1000.0)}, index=idx,
    )


def _ctx(hist: dict, ts: pd.Timestamp) -> dict:
    return {"ts": ts, "market_snapshot": {"ohlcv_history": hist}}


@pytest.mark.asyncio
async def test_entry_on_breakout_above_200ma():
    """20봉 신고가 돌파 + 200MA 위 → buy, 포지션 잡힘 + ATR 스톱 기록."""
    s = LiveTurtleTrendDaily(top_n=6)
    hist = {"BREAKOUT": _uptrend_breakout(), "FLAT": _flat()}
    ts = hist["BREAKOUT"].index[-1]  # UTC 00:00
    sig = await s.on_bar(_ctx(hist, ts))
    assert sig.action == "buy"
    assert "BREAKOUT" in s._positions
    assert "FLAT" not in s._positions  # 횡보 = 돌파 없음
    assert s._positions["BREAKOUT"]["stop"] < s._positions["BREAKOUT"]["entry"]


@pytest.mark.asyncio
async def test_warmup_insufficient_history_holds():
    """200봉 미만(SMA200 warmup) → 진입 후보 없음 → hold."""
    s = LiveTurtleTrendDaily()
    hist = {"X": _uptrend_breakout(n=50)}
    ts = hist["X"].index[-1]
    sig = await s.on_bar(_ctx(hist, ts))
    assert sig.action == "hold"
    assert not s._positions


@pytest.mark.asyncio
async def test_bar_boundary_non_midnight_holds():
    """UTC 00:00 아닌 시각 → not my bar (hold)."""
    s = LiveTurtleTrendDaily()
    hist = {"BREAKOUT": _uptrend_breakout()}
    ts = hist["BREAKOUT"].index[-1] + pd.Timedelta(hours=6)
    sig = await s.on_bar(_ctx(hist, ts))
    assert sig.action == "hold" and "not my bar" in sig.reason


@pytest.mark.asyncio
async def test_long_only_no_short_on_breakdown():
    """하락 돌파(저점 이탈)에는 진입 안 함 (롱 전용)."""
    s = LiveTurtleTrendDaily()
    idx = _daily_index(260)
    base = np.linspace(140.0, 100.0, 260)  # 하락추세
    c = base.copy(); c[-1] = base[-2] - 8.0  # 신저가 이탈
    hist = {"DOWN": pd.DataFrame(
        {"open": c, "high": c + 0.5, "low": c - 0.5, "close": c, "volume": np.full(260, 1000.0)},
        index=idx)}
    sig = await s.on_bar(_ctx(hist, idx[-1]))
    assert sig.action == "hold"
    assert not s._positions  # 숏 미지원


@pytest.mark.asyncio
async def test_exit_on_atr_stop():
    """보유 중 저가가 2×ATR 스톱 터치 → 청산."""
    s = LiveTurtleTrendDaily(top_n=6)
    hist = {"BREAKOUT": _uptrend_breakout()}
    ts = hist["BREAKOUT"].index[-1]
    await s.on_bar(_ctx(hist, ts))
    assert "BREAKOUT" in s._positions
    stop = s._positions["BREAKOUT"]["stop"]
    # 다음 봉: 저가가 스톱 아래로 급락
    h2 = hist["BREAKOUT"].copy()
    new_idx = h2.index[-1] + pd.Timedelta(days=1)
    crash_close = stop - 2.0
    row = pd.DataFrame({"open": [crash_close], "high": [crash_close + 0.1],
                        "low": [stop - 3.0], "close": [crash_close], "volume": [1000.0]},
                       index=[new_idx])
    h2 = pd.concat([h2, row])
    sig = await s.on_bar(_ctx({"BREAKOUT": h2}, new_idx))
    assert "BREAKOUT" not in s._positions  # 스톱 청산됨
