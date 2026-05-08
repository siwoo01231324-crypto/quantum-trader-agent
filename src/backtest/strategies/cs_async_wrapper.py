"""AsyncStrategy wrapper for cross-sectional universe-scan strategies (#218).

각 cs_* 모듈의 `compute_weights(...)` 함수를 `AsyncStrategy` 프로토콜
(`src/backtest/protocol.py`) 준수 인스턴스로 wrapping.

핵심 책임:
- ctx.market_snapshot["ohlcv_history"] 의 dict[code → DataFrame] 을
  종목×시점 패널로 변환
- 매 N바 (rebal_freq, default 5 = 주간) 에 한 번만 compute_weights 호출
- 비-리밸일은 `Signal(action="hold")` 반환
- 리밸일에는 basket-level `Signal(action="buy", size=exposure_target)` 반환
  + 보유 weights 를 ctx 에 추가 (broker 가 이를 종목별 orders 로 변환)

본 wrapper 는 broker 동적 universe 확장 (#218 Phase 2) 와 함께 동작하도록
설계됨 — 단독으로는 orchestrator 등록만 가능하고 실제 종목별 발주는 broker
지원 필요. 따라서 본 phase 1 산출물은 "orchestrator 등록 + Signal 반환
스모크" 까지 — 실제 라이브 발주는 broker 확장 PR 후 enable.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd

from backtest.protocol import Signal


class CrossSectionalAsyncStrategy:
    """Generic AsyncStrategy wrapper for universe-scan cs_* compute_weights.

    Args:
        strategy_id: snake_case identifier — register_strategy_returns 키.
        compute_weights_fn: 모듈의 `compute_weights` 함수 (panels → weights).
        symbol: basket symbol (예: "KRX_TOP350_BASKET", "CRYPTO_TOP30_BASKET").
        rebal_freq: 리밸 주기 (bar 단위).
        warmup_bars: 첫 신호 반환까지 필요한 최소 bar 수.
        weights_kind: "krx" (close + turnover) | "crypto" (close + quote_volume) |
                      "krx_hlc" (high + low + close + turnover, ADX/MA 용).
        params: compute_weights_fn 에 전달할 추가 kwargs.
    """

    SYMBOL: str
    MIN_HISTORY: int

    def __init__(
        self,
        strategy_id: str,
        compute_weights_fn: Callable,
        symbol: str,
        *,
        rebal_freq: int = 5,
        warmup_bars: int = 252,
        weights_kind: str = "krx",
        params: Optional[dict] = None,
    ) -> None:
        self.strategy_id = strategy_id
        self.compute_weights_fn = compute_weights_fn
        self.SYMBOL = symbol
        self.rebal_freq = rebal_freq
        self.MIN_HISTORY = warmup_bars
        self.weights_kind = weights_kind
        self.params = params or {}
        self._bar_count = 0
        self._last_weights: Optional[pd.Series] = None

    def _build_panels(self, ohlcv_history: dict) -> tuple:
        """Convert ohlcv_history dict → (close, [high, low,] turnover/quote_volume)."""
        if not ohlcv_history:
            return None
        codes = list(ohlcv_history.keys())
        closes = pd.DataFrame({c: df["close"] for c, df in ohlcv_history.items()})
        if self.weights_kind == "krx":
            turnover = pd.DataFrame({
                c: df["close"] * df["volume"] for c, df in ohlcv_history.items()
            }).reindex(closes.index)
            return (closes, turnover)
        if self.weights_kind == "krx_hlc":
            highs = pd.DataFrame({c: df["high"] for c, df in ohlcv_history.items()}).reindex(closes.index)
            lows = pd.DataFrame({c: df["low"] for c, df in ohlcv_history.items()}).reindex(closes.index)
            turnover = pd.DataFrame({
                c: df["close"] * df["volume"] for c, df in ohlcv_history.items()
            }).reindex(closes.index)
            return (highs, lows, closes, turnover)
        if self.weights_kind == "crypto":
            qv = pd.DataFrame({
                c: df["quote_volume"] for c, df in ohlcv_history.items()
                if "quote_volume" in df.columns
            }).reindex(closes.index)
            return (closes, qv)
        raise ValueError(f"Unknown weights_kind: {self.weights_kind}")

    async def on_bar(self, ctx: object) -> Signal | None:
        self._bar_count += 1
        ms = getattr(ctx, "market_snapshot", None) or {}
        ohlcv = ms.get("ohlcv_history") if isinstance(ms, dict) else None

        # Warmup or non-rebal bar → hold
        if not ohlcv or self._bar_count < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0,
                          reason=f"{self.strategy_id}_warmup")
        if self._bar_count % self.rebal_freq != 0:
            return Signal(action="hold", size=0.0,
                          reason=f"{self.strategy_id}_no_rebal")

        try:
            panels = self._build_panels(ohlcv)
            if panels is None:
                return Signal(action="hold", size=0.0,
                              reason=f"{self.strategy_id}_no_data")
            weights_df = self.compute_weights_fn(*panels, **self.params)
            last_row = weights_df.iloc[-1]
            self._last_weights = last_row[last_row > 0]
            exposure = float(last_row.sum())
        except Exception as e:
            return Signal(action="hold", size=0.0,
                          reason=f"{self.strategy_id}_error:{type(e).__name__}")

        if exposure <= 0:
            return Signal(action="sell", size=0.0,
                          reason=f"{self.strategy_id}_zero_exposure")

        return Signal(
            action="buy",
            size=exposure,
            reason=f"{self.strategy_id}_rebal:{len(self._last_weights)}_picks",
        )

    @property
    def latest_weights(self) -> Optional[pd.Series]:
        """Current basket weights (for broker → orders conversion)."""
        return self._last_weights


# ---------------------------------------------------------------------------
# Per-strategy factory functions
# ---------------------------------------------------------------------------

def make_cs_tsmom_kr_daily(**params) -> CrossSectionalAsyncStrategy:
    from backtest.strategies import cs_tsmom_kr_daily
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_tsmom_kr_daily",
        compute_weights_fn=cs_tsmom_kr_daily.compute_weights,
        symbol="KRX_TOP350_BASKET",
        rebal_freq=5, warmup_bars=252, weights_kind="krx",
        params={"top_n": 20, **params},
    )


def make_cs_rsi_div_kr(**params) -> CrossSectionalAsyncStrategy:
    from backtest.strategies import cs_rsi_div_kr
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_rsi_div_kr",
        compute_weights_fn=cs_rsi_div_kr.compute_weights,
        symbol="KRX_TOP350_BASKET",
        rebal_freq=5, warmup_bars=40, weights_kind="krx",
        params={"top_n": 20, **params},
    )


def make_cs_bb_macd_kr(**params) -> CrossSectionalAsyncStrategy:
    """⚠️ INACTIVE — bench Sharpe -0.32. Production 미등록."""
    from backtest.strategies import cs_bb_macd_kr
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_bb_macd_kr",
        compute_weights_fn=cs_bb_macd_kr.compute_weights,
        symbol="KRX_TOP350_BASKET",
        rebal_freq=5, warmup_bars=40, weights_kind="krx",
        params={"top_n": 20, **params},
    )


def make_cs_adx_ma_kr(**params) -> CrossSectionalAsyncStrategy:
    from backtest.strategies import cs_adx_ma_kr
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_adx_ma_kr",
        compute_weights_fn=cs_adx_ma_kr.compute_weights,
        symbol="KRX_TOP350_BASKET",
        rebal_freq=5, warmup_bars=40, weights_kind="krx_hlc",
        params={"top_n": 20, **params},
    )


def make_cs_tsmom_crypto_daily(**params) -> CrossSectionalAsyncStrategy:
    from backtest.strategies import cs_tsmom_crypto_daily
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_tsmom_crypto_daily",
        compute_weights_fn=cs_tsmom_crypto_daily.compute_weights,
        symbol="CRYPTO_TOP30_BASKET",
        rebal_freq=5, warmup_bars=252, weights_kind="crypto",
        params={"top_n": 10, **params},
    )


def make_cs_rsi_div_crypto(**params) -> CrossSectionalAsyncStrategy:
    from backtest.strategies import cs_rsi_div_crypto
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_rsi_div_crypto",
        compute_weights_fn=cs_rsi_div_crypto.compute_weights,
        symbol="CRYPTO_TOP30_BASKET",
        rebal_freq=5, warmup_bars=40, weights_kind="crypto",
        params={"top_n": 10, **params},
    )


def make_cs_macd_vol_crypto(**params) -> CrossSectionalAsyncStrategy:
    from backtest.strategies import cs_macd_vol_crypto
    return CrossSectionalAsyncStrategy(
        strategy_id="cs_macd_vol_crypto",
        compute_weights_fn=cs_macd_vol_crypto.compute_weights,
        symbol="CRYPTO_TOP30_BASKET",
        rebal_freq=5, warmup_bars=40, weights_kind="crypto",
        params={"top_n": 10, "vol_ceiling": 2.0, **params},
    )


# Registry of active wrap factories (cs_bb_macd_kr 제외 — inactive)
ACTIVE_WRAP_FACTORIES: dict[str, Callable] = {
    "cs_tsmom_kr_daily": make_cs_tsmom_kr_daily,
    "cs_rsi_div_kr": make_cs_rsi_div_kr,
    "cs_adx_ma_kr": make_cs_adx_ma_kr,
    "cs_tsmom_crypto_daily": make_cs_tsmom_crypto_daily,
    "cs_rsi_div_crypto": make_cs_rsi_div_crypto,
    "cs_macd_vol_crypto": make_cs_macd_vol_crypto,
}
