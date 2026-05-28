"""Live-scanner paradigm helpers (#227).

Live-scanner strategies are evaluated *per-symbol on every tick* — a single
``on_bar(ctx)`` call receives one symbol's snapshot and returns at most one
``Signal``. The orchestrator (``AsyncStrategyOrchestrator.run_bar``) detects
these strategies via the ``is_live_scanner`` class attribute and iterates
``market_snapshot["ohlcv_history"]`` (``dict[str, pd.DataFrame]``), dispatching
once per symbol.

Position-level exits (stop_loss / take_profit / trailing_stop) are NOT the
strategy's responsibility. The strategy only emits ``buy`` signals; exits are
enforced by ``LivePositionRiskManager`` (S2) consuming the class attributes
declared here.

This is the third paradigm alongside ``universe-scan`` (cross-sectional
ranking, weekly rebal — see ``docs/specs/universe-scan-strategy-pattern.md``)
and ``single-ticker`` (legacy, e.g. ``momo_btc_v2``).

2026-05-26 — A+B+C entry filters added (anomaly_guard, trend_filter,
regime_filter). Default OFF to preserve existing behavior; live deployments
enable via production.yaml kwargs. See ``docs/patch-notes/index.yaml`` v0.6.0.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.strategies import _indicators


class LiveScannerMixin:
    """Marker mixin for live-scanner paradigm strategies.

    Subclasses inherit ``is_live_scanner = True`` which opts them into
    per-symbol dispatch in ``AsyncStrategyOrchestrator.run_bar``. Stop/TP
    thresholds declared here are consumed by ``LivePositionRiskManager``
    (added in S2 of #227); strategies themselves never emit ``sell`` signals.

    Subclass example:

        class LiveRsiOversoldVolumeSpike(LiveScannerMixin):
            stop_loss_pct: ClassVar[float] = 0.03
            take_profit_pct: ClassVar[float] = 0.06

            async def on_bar(self, ctx) -> Signal | None:
                snap = ctx["market_snapshot"]      # single-symbol snapshot
                history = snap["history"]
                ...
                return Signal(action="buy", size=0.05, reason="...")
    """

    is_live_scanner: ClassVar[bool] = True
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    trailing_stop_pct: ClassVar[float | None] = None

    # ── Dynamic Universe Architecture (2026-05-28, Phase 1) ───────────────
    # 전략별 universe + interval 선언. 기본값은 BINANCE_USDT_TOP30 + "1d"
    # (기존 hardcoded 동작과 byte-identical). 동적 universe 가 필요한 전략은
    # subclass 에서 override.
    @classmethod
    def get_universe(cls) -> list[str]:
        """전략이 fetch 받고 싶은 symbol 목록. 기본 — BINANCE_USDT_TOP30.

        live_run 이 매 bar 마다 active 전략들에서 호출 후 union 으로 fetch.
        """
        from src.portfolio.binance_universe import BINANCE_USDT_TOP30
        return list(BINANCE_USDT_TOP30)

    @classmethod
    def get_interval(cls) -> str:
        """전략이 받고 싶은 봉 interval — "1d" / "1h" / "15m" 등.

        기본 "1d" — 기존 fetch_universe_klines 의 default 와 동일.
        """
        return "1d"
    # 2026-05-21: stop/TP 청산 직후 같은 (sid, symbol) 재진입 차단 시간 (초).
    # Default 0.0 = 차단 없음 (기존 동작 보존). 0 보다 크면 orchestrator 가
    # `release_live_position()` 호출 시점에 monotonic 타임스탬프를 기록하고,
    # 그 시점 + cooldown 안에 들어오는 BUY 신호는 통과시키지 않는다.
    # Churn 방지용 (예: ATR breakout 직후 stop 맞고 즉시 재진입 반복).
    # production.yaml 의 strategy kwargs 로 override 가능.
    cooldown_after_stop_sec: ClassVar[float] = 0.0

    # 2026-05-22: 레버리지 트레이딩용 ROI 기반 익절/손절. take_profit_roi /
    # stop_loss_roi (증거금 수익률) 가 주어지면 `_apply_roi_targets` 가
    # 가격 pct = ROI / leverage 로 환산해 take_profit_pct / stop_loss_pct 를
    # 덮어쓴다. None 이면 기존 정적 pct 동작 보존.
    take_profit_roi: ClassVar[float | None] = None
    stop_loss_roi: ClassVar[float | None] = None
    leverage: ClassVar[float | None] = None

    # ── 2026-05-26 A+B+C 진입 필터 (docs/patch-notes/index.yaml v0.6.0) ──────
    #
    # B: data anomaly guard — last close 또는 ATR 이 0 이면 진입 금지. TRX
    # 2026-05-24 atr_breakout:last=0,max20=0 같은 데이터 결함 사고 방지.
    # 기본 ON — 누구도 0/NaN 가격에 매수할 이유 없음.
    anomaly_guard_enabled: ClassVar[bool] = True

    # A: trend filter — ADX(14) >= adx_threshold AND last_close >= EMA(slow_period).
    # 횡보장에서 breakout 신호 차단용. 기본 OFF — production.yaml 에서 켠다.
    trend_filter_enabled: ClassVar[bool] = False
    adx_threshold: ClassVar[float] = _indicators.ADX_TREND_DEFAULT  # 20
    adx_period: ClassVar[int] = 14
    ema_slow_period: ClassVar[int] = 50

    # C: regime gate — Hurst exponent + Choppiness Index 로 현재 시장의
    # 추세성/평균회귀성/횡보성 판정. 각 전략은 ``regime_preference`` 로
    # "내가 잘 맞는 시장" 을 선언:
    #   "trend"   : H>=0.55 또는 CI<=38.2 일 때만 진입 (breakout)
    #   "meanrev" : H<=0.45 또는 CI>=61.8 일 때만 진입 (bb_bounce, oversold)
    #   "any"     : regime 무시 (legacy)
    # 기본 OFF — production.yaml 에서 전략별로 켠다.
    regime_filter_enabled: ClassVar[bool] = False
    regime_preference: ClassVar[str] = "any"
    hurst_lookback: ClassVar[int] = 100
    chop_period: ClassVar[int] = 14
    hurst_trend_threshold: ClassVar[float] = _indicators.HURST_TREND_DEFAULT
    hurst_meanrev_threshold: ClassVar[float] = _indicators.HURST_MEANREV_DEFAULT
    chop_range_threshold: ClassVar[float] = _indicators.CHOPPINESS_RANGE_DEFAULT
    chop_trend_threshold: ClassVar[float] = _indicators.CHOPPINESS_TREND_DEFAULT

    def _apply_roi_targets(
        self,
        *,
        take_profit_roi: float | None,
        stop_loss_roi: float | None,
        leverage: float | None,
    ) -> None:
        """ROI 기반 익절/손절 목표를 *가격* pct 로 환산해 instance 에 적용.

        레버리지 트레이딩에서 익절/손절의 직관적 기준은 가격 변동이 아니라
        ROI(증거금 수익률) 다. ROI = 가격변동% × leverage 이므로 거꾸로
        가격 pct = ROI / leverage. ``take_profit_pct`` / ``stop_loss_pct``
        (가격 기준 — ``LivePositionRiskManager`` 가 ``entry × (1 ± pct)`` 로
        청산 평가) 를 이 환산값으로 덮어쓴다.

        예: take_profit_roi=0.12, leverage=10 → take_profit_pct=0.012
        (가격 +1.2% = ROI +12%). ROI 인자가 모두 None 이면 no-op — 기존
        정적 pct 동작 그대로.
        """
        if take_profit_roi is None and stop_loss_roi is None:
            return
        if leverage is None or leverage <= 0:
            raise ValueError(
                "take_profit_roi / stop_loss_roi 사용 시 leverage > 0 필수 "
                f"(got leverage={leverage})"
            )
        if take_profit_roi is not None:
            if take_profit_roi <= 0:
                raise ValueError(
                    f"take_profit_roi must be > 0, got {take_profit_roi}"
                )
            self.take_profit_pct = take_profit_roi / leverage
        if stop_loss_roi is not None:
            if stop_loss_roi <= 0:
                raise ValueError(
                    f"stop_loss_roi must be > 0, got {stop_loss_roi}"
                )
            self.stop_loss_pct = stop_loss_roi / leverage

    # ── 2026-05-26 A+B+C 진입 필터 API ────────────────────────────────────────
    def _apply_filter_kwargs(
        self,
        *,
        anomaly_guard_enabled: bool | None = None,
        trend_filter_enabled: bool | None = None,
        regime_filter_enabled: bool | None = None,
        regime_preference: str | None = None,
        adx_threshold: float | None = None,
        ema_slow_period: int | None = None,
        hurst_lookback: int | None = None,
        chop_period: int | None = None,
    ) -> None:
        """Apply production.yaml filter kwargs to this instance (instance-shadow
        of ClassVar defaults). Each None is no-op so callers can pass through
        ``**filter_kwargs`` blindly. Called once from ``__init__``.
        """
        if anomaly_guard_enabled is not None:
            self.anomaly_guard_enabled = bool(anomaly_guard_enabled)
        if trend_filter_enabled is not None:
            self.trend_filter_enabled = bool(trend_filter_enabled)
        if regime_filter_enabled is not None:
            self.regime_filter_enabled = bool(regime_filter_enabled)
        if regime_preference is not None:
            if regime_preference not in {"trend", "meanrev", "any"}:
                raise ValueError(
                    "regime_preference must be 'trend'/'meanrev'/'any', "
                    f"got {regime_preference!r}"
                )
            self.regime_preference = regime_preference
        if adx_threshold is not None:
            if adx_threshold <= 0:
                raise ValueError(f"adx_threshold must be > 0, got {adx_threshold}")
            self.adx_threshold = float(adx_threshold)
        if ema_slow_period is not None:
            if ema_slow_period < 2:
                raise ValueError(
                    f"ema_slow_period must be >= 2, got {ema_slow_period}"
                )
            self.ema_slow_period = int(ema_slow_period)
        if hurst_lookback is not None:
            if hurst_lookback < 20:
                raise ValueError(
                    f"hurst_lookback must be >= 20, got {hurst_lookback}"
                )
            self.hurst_lookback = int(hurst_lookback)
        if chop_period is not None:
            if chop_period < 2:
                raise ValueError(f"chop_period must be >= 2, got {chop_period}")
            self.chop_period = int(chop_period)

    def _check_data_anomaly(self, history: "pd.DataFrame") -> str | None:
        """B: last close 또는 ATR(14) 가 0 이면 reason 반환. else None."""
        if not self.anomaly_guard_enabled:
            return None
        if history is None or len(history) == 0:
            return "anomaly:empty_history"
        last_close = float(history["close"].iloc[-1])
        if last_close <= 0:
            return f"anomaly:last_close={last_close}"
        # ATR 빠른 계산 — high-low 평균. 14봉 없으면 skip (warmup 단계).
        if len(history) >= 15:
            recent = history.iloc[-15:]
            tr_proxy = float((recent["high"] - recent["low"]).mean())
            if tr_proxy <= 0:
                return "anomaly:atr_zero"
        return None

    def _check_trend_filter(self, history: "pd.DataFrame") -> str | None:
        """A: ADX(adx_period) >= adx_threshold AND last_close >= EMA(slow_period)
        둘 다 통과해야 None 반환. 데이터 부족 시 None (warmup → 차단 안 함).
        """
        if not self.trend_filter_enabled:
            return None
        adx_val = _indicators.adx(history, period=int(self.adx_period))
        if adx_val is not None and adx_val < float(self.adx_threshold):
            return f"trend_filter:adx={adx_val:.1f}<{self.adx_threshold:.1f}"
        ema_slow = _indicators.ema(history["close"], int(self.ema_slow_period))
        if len(ema_slow) > 0 and not pd.isna(ema_slow.iloc[-1]):
            last_close = float(history["close"].iloc[-1])
            slow_val = float(ema_slow.iloc[-1])
            if last_close < slow_val:
                return (
                    f"trend_filter:close={last_close:.4f}<ema{self.ema_slow_period}"
                    f"={slow_val:.4f}"
                )
        return None

    def _check_regime_filter(self, history: "pd.DataFrame") -> str | None:
        """C: Hurst + Choppiness 로 현재 레짐 판정 후
        ``regime_preference`` 와 맞지 않으면 reason 반환.
        """
        if not self.regime_filter_enabled:
            return None
        pref = self.regime_preference
        if pref == "any":
            return None
        h = _indicators.hurst_exponent(history["close"], int(self.hurst_lookback))
        ci = _indicators.choppiness_index(history, int(self.chop_period))
        if h is None and ci is None:
            return None   # warmup — let trade through
        if pref == "trend":
            # 추세 시장에서만 통과: H>=trend_thr 또는 CI<=trend_thr.
            h_ok = (h is not None) and (h >= float(self.hurst_trend_threshold))
            ci_ok = (ci is not None) and (ci <= float(self.chop_trend_threshold))
            if not (h_ok or ci_ok):
                return (
                    f"regime_filter:pref=trend,"
                    f"h={'NA' if h is None else f'{h:.2f}'},"
                    f"ci={'NA' if ci is None else f'{ci:.1f}'}"
                )
        elif pref == "meanrev":
            # 평균회귀/횡보 시장에서만 통과: H<=meanrev_thr 또는 CI>=range_thr.
            h_ok = (h is not None) and (h <= float(self.hurst_meanrev_threshold))
            ci_ok = (ci is not None) and (ci >= float(self.chop_range_threshold))
            if not (h_ok or ci_ok):
                return (
                    f"regime_filter:pref=meanrev,"
                    f"h={'NA' if h is None else f'{h:.2f}'},"
                    f"ci={'NA' if ci is None else f'{ci:.1f}'}"
                )
        return None

    def _check_entry_filters(self, history: "pd.DataFrame") -> str | None:
        """A+B+C 통합 게이트. None 이면 통과, else hold reason 문자열.

        호출 위치: 각 live-scanner ``on_bar`` 의 warmup/baseline 체크 직후,
        구체적 진입 룰 평가 직전. 한 메서드 호출로 3 필터 모두 적용된다.
        """
        for check in (
            self._check_data_anomaly,
            self._check_trend_filter,
            self._check_regime_filter,
        ):
            reason = check(history)
            if reason is not None:
                return reason
        return None
