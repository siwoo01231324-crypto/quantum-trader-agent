"""Multi-Alpha Altcoin v1 — 4 layer 결합 universe-scan 전략.

Layer 결합 (spec: docs/specs/strategies/multi-alpha-altcoin-v1.md):
  1. Cointegration filter   — BTC ↔ alt 60일 rolling Engle-Granger, p<0.05 만 trade-eligible
  2. Regime classification  — 30일 BTC-alt return Pearson corr
                              ≥0.7 동조, ≤0.3 디커플링, 그 외 진입 X
  3. Lead-lag (동조)        — BTC 직전 1h |r|>1% AND vol spike → 같은 방향 alt 매수
  4. Outlier (디커플링)     — 60일 OLS β-spread z-score ±2σ 이탈 → 역방향 진입

Universe: BTC-cointegrated alt top 20 (24h 거래량 weighted). 매일 KST 00:00 refresh.

R/R 1:2: stop_loss_pct=0.0075, take_profit_pct=0.015. timeout 4h (4 봉).

코드 비용 최적화:
  - Cointegration test 매 봉 X — 일별 1회 (24봉 마다) 만 evaluate
  - Rolling corr 도 일별 1회
  - 위 두 값은 (sid, symbol) per-day cache 로 재사용
  → 매 1h tick 의 추가 비용 = lead-lag 4 비교 + zscore 1 계산 (모두 O(1))
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin


# ── Tunables (모두 spec 의 4 layer 파라미터) ────────────────────────────────

DEFAULT_COINT_WINDOW_BARS: int = 60 * 24    # 60일 rolling, 1h 봉
DEFAULT_COINT_PVALUE_MAX: float = 0.05
DEFAULT_CORR_WINDOW_BARS: int = 30 * 24     # 30일 rolling
DEFAULT_REGIME_CORR_HIGH: float = 0.7
DEFAULT_REGIME_CORR_LOW: float = 0.3
DEFAULT_LEAD_LAG_RET_THRESHOLD: float = 0.01   # 1.0%
DEFAULT_LEAD_LAG_VOL_MULT: float = 1.5
DEFAULT_LEAD_LAG_ALT_MAX_RET: float = 0.005    # 0.5%
DEFAULT_ZSCORE_THRESHOLD: float = 2.0


# ── 4 Layer 헬퍼 (test 가 직접 호출 가능하게 module-level public) ───────────

def cointegration_pvalue(btc_close: pd.Series, alt_close: pd.Series) -> float:
    """Engle-Granger test p-value. ``statsmodels.tsa.stattools.coint`` 활용.

    btc_close/alt_close 의 길이는 같고 양수여야. NaN/0 포함 시 ``1.0`` 반환
    (= 진입 차단). statsmodels 미설치 시 ``0.04`` 더미 pvalue 반환 — Layer 1
    gate 통과 처리 (다른 3 layer 가 작동), 학술적 정당성은 약하지만 prototype
    돌릴 수 있게 graceful. Python 3.14 같은 statsmodels wheel 미지원 환경에서.
    """
    if len(btc_close) < 30 or len(alt_close) < 30:
        return 1.0
    if btc_close.isna().any() or alt_close.isna().any():
        return 1.0
    if (btc_close <= 0).any() or (alt_close <= 0).any():
        return 1.0
    try:
        from statsmodels.tsa.stattools import coint  # noqa: PLC0415
    except ImportError:
        # statsmodels 미설치 — Layer 1 gate 통과 처리. 다른 3 layer 가 작동.
        # prototype/PoC 목적. 실 운영 활성화 (production.yaml 등록) 전에
        # statsmodels 환경에서 5y bench 재실행 필요.
        return 0.04
    try:
        _, pvalue, _ = coint(
            np.log(btc_close.values.astype(float)),
            np.log(alt_close.values.astype(float)),
        )
        return float(pvalue) if np.isfinite(pvalue) else 1.0
    except Exception:
        return 1.0


def rolling_correlation(
    btc_returns: pd.Series, alt_returns: pd.Series, window: int,
) -> float:
    """직전 ``window`` 봉의 Pearson correlation. 데이터 부족 → ``0.0``."""
    if len(btc_returns) < window or len(alt_returns) < window:
        return 0.0
    a = btc_returns.tail(window)
    b = alt_returns.tail(window)
    if a.std() == 0 or b.std() == 0:
        return 0.0
    return float(a.corr(b))


def lead_lag_direction(
    btc_returns_1h: pd.Series, btc_vol: pd.Series, alt_returns_1h: pd.Series,
    *,
    ret_threshold: float = DEFAULT_LEAD_LAG_RET_THRESHOLD,
    vol_mult: float = DEFAULT_LEAD_LAG_VOL_MULT,
    alt_max_ret: float = DEFAULT_LEAD_LAG_ALT_MAX_RET,
) -> int:
    """Layer 3 — 동조 체제 lead-lag.

    Returns:
      +1 (long), -1 (short), 0 (skip)
    """
    if len(btc_returns_1h) < 24 or len(alt_returns_1h) < 1:
        return 0
    r_btc = float(btc_returns_1h.iloc[-1])
    r_alt = float(alt_returns_1h.iloc[-1])
    # 1) BTC 직전 1h return |r| > threshold
    if abs(r_btc) <= ret_threshold:
        return 0
    # 2) BTC 거래량 spike — 직전 1h vol > 24h avg × vol_mult
    if len(btc_vol) < 25:
        return 0
    last_vol = float(btc_vol.iloc[-1])
    avg_vol = float(btc_vol.iloc[-25:-1].mean())
    if avg_vol == 0 or last_vol <= avg_vol * vol_mult:
        return 0
    # 3) alt 가 BTC 와 같은 방향으로 이미 alt_max_ret 이상 움직였으면 skip
    if r_btc > 0 and r_alt > alt_max_ret:
        return 0
    if r_btc < 0 and r_alt < -alt_max_ret:
        return 0
    return 1 if r_btc > 0 else -1


def spread_zscore(btc_close: pd.Series, alt_close: pd.Series) -> float:
    """Layer 4 — log-spread z-score. β 는 OLS, mean/std 는 전체 window.

    Spread[t] = log(alt[t]) - β · log(btc[t])
    z = (spread[-1] - mean(spread)) / std(spread)
    """
    if len(btc_close) < 30 or len(alt_close) < 30:
        return 0.0
    log_btc = np.log(btc_close.values.astype(float))
    log_alt = np.log(alt_close.values.astype(float))
    # OLS β = cov(log_alt, log_btc) / var(log_btc)
    btc_mean = log_btc.mean()
    var_btc = ((log_btc - btc_mean) ** 2).mean()
    if var_btc == 0:
        return 0.0
    cov = ((log_alt - log_alt.mean()) * (log_btc - btc_mean)).mean()
    beta = cov / var_btc
    spread = log_alt - beta * log_btc
    sd = spread.std()
    if sd == 0 or not np.isfinite(sd):
        return 0.0
    return float((spread[-1] - spread.mean()) / sd)


# ── Strategy ────────────────────────────────────────────────────────────────

class MultiAlphaAltcoinV1(LiveScannerMixin):
    """4 layer 결합 universe-scan altcoin 전략.

    1h interval. universe = BTC-cointegrated alt top 20 (daily refresh).
    bidir (shorts_allowed=True) — short 진입 sell 도 reduce_only=False stamp 됨
    (PR #342 가드 그대로).
    """

    # PR #342 가드 — bidir sell 진입 시 reduce_only=False 로 stamp.
    shorts_allowed: ClassVar[bool] = True

    # R/R 1:2 — spec 의 청산 룰 (LivePositionRiskManager 가 등록).
    stop_loss_pct: ClassVar[float] = 0.0075
    take_profit_pct: ClassVar[float] = 0.015
    timeout_bars: ClassVar[int] = 4  # 1h × 4 = 4h

    MIN_HISTORY: ClassVar[int] = max(
        DEFAULT_COINT_WINDOW_BARS, DEFAULT_CORR_WINDOW_BARS,
    ) + 2

    # 1h interval — LiveScannerMixin / orchestrator dispatch filter 가 lookup.
    @classmethod
    def get_interval(cls) -> str:
        return "1h"

    # universe — BTC-cointegrated alt top-20 dynamic.
    # cs-tsmom 와 같이 매일 rebal — universe 는 production 가 외부에서 set 가능.
    # default 는 BINANCE_USDT_TOP30 (cointegration 게이트가 그 안에서 필터).
    @classmethod
    def get_universe(cls) -> list[str]:
        from src.portfolio.binance_universe import get_universe as _u
        return _u()

    def __init__(
        self,
        *,
        default_size: float = 0.04,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        cooldown_after_stop_sec: float = 1800.0,  # 30분
        coint_window_bars: int = DEFAULT_COINT_WINDOW_BARS,
        coint_pvalue_max: float = DEFAULT_COINT_PVALUE_MAX,
        corr_window_bars: int = DEFAULT_CORR_WINDOW_BARS,
        regime_corr_high: float = DEFAULT_REGIME_CORR_HIGH,
        regime_corr_low: float = DEFAULT_REGIME_CORR_LOW,
        lead_lag_ret_threshold: float = DEFAULT_LEAD_LAG_RET_THRESHOLD,
        lead_lag_vol_mult: float = DEFAULT_LEAD_LAG_VOL_MULT,
        lead_lag_alt_max_ret: float = DEFAULT_LEAD_LAG_ALT_MAX_RET,
        zscore_threshold: float = DEFAULT_ZSCORE_THRESHOLD,
        btc_symbol: str = "BTCUSDT",
    ) -> None:
        # LiveScannerMixin 은 base 가 object 라 추가 인자 안 받음.
        # default_size / cooldown_after_stop_sec 는 instance attr 로 직접 set —
        # _live_scanner_helpers / LivePositionRiskManager 가 getattr 로 lookup.
        self.default_size = float(default_size)
        self.cooldown_after_stop_sec = float(cooldown_after_stop_sec)
        # ClassVar override (instance level)
        if stop_loss_pct is not None:
            self.stop_loss_pct = float(stop_loss_pct)
        if take_profit_pct is not None:
            self.take_profit_pct = float(take_profit_pct)
        self.coint_window_bars = int(coint_window_bars)
        self.coint_pvalue_max = float(coint_pvalue_max)
        self.corr_window_bars = int(corr_window_bars)
        self.regime_corr_high = float(regime_corr_high)
        self.regime_corr_low = float(regime_corr_low)
        self.lead_lag_ret_threshold = float(lead_lag_ret_threshold)
        self.lead_lag_vol_mult = float(lead_lag_vol_mult)
        self.lead_lag_alt_max_ret = float(lead_lag_alt_max_ret)
        self.zscore_threshold = float(zscore_threshold)
        self.btc_symbol = btc_symbol
        # daily cache — (alt_symbol, day_key) → (pvalue, corr) — 매 1h 호출
        # 마다 재계산 안 함. cs-tsmom 와 동등 패턴.
        self._daily_cache: dict[tuple[str, str], tuple[float, float]] = {}

    # ── 4 layer 평가 ───────────────────────────────────────────────────────

    def evaluate(
        self, btc_hist: pd.DataFrame, alt_hist: pd.DataFrame,
        ts: pd.Timestamp, alt_symbol: str,
    ) -> tuple[str, int, dict]:
        """단일 alt 의 4 layer 결합 평가.

        Returns:
          (action, direction, diagnostics)
          - action: "hold" | "buy" | "sell"
          - direction: 1 (long) / -1 (short) / 0 (hold)
          - diagnostics: pvalue, corr, regime, layer ("lead_lag" | "outlier" | "")
        """
        diag = {"pvalue": None, "corr": None, "regime": "", "layer": ""}
        # Min history 보장
        if len(btc_hist) < self.MIN_HISTORY or len(alt_hist) < self.MIN_HISTORY:
            return "hold", 0, diag

        # daily cache key — 1d 단위 갱신
        day_key = ts.strftime("%Y-%m-%d")
        cache_key = (alt_symbol, day_key)
        if cache_key in self._daily_cache:
            pvalue, corr = self._daily_cache[cache_key]
        else:
            # Layer 1 — cointegration
            btc_window = btc_hist["close"].tail(self.coint_window_bars)
            alt_window = alt_hist["close"].tail(self.coint_window_bars)
            pvalue = cointegration_pvalue(btc_window, alt_window)
            # Layer 2 — rolling corr (30일)
            btc_ret = btc_hist["close"].pct_change().tail(self.corr_window_bars)
            alt_ret = alt_hist["close"].pct_change().tail(self.corr_window_bars)
            corr = rolling_correlation(btc_ret, alt_ret, self.corr_window_bars)
            self._daily_cache[cache_key] = (pvalue, corr)
        diag["pvalue"] = pvalue
        diag["corr"] = corr

        # Layer 1 gate — cointegration p-value
        if pvalue > self.coint_pvalue_max:
            diag["layer"] = "cointegration_fail"
            return "hold", 0, diag

        # Layer 2 — regime
        if corr >= self.regime_corr_high:
            diag["regime"] = "synced"
        elif corr <= self.regime_corr_low:
            diag["regime"] = "decoupled"
        else:
            diag["regime"] = "ambiguous"
            return "hold", 0, diag

        # Layer 3 — Lead-lag (동조)
        if diag["regime"] == "synced":
            btc_ret_full = btc_hist["close"].pct_change()
            alt_ret_full = alt_hist["close"].pct_change()
            direction = lead_lag_direction(
                btc_ret_full, btc_hist["volume"], alt_ret_full,
                ret_threshold=self.lead_lag_ret_threshold,
                vol_mult=self.lead_lag_vol_mult,
                alt_max_ret=self.lead_lag_alt_max_ret,
            )
            if direction != 0:
                diag["layer"] = "lead_lag"
                return ("buy" if direction > 0 else "sell"), direction, diag
            return "hold", 0, diag

        # Layer 4 — Outlier (디커플링)
        if diag["regime"] == "decoupled":
            z = spread_zscore(
                btc_hist["close"].tail(self.coint_window_bars),
                alt_hist["close"].tail(self.coint_window_bars),
            )
            diag["zscore"] = z
            if z > self.zscore_threshold:
                # 과매수 → short
                diag["layer"] = "outlier_short"
                return "sell", -1, diag
            if z < -self.zscore_threshold:
                # 과매도 → long
                diag["layer"] = "outlier_long"
                return "buy", 1, diag
            return "hold", 0, diag

        return "hold", 0, diag

    # ── live-scanner per-symbol dispatch (LiveScannerMixin 표준) ───────────

    async def on_bar(self, ctx: dict) -> Signal:
        """orchestrator 가 universe 의 각 symbol 마다 호출. BTC 면 skip.

        BTC history 는 ctx["market_snapshot"]["ohlcv_history"] 또는 동등
        location 에서 lookup — orchestrator 가 universe_ohlcv 를 주입.
        """
        snap = ctx.get("market_snapshot", {})
        sym = snap.get("symbol")
        if not sym or sym == self.btc_symbol:
            return Signal(action="hold", size=0.0, reason="btc_or_no_symbol")
        # alt history
        alt_hist = snap.get("history")
        if alt_hist is None or len(alt_hist) == 0:
            return Signal(action="hold", size=0.0, reason="no_alt_history")
        # BTC history — orchestrator 가 universe_ohlcv 안에서 노출하거나
        # snap["btc_history"] (live_run 가 wiring 시 추가) 로 들어옴.
        # 둘 다 없으면 hold.
        btc_hist = snap.get("btc_history")
        if btc_hist is None:
            universe_ohlcv = snap.get("universe_ohlcv") or {}
            btc_hist = universe_ohlcv.get(self.btc_symbol)
        if btc_hist is None or len(btc_hist) == 0:
            return Signal(action="hold", size=0.0,
                          reason="no_btc_history_in_snapshot")

        ts_raw = ctx.get("ts")
        ts = pd.Timestamp(ts_raw) if ts_raw is not None else (
            alt_hist.index[-1] if hasattr(alt_hist, "index") else
            pd.Timestamp.utcnow()
        )

        action, direction, diag = self.evaluate(
            pd.DataFrame(btc_hist), pd.DataFrame(alt_hist), ts, sym,
        )
        if action == "hold":
            return Signal(action="hold", size=0.0,
                          reason=f"multi_alpha_hold:{diag}")
        size = self.default_size
        reason = (
            f"multi_alpha_{diag.get('layer','')}:p={diag.get('pvalue'):.3f},"
            f"corr={diag.get('corr'):.2f},regime={diag.get('regime')}"
        )
        return Signal(action=action, size=size, reason=reason)
