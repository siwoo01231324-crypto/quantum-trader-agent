"""Live universe-scanner: Donchian20 돌파 + BTC 레짐 게이트 추세추종 롱 (4h 스윙).

리서치 종결(2026-06-25, `docs/work/active/swing-strategy-research-handoff.draft.md`):
투매반등(평균회귀, 베어장 강함)의 짝 — 돌파(추세추종, 불장 강함). 둘은 레짐 비상관이라
병렬 운용 시 분산효과로 합성 MDD 완화(cap+breakout lev1 CAGR 24%/MDD 28%/Sharpe 1.0).
**앙상블 wrapper 금지**(분산파괴 REJECTED, `.ai.md`) → cap 과 별개 2전략 병렬.

Per-symbol entry rule (4h 종가):
    close[-1] > max(high[-21:-1])    # Donchian20 상단 돌파
    AND close[-1] > EMA200            # 자기 상승추세
    AND BTC 4h close > BTC EMA200      # BTC 레짐 게이트 (엣지·MDD 핵심)

BTC ohlcv 는 `live_macross_regime_v1` 처럼 orchestrator 가 박아주는
``market_snapshot["universe_ohlcv"]["BTCUSDT"]`` 로 읽는다.

청산 (2단):
  ① 하드 손절 = entry − 2×ATR(14) — 진입 시점 동적 override (`stop_loss_pct_override`).
  ② **추세 청산 = close < Donchian10 하단** (채널청산) — 엣지의 핵심. 가격 임계가 아니라
     매 봉 갱신되는 채널 레벨이라 `LivePositionRiskManager` 의 정적 stop/TP 로 표현 불가.
     `channel_exit_level(history)` 가 그 레벨(Donchian10 하단)을 반환 — orchestrator/
     risk manager 의 per-bar ratchet 배선이 이 값을 소비(미배선 시 ② 미동작, ① 만 작동).
time-stop 면제 (추세는 길게 — max_hold timeout 은 #5 검증서 오히려 손해).

regime_preference="trend" (regime gate 켤 때만) — 돌파는 추세 시장 전용.

⚠️ 활성화 게이트: 채널청산(②) 배선 전까지는 ① 2ATR 손절만 작동 → 백테스트 검증판과
다름(trailing 근사 PF 1.29 vs 채널 1.35). production.yaml commented candidate 유지,
채널청산 배선 + testnet 검증 후 활성화. spec "활성화 게이트" 참조.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin

_BTC_SYMBOL: str = "BTCUSDT"
_BTC_EMA_PERIOD: int = 200


def _ema_last(close: pd.Series, span: int) -> float | None:
    if len(close) < span:
        return None
    val = close.astype(float).ewm(span=span, adjust=False).mean().iloc[-1]
    return None if pd.isna(val) else float(val)


def _atr_last(history: pd.DataFrame, period: int) -> float | None:
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


def _btc_regime_up(btc_hist: pd.DataFrame | None, *, ema_period: int = _BTC_EMA_PERIOD) -> bool | None:
    """BTC 4h close ≥ EMA200 → True(상승장), < → False, 데이터부족 → None."""
    if btc_hist is None or len(btc_hist) < ema_period:
        return None
    ema = _ema_last(btc_hist["close"], ema_period)
    if ema is None:
        return None
    return float(btc_hist["close"].iloc[-1]) >= ema


class LiveDonchianBreakoutBtcGate(LiveScannerMixin):
    """Stateless per-symbol Donchian20 돌파 + BTC 레짐 게이트 (4h 스윙, long-only)."""

    strategy_id: ClassVar[str] = "live-donchian-breakout-btcgate"

    BREAKOUT_LOOKBACK: ClassVar[int] = 20    # Donchian 상단 (진입)
    EXIT_LOOKBACK: ClassVar[int] = 10        # Donchian 하단 (추세 청산)
    EMA_PERIOD: ClassVar[int] = 200
    ATR_PERIOD: ClassVar[int] = 14
    STOP_ATR_MULT: ClassVar[float] = 2.0     # 하드 손절 = entry − 2×ATR
    BREAKOUT_BUFFER: ClassVar[float] = 1.001  # 0.1% 명확한 돌파만 (live_breakout #326 교훈)
    MIN_HISTORY: ClassVar[int] = 205         # EMA200 + 여유

    # 정적 fallback. 동적 2ATR stop 이 정상 경로. TP 는 추세청산이 주청산이라 넓게.
    stop_loss_pct: ClassVar[float] = 0.08
    take_profit_pct: ClassVar[float] = 0.50
    trailing_stop_pct: ClassVar[float | None] = None

    regime_preference: ClassVar[str] = "trend"
    max_hold_sec: ClassVar[float | None] = None   # 추세 = time-stop 면제

    def __init__(
        self, *,
        default_size: float = 0.05,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        stop_atr_mult: float | None = None,
        atr_period: int | None = None,
        breakout_lookback: int | None = None,
        exit_lookback: int | None = None,
        btc_regime_gate: bool = True,
        max_concurrent_positions: int | None = None,
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
        if stop_atr_mult is not None:
            if stop_atr_mult <= 0:
                raise ValueError(f"stop_atr_mult must be > 0, got {stop_atr_mult}")
            self.STOP_ATR_MULT = float(stop_atr_mult)
        if atr_period is not None:
            if atr_period < 2:
                raise ValueError(f"atr_period must be >= 2, got {atr_period}")
            self.ATR_PERIOD = int(atr_period)
        if breakout_lookback is not None:
            if breakout_lookback < 2:
                raise ValueError(f"breakout_lookback must be >= 2, got {breakout_lookback}")
            self.BREAKOUT_LOOKBACK = int(breakout_lookback)
        if exit_lookback is not None:
            if exit_lookback < 2:
                raise ValueError(f"exit_lookback must be >= 2, got {exit_lookback}")
            self.EXIT_LOOKBACK = int(exit_lookback)
        self.btc_regime_gate = bool(btc_regime_gate)
        if max_concurrent_positions is not None:
            self.max_concurrent_positions = int(max_concurrent_positions)
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
        """돌파는 가장 유동적인 크립토 top-30 집중.

        깨끗한 크립토 메이저 재분석(2026-06-30)에서 5y/2y/1y 전 기간 top-30 이
        최고 PF(1.41/1.54/1.31), 확대 시 단조 열화(top-100 1y PF 1.05 붕괴).
        토큰화주식·상품·forex 가 섞인 BINANCE_USDT_TOP30(EUR 등) 대신 검증된
        크립토 유니버스 상위 30 만 사용. → docs/specs/strategies 참조.
        """
        from src.portfolio.binance_universe import SWING_CRYPTO_UNIVERSE
        return list(SWING_CRYPTO_UNIVERSE[:30])

    def channel_exit_level(self, history: pd.DataFrame) -> float | None:
        """추세 청산 레벨 = Donchian(EXIT_LOOKBACK) 하단 = min(low[-(K+1):-1]).

        보유 중 이 레벨 아래로 종가가 닫히면 추세 청산. orchestrator/risk manager
        의 per-bar ratchet 배선이 매 봉 호출해 소비(미배선 시 None 영향 — ① 손절만).
        직전 K봉 기준(현재 봉 제외) — look-ahead 없음.
        """
        low = history["low"]
        window = low.iloc[-(self.EXIT_LOOKBACK + 1):-1]
        if len(window) < self.EXIT_LOOKBACK:
            return None
        return float(window.min())

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        # 마감봉 게이트 — live forming-bar 제거(가짜돌파 방지, 백테스트 무변경).
        history, _closed_ts = self._closed_bar_history(ctx)
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        filter_reason = self._check_entry_filters(history)
        if filter_reason is not None:
            return Signal(action="hold", size=0.0, reason=filter_reason)

        close = float(history["close"].iloc[-1])
        if close <= 0:
            return Signal(action="hold", size=0.0, reason="bad_price")

        # 1) Donchian20 상단 돌파 (0.1% 버퍼 — marginal churn 차단, live_breakout #326)
        high = history["high"]
        baseline = high.iloc[-(self.BREAKOUT_LOOKBACK + 1):-1]
        if len(baseline) < self.BREAKOUT_LOOKBACK:
            return Signal(action="hold", size=0.0, reason="baseline_short")
        prior_max = float(baseline.max())
        if close < prior_max * self.BREAKOUT_BUFFER:
            return Signal(action="hold", size=0.0,
                          reason=f"no_breakout:close={close:.4g},max={prior_max:.4g}")

        # 2) 자기 상승추세 (close > EMA200)
        ema200 = _ema_last(history["close"], self.EMA_PERIOD)
        if ema200 is None:
            return Signal(action="hold", size=0.0, reason="ema_warmup")
        if not close > ema200:
            return Signal(action="hold", size=0.0,
                          reason=f"below_ema200:close={close:.4g}<{ema200:.4g}")

        # 3) BTC 레짐 게이트 (엣지·MDD 핵심)
        if self.btc_regime_gate:
            universe = snap.get("universe_ohlcv") if isinstance(snap, dict) else None
            btc_hist = universe.get(_BTC_SYMBOL) if isinstance(universe, dict) else None
            regime_up = _btc_regime_up(btc_hist)
            if regime_up is None:
                return Signal(action="hold", size=0.0, reason="btc_regime_unavailable")
            if not regime_up:
                return Signal(action="hold", size=0.0, reason="btc_regime_down")

        # 하드 손절 = entry − STOP_ATR_MULT×ATR → pct override
        atr = _atr_last(history, self.ATR_PERIOD)
        sl_override = None
        if atr is not None and atr > 0:
            sl_pct = self.STOP_ATR_MULT * atr / close
            if 0 < sl_pct < 1:
                sl_override = sl_pct

        # 봉당 1진입 dedup (live 한정) — 같은 마감봉 재신호/재진입 차단.
        _symbol = snap.get("symbol") if isinstance(snap, dict) else None
        if self._already_entered_bar(ctx, _symbol, _closed_ts):
            return Signal(action="hold", size=0.0, reason="bar_dedup")
        self._mark_entered_bar(ctx, _symbol, _closed_ts)

        return Signal(
            action="buy",
            size=self.default_size,
            reason=(
                f"donchian_breakout:close={close:.4g}>max{self.BREAKOUT_LOOKBACK}"
                f"={prior_max:.4g},ema200={ema200:.4g},btc_gate={self.btc_regime_gate}"
                + (f",atr_stop={sl_override:.3%}" if sl_override else "")
            ),
            confidence=0.6,
            stop_loss_pct_override=sl_override,
        )
