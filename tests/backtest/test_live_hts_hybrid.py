"""Unit tests for LiveHtsHybrid (#230 — live-scanner paradigm, HTS 검색식 OR 합성)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from backtest.strategies.live_hts_hybrid import LiveHtsHybrid


# -- fixtures ---------------------------------------------------------

def _make_daily_cache(tmp_path: Path, symbol: str, *,
                      prev_close: float = 5_000.0,
                      strict_ma_ascending: bool = True,
                      vol_5d: int = 2_000_000) -> Path:
    """Write a synthetic 70-day daily parquet.

    strict_ma_ascending=True (default) → close 가 우상향 linspace → MA5 > MA20 > MA60
    strict_ma_ascending=False → close flat → MA5 == MA20 == MA60 (F 조건 fail 검증용)
    """
    n = 70
    today = pd.Timestamp.now(tz="Asia/Seoul").normalize().tz_localize(None)
    dates = pd.date_range(end=today - pd.Timedelta(days=1), periods=n)
    if strict_ma_ascending:
        # 4500 → prev_close linspace → 최근 5일 avg > 최근 20일 avg > 최근 60일 avg
        closes = np.linspace(prev_close - 500.0, prev_close, n)
    else:
        closes = np.full(n, prev_close)
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.001, "low": closes * 0.999,
        "close": closes,
        "volume": np.full(n, int(vol_5d / 5)),
    }, index=dates)
    df.index.name = "Date"
    cache_dir = tmp_path / "data" / "cache" / "krx_daily"
    cache_dir.mkdir(parents=True, exist_ok=True)
    p = cache_dir / f"{symbol}.parquet"
    df.to_parquet(p)
    return cache_dir


def _intraday_history(n: int = 60, base: float = 5_500.0, *,
                      include_volume: bool = True,
                      tz: str = "Asia/Seoul") -> pd.DataFrame:
    """1m intraday history — close = base (보합, B/G 가 0% 이므로 daily ret 조건 X).

    base 가 prev_close 대비 +>=2%/5% 이도록 호출자가 조정.
    """
    today = pd.Timestamp.now(tz=tz).normalize() + pd.Timedelta(hours=9, minutes=5)
    idx = pd.date_range(today, periods=n, freq="1min")
    closes = np.full(n, base)
    df = pd.DataFrame({
        "open": closes, "high": closes * 1.0005, "low": closes * 0.9995,
        "close": closes,
        "volume": np.full(n, 5_000.0) if include_volume else np.zeros(n),
    }, index=idx)
    return df


def _ctx(history: pd.DataFrame, symbol: str = "005930") -> dict:
    return {
        "ts": history.index[-1],
        "market_snapshot": {
            "symbol": symbol,
            "history": history,
            "price": float(history["close"].iloc[-1]),
        },
        "factors": {},
    }


def _run(strategy, ctx) -> Signal | None:
    return asyncio.run(strategy.on_bar(ctx))


# -- LiveScannerMixin contract ---------------------------------------

class TestMixinContract:
    def test_inherits_marker(self) -> None:
        assert issubclass(LiveHtsHybrid, LiveScannerMixin)
        assert LiveHtsHybrid.is_live_scanner is True

    def test_stop_tp_class_attrs(self) -> None:
        assert LiveHtsHybrid.stop_loss_pct == 0.02
        assert LiveHtsHybrid.take_profit_pct == 0.02
        assert LiveHtsHybrid.trailing_stop_pct is None

    def test_on_bar_is_async(self) -> None:
        import inspect
        assert inspect.iscoroutinefunction(LiveHtsHybrid().on_bar)


# -- warmup / time gate / missing daily ------------------------------

class TestGateRejections:
    def test_warmup_returns_hold(self, tmp_path: Path) -> None:
        cache = _make_daily_cache(tmp_path, "005930")
        s = LiveHtsHybrid(daily_cache_dir=str(cache))
        short_hist = _intraday_history(n=10)
        result = _run(s, _ctx(short_hist))
        assert result.action == "hold"
        assert "warmup" in result.reason

    def test_time_gate_after_1030_returns_hold(self, tmp_path: Path) -> None:
        cache = _make_daily_cache(tmp_path, "005930")
        s = LiveHtsHybrid(daily_cache_dir=str(cache), max_entry_hour=10.5)
        # 11:00 KST 시작 60분 → 12:00 까지 — 10:30 게이트 초과
        late = pd.Timestamp.now(tz="Asia/Seoul").normalize() + pd.Timedelta(hours=11)
        idx = pd.date_range(late, periods=60, freq="1min")
        df = pd.DataFrame({
            "open": np.full(60, 5_500.0), "high": np.full(60, 5_500.0),
            "low": np.full(60, 5_500.0), "close": np.full(60, 5_500.0),
            "volume": np.full(60, 5_000.0),
        }, index=idx)
        result = _run(s, _ctx(df))
        assert result.action == "hold"
        assert "time_gate" in result.reason

    def test_missing_daily_cache_returns_hold(self, tmp_path: Path) -> None:
        # Cache dir 비어있음 → no_daily_cache
        empty_cache = tmp_path / "empty_cache"
        empty_cache.mkdir()
        s = LiveHtsHybrid(daily_cache_dir=str(empty_cache))
        history = _intraday_history(n=60, base=5_500.0)
        result = _run(s, _ctx(history))
        assert result.action == "hold"
        assert "no_daily" in result.reason or "warmup" in result.reason


# -- screener evaluation: buy / no-signal ----------------------------

class TestEvaluatorIntegration:
    def test_no_signal_when_daily_fails(self, tmp_path: Path) -> None:
        # flat MA (ma5==ma20==ma60) → F (strict > ) fail → no signal
        cache = _make_daily_cache(tmp_path, "005930", prev_close=5_000.0,
                                  strict_ma_ascending=False)
        s = LiveHtsHybrid(daily_cache_dir=str(cache))
        history = _intraday_history(n=60, base=5_500.0)
        result = _run(s, _ctx(history))
        assert result.action == "hold"
        assert "no_signal" in result.reason

    def test_buy_signal_emitted(self, tmp_path: Path) -> None:
        # daily 정배열 통과 + close=5500 (prev=5000 대비 +10%) → B,G 통과
        cache = _make_daily_cache(tmp_path, "005930", prev_close=5_000.0,
                                  strict_ma_ascending=True)
        s = LiveHtsHybrid(daily_cache_dir=str(cache))
        history = _intraday_history(n=60, base=5_500.0)
        result = _run(s, _ctx(history))
        assert result.action == "buy"
        assert result.size == 0.05
        # triggered_by 기록 — SWING H 없음 + daily 통과 → 최소 SWING fire (DTS 도 가능)
        assert "hts_hybrid:" in result.reason
        assert any(tag in result.reason for tag in ("dts", "wait5m", "swing"))

    def test_default_size_validation(self) -> None:
        with pytest.raises(ValueError):
            LiveHtsHybrid(default_size=0.0)
        with pytest.raises(ValueError):
            LiveHtsHybrid(default_size=1.5)


# -- production.yaml entry contract ----------------------------------

class TestProductionYamlEntry:
    def test_class_import_path_resolves(self) -> None:
        import importlib
        mod = importlib.import_module("backtest.strategies.live_hts_hybrid")
        assert hasattr(mod, "LiveHtsHybrid")

    def test_yaml_entry_kwargs_match_constructor(self) -> None:
        import yaml
        yaml_path = Path(__file__).resolve().parents[2] / "configs" / "orchestrator" / "production.yaml"
        text = yaml_path.read_text(encoding="utf-8")
        # entry 가 주석 처리되어 있음 → string search
        assert "live-hts-hybrid" in text
        assert "live_hts_hybrid.LiveHtsHybrid" in text
        assert "default_size: 0.05" in text
        assert "max_entry_hour: 10.5" in text
        # constructor 호환 검증
        kwargs = {"default_size": 0.05, "max_entry_hour": 10.5}
        s = LiveHtsHybrid(**kwargs)
        assert s.default_size == 0.05
        assert s.max_entry_hour == 10.5
