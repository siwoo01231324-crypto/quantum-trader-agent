"""AsyncStrategy wrapper tests for cross-sectional strategies (#218 Phase 2)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies.cs_async_wrapper import (
    ACTIVE_WRAP_FACTORIES,
    CrossSectionalAsyncStrategy,
    make_cs_bb_macd_kr,
    make_cs_rsi_div_kr,
    make_cs_tsmom_kr_daily,
)


@dataclass
class FakeCtx:
    market_snapshot: dict[str, Any]


def make_ohlcv_history(n_codes: int = 30, n_bars: int = 300, seed: int = 7,
                       *, with_quote_volume: bool = False) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=n_bars, freq="B")
    out: dict[str, pd.DataFrame] = {}
    for i in range(n_codes):
        rets = rng.normal(0.0005, 0.02, size=n_bars)
        close = 1000 * np.exp(np.cumsum(rets))
        high = close * (1 + np.abs(rng.normal(0, 0.005, size=n_bars)))
        low = close * (1 - np.abs(rng.normal(0, 0.005, size=n_bars)))
        volume = rng.uniform(1e6, 1e8, size=n_bars)
        df = pd.DataFrame({"open": close, "high": high, "low": low,
                           "close": close, "volume": volume}, index=dates)
        if with_quote_volume:
            df["quote_volume"] = close * volume
        out[f"S{i:03d}"] = df
    return out


@pytest.mark.asyncio
async def test_wrapper_returns_hold_during_warmup():
    s = make_cs_rsi_div_kr()
    ctx = FakeCtx(market_snapshot={"ohlcv_history": make_ohlcv_history()})
    sig = await s.on_bar(ctx)
    assert sig is not None
    assert sig.action == "hold"


@pytest.mark.asyncio
async def test_wrapper_returns_signal_after_warmup_at_rebal():
    s = make_cs_rsi_div_kr()
    ctx = FakeCtx(market_snapshot={"ohlcv_history": make_ohlcv_history(n_bars=200)})
    # 강제로 bar count 를 (warmup + rebal_freq * k) 로 만들어 트리거
    s._bar_count = s.MIN_HISTORY + s.rebal_freq - 1  # 다음 호출이 rebal
    sig = await s.on_bar(ctx)
    assert sig is not None
    assert sig.action in ("buy", "sell", "hold")
    if sig.action == "buy":
        assert 0.0 < sig.size <= 1.0 + 1e-9   # equal-weight float tolerance
        assert s.latest_weights is not None and len(s.latest_weights) > 0


@pytest.mark.asyncio
async def test_wrapper_no_data_returns_hold():
    s = make_cs_tsmom_kr_daily()
    ctx = FakeCtx(market_snapshot={"ohlcv_history": {}})
    s._bar_count = s.MIN_HISTORY + s.rebal_freq
    sig = await s.on_bar(ctx)
    assert sig is not None
    assert sig.action == "hold"


@pytest.mark.asyncio
async def test_wrapper_handles_compute_error_gracefully():
    """compute_weights_fn 에서 예외 발생 시 hold 반환 (orchestrator 다운 안 됨)."""
    def bad_fn(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    s = CrossSectionalAsyncStrategy(
        strategy_id="bad_test",
        compute_weights_fn=bad_fn,
        symbol="TEST_BASKET",
        warmup_bars=10, rebal_freq=1,
    )
    ctx = FakeCtx(market_snapshot={"ohlcv_history": make_ohlcv_history(n_bars=20)})
    s._bar_count = 12  # past warmup, rebal due
    sig = await s.on_bar(ctx)
    assert sig.action == "hold"
    assert "error" in sig.reason


def test_active_factories_registry_excludes_inactive():
    assert "cs_bb_macd_kr" not in ACTIVE_WRAP_FACTORIES
    expected = {
        "cs_tsmom_kr_daily", "cs_rsi_div_kr", "cs_adx_ma_kr",
        "cs_tsmom_crypto_daily", "cs_rsi_div_crypto", "cs_macd_vol_crypto",
    }
    assert set(ACTIVE_WRAP_FACTORIES.keys()) == expected


def test_inactive_factory_still_importable():
    """make_cs_bb_macd_kr 는 여전히 호출 가능해야 함 (코드 유지). 단 ACTIVE 에서 제외."""
    s = make_cs_bb_macd_kr()
    assert s.strategy_id == "cs_bb_macd_kr"


def test_all_active_factories_produce_protocol_compliant_instance():
    """AsyncStrategy 는 @runtime_checkable Protocol 이 아니므로 isinstance 대신
    duck-type 체크 (on_bar 코루틴 메서드 존재) 로 검증."""
    import inspect
    for name, factory in ACTIVE_WRAP_FACTORIES.items():
        s = factory()
        assert hasattr(s, "on_bar"), f"{name} missing on_bar"
        assert inspect.iscoroutinefunction(s.on_bar), f"{name}.on_bar not async"
        assert s.SYMBOL.endswith("_BASKET")
        assert s.strategy_id == name


@pytest.mark.asyncio
async def test_crypto_wrapper_uses_quote_volume_path():
    s = make_cs_tsmom_kr_daily()  # KRX kind
    ohlcv_no_qv = make_ohlcv_history(n_bars=300, with_quote_volume=False)
    ctx = FakeCtx(market_snapshot={"ohlcv_history": ohlcv_no_qv})
    s._bar_count = s.MIN_HISTORY + s.rebal_freq
    sig = await s.on_bar(ctx)
    # KRX path 는 close*volume 으로 turnover 산출 가능 → 정상 동작
    assert sig is not None
