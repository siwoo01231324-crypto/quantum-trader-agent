"""Regression tests: momo_btc_v2 마이그레이션 전후 bit-identical 검증 (US-005).

Legacy (전): on_bar 내부에서 compute_rsi 직접 호출.
New   (후): context["factors"]["rsi"] 를 엔진이 선계산해서 주입.

두 경로의 equity_curve 와 trades 전 필드가 완전 동일해야 함.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from typing import Literal

from backtest.engine import BacktestConfig, run_backtest
from backtest.protocol import Bar, Signal
from backtest.strategies.momo_btc_v2 import MomoBtcV2
from risk.sizing import consensus_kelly, ewma_sigma, fractional_kelly, kelly_continuous, vol_target
from signals.rsi import compute_rsi, detect_divergence

SizingMode = Literal["full", "half-kelly", "vol-target"]


# ---------------------------------------------------------------------------
# _LegacyMomoBtcV2: 마이그레이션 전 스냅샷 — 테스트 파일 내부 클래스
# ---------------------------------------------------------------------------

class _LegacyMomoBtcV2:
    """마이그레이션 전 momo_btc_v2 스냅샷 — 직접 compute_rsi 호출 방식.

    #87 (Signal 인터페이스 확장 + consensus_kelly + confidence) 반영 후 기준.
    현재 MomoBtcV2 와 유일한 차이: `rsi` 소스 (compute_rsi 직접 vs context 훅).
    """

    RSI_PERIOD: int = 14
    LOOKBACK: int = 14

    def __init__(
        self,
        *,
        sizing_mode: SizingMode = "full",
        sizing_lookback: int = 60,
        kelly_k: float = 0.5,
        target_annual: float = 0.20,
        periods_per_year: int = 365 * 96,
        ewma_lam: float = 0.94,
        use_consensus_kelly: bool = False,
        signal_agreement: float = 0.0,
        consensus_k_base: float = 0.5,
        consensus_k_max: float = 0.75,
    ) -> None:
        if sizing_lookback < 2:
            raise ValueError(f"sizing_lookback must be >= 2, got {sizing_lookback}")
        self.sizing_mode: SizingMode = sizing_mode
        self.sizing_lookback = sizing_lookback
        self.kelly_k = kelly_k
        self.target_annual = target_annual
        self.periods_per_year = periods_per_year
        self.ewma_lam = ewma_lam
        self.use_consensus_kelly = use_consensus_kelly
        self.signal_agreement = signal_agreement
        self.consensus_k_base = consensus_k_base
        self.consensus_k_max = consensus_k_max

    def on_init(self, context: dict) -> None:
        pass

    def _compute_confidence(
        self,
        div_magnitude: float,
        atr: float,
        bars_since_pivot: int,
    ) -> float:
        if atr <= 0.0:
            return 0.0
        return max(0.0, min(1.0, abs(div_magnitude) / atr * min(bars_since_pivot / self.LOOKBACK, 1.0)))

    def _entry_size(self, close: pd.Series) -> float:
        if self.sizing_mode == "full":
            return 1.0
        window = close.iloc[-(self.sizing_lookback + 1):]
        returns = window.pct_change().dropna()
        if len(returns) < 2:
            return 0.0
        sigma = ewma_sigma(returns, lam=self.ewma_lam)
        if self.sizing_mode == "half-kelly":
            mu = float(returns.mean())
            full = kelly_continuous(mu=mu, sigma=sigma)
            if self.use_consensus_kelly:
                return consensus_kelly(
                    full,
                    self.signal_agreement,
                    k_base=self.consensus_k_base,
                    k_max=self.consensus_k_max,
                )
            return fractional_kelly(full, k=self.kelly_k)
        if self.sizing_mode == "vol-target":
            return vol_target(
                sigma_period=sigma,
                target_annual=self.target_annual,
                periods_per_year=self.periods_per_year,
            )
        raise ValueError(f"unknown sizing_mode: {self.sizing_mode!r}")

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        min_bars = self.RSI_PERIOD + self.LOOKBACK * 2 + 1
        if len(history) < min_bars:
            return Signal(action="hold", size=0.0, reason="warmup")

        close = history["close"]
        rsi = compute_rsi(close, self.RSI_PERIOD)
        div = detect_divergence(close, rsi, self.LOOKBACK)

        latest = div.iloc[-1]
        if latest == "bullish":
            size = self._entry_size(close)
            if size <= 0.0:
                return Signal(action="hold", size=0.0, reason="bullish divergence (sized=0)")

            window = close.iloc[-(self.sizing_lookback + 1):]
            returns = window.pct_change().dropna()
            mu_hat = float(returns.mean()) if len(returns) >= 2 else 0.0

            from signals.atr import compute_atr
            atr_series = compute_atr(history["high"], history["low"], close)
            atr_val = float(atr_series.iloc[-1]) if not atr_series.empty else 0.0
            div_magnitude = float(close.iloc[-1] - close.iloc[-self.LOOKBACK - 1])
            recent_div = div.iloc[-self.LOOKBACK:]
            bullish_indices = [i for i, v in enumerate(recent_div) if v == "bullish"]
            bars_since_pivot = (len(recent_div) - bullish_indices[0]) if bullish_indices else self.LOOKBACK
            conf = self._compute_confidence(div_magnitude, atr_val, bars_since_pivot)

            return Signal(
                action="buy",
                size=size,
                reason="bullish divergence",
                expected_return=mu_hat,
                confidence=conf,
            )
        elif latest == "bearish":
            return Signal(action="sell", size=1.0, reason="bearish divergence")
        return Signal(action="hold", size=0.0, reason="no signal")


# ---------------------------------------------------------------------------
# 헬퍼: OHLCV 생성
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 1000, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="15min", tz="UTC")
    close = 100 + np.cumsum(rng.standard_normal(n) * 0.3)
    high = close + rng.random(n) * 0.2
    low = close - rng.random(n) * 0.2
    open_ = close + rng.standard_normal(n) * 0.1
    volume = rng.integers(1_000, 10_000, size=n).astype(float)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


# ---------------------------------------------------------------------------
# 공통 설정: max_drawdown_halt_pct=1.0 → halt 비활성으로 전 구간 실행
# ---------------------------------------------------------------------------

_CONFIG = BacktestConfig(max_drawdown_halt_pct=1.0)
_OHLCV = _make_ohlcv(n=1000, seed=7)


# ---------------------------------------------------------------------------
# 회귀 테스트
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("sizing_mode", ["full", "half-kelly", "vol-target"])
def test_migration_equity_curve_bit_identical(sizing_mode: str) -> None:
    """마이그레이션 전후 equity curve 가 bit-identical 이어야 한다."""
    legacy = _LegacyMomoBtcV2(sizing_mode=sizing_mode)
    new = MomoBtcV2(sizing_mode=sizing_mode)

    legacy_result = run_backtest(_OHLCV, legacy, _CONFIG)
    new_result = run_backtest(_OHLCV, new, _CONFIG)

    pd.testing.assert_series_equal(
        new_result.equity_curve,
        legacy_result.equity_curve,
        check_exact=True,
        check_names=False,
    )


@pytest.mark.parametrize("sizing_mode", ["full", "half-kelly", "vol-target"])
def test_migration_trades_bit_identical(sizing_mode: str) -> None:
    """마이그레이션 전후 trades 전 필드가 bit-identical 이어야 한다."""
    legacy = _LegacyMomoBtcV2(sizing_mode=sizing_mode)
    new = MomoBtcV2(sizing_mode=sizing_mode)

    legacy_trades = run_backtest(_OHLCV, legacy, _CONFIG).trades
    new_trades = run_backtest(_OHLCV, new, _CONFIG).trades

    assert len(new_trades) == len(legacy_trades), (
        f"[{sizing_mode}] trade count mismatch: {len(new_trades)} vs {len(legacy_trades)}"
    )
    assert len(new_trades) > 0, (
        f"[{sizing_mode}] no trades generated — seed/n 조합을 바꿔야 함"
    )

    for i, (new_t, legacy_t) in enumerate(zip(new_trades, legacy_trades)):
        for field in ("ts", "action", "price", "size", "commission", "reason"):
            assert new_t.get(field) == legacy_t.get(field), (
                f"[{sizing_mode}] trade[{i}].{field}: "
                f"{new_t.get(field)!r} != {legacy_t.get(field)!r}"
            )
