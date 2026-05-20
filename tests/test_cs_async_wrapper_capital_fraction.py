"""CrossSectionalAsyncStrategy.capital_fraction 검증 (2026-05-21).

사용자 요청: cs-tsmom-crypto-daily 에 잔고의 50% 만 배정. 종목당 deploy =
capital_fraction × (1/top_n) × equity. weights 와 exposure 둘 다 스케일.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.protocol import Signal
from backtest.strategies.cs_async_wrapper import CrossSectionalAsyncStrategy


def _make_ctx(n_bars: int = 260, n_symbols: int = 5) -> object:
    """Synth ohlcv_history for wrapper.on_bar."""
    rng = np.random.default_rng(42)
    idx = pd.date_range("2024-01-01", periods=n_bars, freq="D")
    ohlcv = {}
    for i in range(n_symbols):
        sym = f"SYM{i:02d}"
        rets = rng.normal(0.001, 0.02, n_bars)
        close = 100 * (1 + pd.Series(rets, index=idx)).cumprod().values
        ohlcv[sym] = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": np.full(n_bars, 1_000_000.0),
            "quote_volume": np.full(n_bars, 20_000_000.0),
        }, index=idx)
    class _Ctx: pass
    c = _Ctx()
    c.market_snapshot = {"ohlcv_history": ohlcv}
    return c


def _fake_compute_weights(closes: pd.DataFrame, qv: pd.DataFrame,
                           top_n: int = 3, **_kw) -> pd.DataFrame:
    """Trivial deterministic compute_weights: top-N by raw last close."""
    n = len(closes)
    w = pd.DataFrame(0.0, index=closes.index, columns=closes.columns)
    if n < 5:
        return w
    last = closes.iloc[-1]
    picks = last.nlargest(top_n).index
    w.loc[closes.index[-1], picks] = 1.0 / top_n
    return w


@pytest.mark.asyncio
async def test_capital_fraction_default_is_one():
    """default 는 1.0 (기존 동작 그대로) — 5y backtest 비호환 방지."""
    strat = CrossSectionalAsyncStrategy(
        strategy_id="t",
        compute_weights_fn=_fake_compute_weights,
        weights_kind="crypto",
        rebal_freq=1,
        warmup_bars=5,
    )
    assert strat.capital_fraction == 1.0
    ctx = _make_ctx(n_bars=10, n_symbols=5)
    for _ in range(5):
        await strat.on_bar(ctx)  # warmup
    sig = await strat.on_bar(ctx)
    assert sig.action == "buy"
    # 3 picks × 1/3 = 1.0
    assert sig.size == pytest.approx(1.0, abs=1e-9)


@pytest.mark.asyncio
async def test_capital_fraction_half_scales_size_and_weights():
    """capital_fraction=0.5 → size=0.5, 각 weight = 0.5/3 ≈ 0.1667."""
    strat = CrossSectionalAsyncStrategy(
        strategy_id="t",
        compute_weights_fn=_fake_compute_weights,
        weights_kind="crypto",
        rebal_freq=1,
        warmup_bars=5,
        capital_fraction=0.5,
    )
    assert strat.capital_fraction == 0.5
    ctx = _make_ctx(n_bars=10, n_symbols=5)
    for _ in range(5):
        await strat.on_bar(ctx)
    sig = await strat.on_bar(ctx)
    assert sig.action == "buy"
    assert sig.size == pytest.approx(0.5, abs=1e-9)
    # weights 도 같이 스케일됨 — downstream broker 가 latest_weights 로
    # 종목별 qty 산정 시 일관성 유지.
    weights = strat.latest_weights
    assert weights is not None
    assert weights.sum() == pytest.approx(0.5, abs=1e-9)
    # 각 종목은 capital_fraction × (1/top_n) = 0.5 × 0.333 ≈ 0.167
    for v in weights.values:
        assert v == pytest.approx(0.5 / 3, abs=1e-9)


def test_invalid_capital_fraction_raises():
    for bad in (0.0, -0.5, 1.5):
        with pytest.raises(ValueError, match="capital_fraction"):
            CrossSectionalAsyncStrategy(
                strategy_id="t",
                compute_weights_fn=_fake_compute_weights,
                weights_kind="crypto",
                capital_fraction=bad,
            )
