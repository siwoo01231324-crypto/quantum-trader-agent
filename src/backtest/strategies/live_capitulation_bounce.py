"""Live universe-scanner: 투매반등 (capitulation bounce) 평균회귀 롱 (4h 스윙).

리서치 종결(2026-06-25, `docs/work/active/swing-strategy-research-handoff.draft.md`):
크립토 인트라데이는 비용벽으로 죽고(스윙이 정답), 투매반등 평균회귀가 5y·정직10bp·
random-vs-signal 게이트를 통과한 두 번째 유효 신호(일봉 터틀에 이어). 4h·꼬리저점 손절이
스윗스팟 — 라이브 의미론(no-timeout)에서 오히려 더 강함(백테스트 timeout판 PF1.37 →
라이브 PF1.63 / exp +1.73%/거래).

Per-symbol entry rule (긴 아랫꼬리 투매바닥):
    low[-1] <= EMA20[-1] - N_DEV*ATR(14)        # 투매 깊이 (EMA20 아래 2.5×ATR)
    AND lower_wick >= WICK_MULT * body           # 긴 아랫꼬리 (1.5×몸통)
    AND close[-1] > open[-1]                      # 반등 양봉
    AND volume[-1] > VOL_MULT * mean(vol[-21:-1]) # 거래량 스파이크 (2×)

청산 (LivePositionRiskManager): **꼬리저점 손절 + 2R TP** 를 진입 시점 ATR/꼬리 거리로
계산해 Signal override 로 전달 — 정적 % 가 아니라 봉별 변동성 비례 동적 거리.
  stop_loss_pct_override   = (entry - wick_low) / entry        # 꼬리저점까지
  take_profit_pct_override = RR * (entry - wick_low) / entry   # 2R
time-stop 면제 (스윙 — 평균회귀는 반등까지 보유). `live_breakout_with_atr_stop` 의
ATR override 패턴 미러.

regime_preference="meanrev" (켤 때만) — 투매반등은 평균회귀/공포 국면 전용.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


def _ema_last(close: pd.Series, span: int) -> float | None:
    if len(close) < span:
        return None
    val = close.astype(float).ewm(span=span, adjust=False).mean().iloc[-1]
    return None if pd.isna(val) else float(val)


def _atr_last(history: pd.DataFrame, period: int) -> float | None:
    """ATR(period) — Wilder/SMA 근사 (live_breakout_with_atr_stop 미러). 부족 시 None."""
    if len(history) < period + 1:
        return None
    high = history["high"].astype(float).values
    low = history["low"].astype(float).values
    close = history["close"].astype(float).values
    prev_close = close[:-1]
    h, l = high[1:], low[1:]
    tr = [max(h[i] - l[i], abs(h[i] - prev_close[i]), abs(l[i] - prev_close[i]))
          for i in range(len(h))]
    if len(tr) < period:
        return None
    return float(sum(tr[-period:]) / float(period))


class LiveCapitulationBounce(LiveScannerMixin):
    """Stateless per-symbol 투매반등 평균회귀 감지기 (4h 스윙, long-only)."""

    strategy_id: ClassVar[str] = "live-capitulation-bounce"

    EMA_PERIOD: ClassVar[int] = 20
    ATR_PERIOD: ClassVar[int] = 14
    N_DEV: ClassVar[float] = 2.5         # EMA20 아래 N×ATR 투매 깊이
    WICK_MULT: ClassVar[float] = 1.5     # 아랫꼬리/몸통
    VOL_LOOKBACK: ClassVar[int] = 20
    VOL_MULT: ClassVar[float] = 2.0
    RR: ClassVar[float] = 2.0            # 손익비 — TP = RR × (꼬리저점 손절거리)
    MIN_HISTORY: ClassVar[int] = 30      # EMA20 + ATR14 + volMA20 warmup

    # 정적 fallback (override 미전달 시). 동적 꼬리저점 stop 이 정상 경로.
    stop_loss_pct: ClassVar[float] = 0.05
    take_profit_pct: ClassVar[float] = 0.10
    trailing_stop_pct: ClassVar[float | None] = None

    # 평균회귀 — regime gate 켤 때만 진입.
    regime_preference: ClassVar[str] = "meanrev"

    # 스윙 = time-stop 면제 (반등까지 보유). risk manager 가 ClassVar 읽어 등록.
    max_hold_sec: ClassVar[float | None] = None

    def __init__(
        self, *,
        default_size: float = 0.05,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        n_dev: float | None = None,
        wick_mult: float | None = None,
        vol_mult: float | None = None,
        rr: float | None = None,
        take_profit_roi: float | None = None,
        stop_loss_roi: float | None = None,
        leverage: float | None = None,
        cooldown_after_stop_sec: float | None = None,
        anomaly_guard_enabled: bool | None = None,
        trend_filter_enabled: bool | None = None,
        regime_filter_enabled: bool | None = None,
        regime_preference: str | None = None,
        adx_threshold: float | None = None,
        ema_slow_period: int | None = None,
        hurst_lookback: int | None = None,
        chop_period: int | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = default_size
        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct
        for kwarg, value, attr in (
            ("n_dev", n_dev, "N_DEV"), ("wick_mult", wick_mult, "WICK_MULT"),
            ("vol_mult", vol_mult, "VOL_MULT"), ("rr", rr, "RR"),
        ):
            if value is not None:
                if value <= 0:
                    raise ValueError(f"{kwarg} must be > 0, got {value}")
                setattr(self, attr, float(value))
        if cooldown_after_stop_sec is not None:
            if cooldown_after_stop_sec < 0:
                raise ValueError(
                    f"cooldown_after_stop_sec must be >= 0, got {cooldown_after_stop_sec}"
                )
            self.cooldown_after_stop_sec = cooldown_after_stop_sec
        self._apply_roi_targets(
            take_profit_roi=take_profit_roi,
            stop_loss_roi=stop_loss_roi,
            leverage=leverage,
        )
        self._apply_filter_kwargs(
            anomaly_guard_enabled=anomaly_guard_enabled,
            trend_filter_enabled=trend_filter_enabled,
            regime_filter_enabled=regime_filter_enabled,
            regime_preference=regime_preference,
            adx_threshold=adx_threshold,
            ema_slow_period=ema_slow_period,
            hurst_lookback=hurst_lookback,
            chop_period=chop_period,
        )

    @classmethod
    def get_interval(cls) -> str:
        return "4h"

    @classmethod
    def get_universe(cls) -> list[str]:
        """투매반등은 크립토 top-100 확대.

        깨끗한 크립토 메이저 재분석(2026-06-30): 확대해도 PF 유지/상승(2y top-100
        PF 2.14, 1y 2.54)+거래수 2.3배. 돌파와 달리 넓을수록 유리. 토큰화주식·
        상품·forex 섞인 BINANCE_USDT_TOP30 대신 검증 크립토 유니버스 상위 100.
        → docs/specs/strategies 참조.
        """
        from src.portfolio.binance_universe import SWING_CRYPTO_UNIVERSE
        return list(SWING_CRYPTO_UNIVERSE[:100])

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        filter_reason = self._check_entry_filters(history)
        if filter_reason is not None:
            return Signal(action="hold", size=0.0, reason=filter_reason)

        close = float(history["close"].iloc[-1])
        open_ = float(history["open"].iloc[-1])
        low = float(history["low"].iloc[-1])
        if close <= 0 or low <= 0:
            return Signal(action="hold", size=0.0, reason="bad_price")

        # 1) 반등 양봉
        if not close > open_:
            return Signal(action="hold", size=0.0, reason="not_bullish")

        # 2) 긴 아랫꼬리 (>= WICK_MULT × 몸통)
        body = abs(close - open_)
        lower_wick = min(close, open_) - low
        if lower_wick < self.WICK_MULT * max(body, 1e-9):
            return Signal(action="hold", size=0.0,
                          reason=f"wick_short:{lower_wick:.4g}<{self.WICK_MULT}xbody")

        # 3) 투매 깊이 — low <= EMA20 - N_DEV*ATR
        ema20 = _ema_last(history["close"], self.EMA_PERIOD)
        atr = _atr_last(history, self.ATR_PERIOD)
        if ema20 is None or atr is None or atr <= 0:
            return Signal(action="hold", size=0.0, reason="indicator_warmup")
        capitulation_level = ema20 - self.N_DEV * atr
        if not low <= capitulation_level:
            return Signal(action="hold", size=0.0,
                          reason=f"no_capitulation:low={low:.4g}>{capitulation_level:.4g}")

        # 4) 거래량 스파이크
        volume = history["volume"]
        baseline = volume.iloc[-(self.VOL_LOOKBACK + 1):-1]
        if len(baseline) < self.VOL_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="vol_baseline_short")
        vol_ma = float(baseline.mean())
        if vol_ma <= 0:
            return Signal(action="hold", size=0.0, reason="vol_ma_zero")
        last_vol = float(volume.iloc[-1])
        vol_ratio = last_vol / vol_ma
        if vol_ratio < self.VOL_MULT:
            return Signal(action="hold", size=0.0, reason=f"vol_low:{vol_ratio:.2f}")

        # 동적 청산 거리 — 꼬리저점 손절 + 2R TP (entry≈close).
        sl_pct = (close - low) / close
        tp_pct = self.RR * sl_pct
        # 위생 가드 — risk manager StopTpPolicy 는 (0,1) 만 허용.
        if not (0 < sl_pct < 1):
            return Signal(action="hold", size=0.0, reason=f"sl_pct_oob:{sl_pct:.4f}")
        tp_pct = min(tp_pct, 0.999)

        # confidence ∈ [0,1] — 투매 깊이 + 거래량 초과.
        depth = min(1.0, (capitulation_level - low) / max(atr, 1e-9) + 0.3) if low < capitulation_level else 0.3
        confidence = max(0.0, min(1.0, 0.4 + 0.3 * (vol_ratio / self.VOL_MULT - 1.0) + 0.3 * depth))

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"capitulation_bounce:low={low:.4g}<={capitulation_level:.4g},"
                f"wick={lower_wick:.4g},vol_ratio={vol_ratio:.2f},"
                f"sl={sl_pct:.3%},tp={tp_pct:.3%}"
            ),
            confidence=confidence,
            stop_loss_pct_override=sl_pct,
            take_profit_pct_override=tp_pct,
        )
