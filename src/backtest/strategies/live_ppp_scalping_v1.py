"""Live-scanner: PPP 스캘핑 매매법 (1P/2P/3P) — EMA 배열 + 이평 지지 + QPP 크로스.

외부 유튜브 강의 "PPP 스캘핑 매매법" 을 공개 표준 기술적지표(EMA·Stochastic RSI)
로 독립 구현한 live-scanner 전략. PPP 체험판 지표(보호 상품)의 소스를 복제하지
않고, 그 지표가 사실상 Stochastic-RSI 계열(0~100 본선/시그널 크로스)임을 출력으로
확인해 ``QPP`` (Stochastic RSI) 로 재현했다. 같은 규약을 TradingView 우리 지표
``QPP Oscillator`` (D:\\ppp_transcribe\\qpp_oscillator.pine) 와 공유한다.

## 발화 규칙 (매 마지막 확정봉)

1. **1P 방향** — 장기 이평 배열: ``EMA(120) > EMA(240)`` 정배열 → 롱장,
   ``EMA(120) < EMA(240)`` 역배열 → 숏장.
2. **2P 셋업** — 직전 ``touch_lookback`` 봉 안에서 가격이 60/120/240 EMA 중
   하나를 지지(롱: low 가 이평 ±tol 닿고 close>이평) / 저항(숏: high 가 이평
   ±tol 닿고 close<이평) 한 적이 있는가.
3. **3P 트리거** — QPP(Stochastic RSI) 본선×시그널 크로스: 골든→롱, 데드→숏.

세 레이어가 정렬돼야 진입. (선택) ``btc_regime_gate`` 켜면 macross 처럼 BTC
SMA200 레짐과 방향 정렬도 요구.

## 청산

``LivePositionRiskManager`` 가 ClassVar ``stop_loss_pct`` / ``take_profit_pct``
(/ trailing) 로 처리 — 전략은 진입만 담당. 강의의 "다음 이평 목표 익절" 은
live-scanner 의 고정 pct 청산으로 단순화(기본 1.5%/3% = 1:2). 스캘핑이라
global 1h time-stop 과 충돌 안 함(짧은 보유 지향) → ``max_hold_sec`` 미선언.

## status: candidate (비활성)

5y 다중자산 게이트(PF>1 AND 거래당 기대값>0) 통과 전까지 candidate. 스캘핑은
현실 비용(슬립+fee ≥10bp) 부담이 커 활성화 전 5y bench 필수. production.yaml
미활성(commented), orchestrator register/returns export 미실시.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from signals.rsi import detect_divergence

# 기본 파라미터 (TradingView QPP Oscillator 규약과 동일).
_EMA_FAST, _EMA_MID, _EMA_SLOW = 60, 120, 240
_RSI_LEN, _STOCH_LEN, _SMOOTH_K, _SMOOTH_D = 14, 14, 3, 3

_CROSS_GOLDEN = "golden"
_CROSS_DEATH = "death"

_BTC_SYMBOL = "BTCUSDT"
_BTC_SMA_PERIOD = 200


def _ema(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(span=length, adjust=False, min_periods=length).mean()


def _rma(series: pd.Series, length: int) -> pd.Series:
    """Wilder RMA — TradingView ``ta.rsi`` 내부 평활과 동일."""
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def _rsi(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    dn = (-delta).clip(lower=0.0)
    rs = _rma(up, length) / _rma(dn, length)
    return 100.0 - 100.0 / (1.0 + rs)


def stoch_rsi(
    close: pd.Series,
    *,
    rsi_len: int = _RSI_LEN,
    stoch_len: int = _STOCH_LEN,
    smooth_k: int = _SMOOTH_K,
    smooth_d: int = _SMOOTH_D,
) -> tuple[pd.Series, pd.Series]:
    """QPP 본선/시그널 = Stochastic RSI (TradingView 규약 미러).

    rsi → stoch(rsi) → SMA(k)=본선 → SMA(본선, d)=시그널. 둘 다 0~100.
    """
    r = _rsi(close, rsi_len)
    ll = r.rolling(stoch_len).min()
    hh = r.rolling(stoch_len).max()
    rng = hh - ll
    # rng==0 (RSI 평탄) → 0으로 나눔 방지. float dtype 유지 위해 where 사용
    # (pd.NA 는 object dtype 화 → rolling().mean() DataError).
    st = 100.0 * (r - ll) / rng.where(rng != 0.0)
    main = st.rolling(smooth_k).mean()
    sig = main.rolling(smooth_d).mean()
    return main, sig


def qpp_cross(
    close: pd.Series,
    *,
    rsi_len: int = _RSI_LEN,
    stoch_len: int = _STOCH_LEN,
    smooth_k: int = _SMOOTH_K,
    smooth_d: int = _SMOOTH_D,
) -> str | None:
    """마지막 확정봉에서 QPP 본선×시그널 크로스 판정.

    golden: 직전 본선<=시그널, 현재 본선>시그널 / death: 그 반대. 그 외 None.
    """
    main, sig = stoch_rsi(
        close, rsi_len=rsi_len, stoch_len=stoch_len,
        smooth_k=smooth_k, smooth_d=smooth_d,
    )
    if len(main) < 2:
        return None
    pm, ps = main.iloc[-2], sig.iloc[-2]
    cm, cs = main.iloc[-1], sig.iloc[-1]
    if pd.isna(pm) or pd.isna(ps) or pd.isna(cm) or pd.isna(cs):
        return None
    if pm <= ps and cm > cs:
        return _CROSS_GOLDEN
    if pm >= ps and cm < cs:
        return _CROSS_DEATH
    return None


def _touched_recent(
    history: pd.DataFrame,
    emas: list[pd.Series],
    *,
    tol: float,
    lookback: int,
    side: str,
) -> bool:
    """직전 ``lookback`` 봉 안에서 이평 지지(long)/저항(short) 터치가 있었는가.

    long  : 봉 low 가 이평 ±tol 닿고 종가는 이평 위 (지지 유지)
    short : 봉 high 가 이평 ±tol 닿고 종가는 이평 아래 (저항)
    """
    n = len(history)
    lows = history["low"]
    highs = history["high"]
    closes = history["close"]
    start = max(0, n - lookback)
    for i in range(start, n):
        for e in emas:
            ev = e.iloc[i]
            if pd.isna(ev) or ev <= 0:
                continue
            if side == "long":
                if lows.iloc[i] <= ev * (1.0 + tol) and closes.iloc[i] > ev:
                    return True
            else:
                if highs.iloc[i] >= ev * (1.0 - tol) and closes.iloc[i] < ev:
                    return True
    return False


def _btc_regime(btc_hist: pd.DataFrame | None, *, sma_period: int = _BTC_SMA_PERIOD) -> str | None:
    """BTC 레짐 — close vs SMA200. up/down/None(warmup)."""
    if btc_hist is None or len(btc_hist) < sma_period:
        return None
    close = btc_hist["close"]
    sma = close.rolling(sma_period).mean()
    lc, ls = close.iloc[-1], sma.iloc[-1]
    if pd.isna(lc) or pd.isna(ls):
        return None
    return "up" if float(lc) >= float(ls) else "down"


class LivePppScalping(LiveScannerMixin):
    """PPP 스캘핑 매매법 (1P EMA 배열 + 2P 이평 지지/저항 + 3P QPP 크로스, bidir)."""

    strategy_id: ClassVar[str] = "live-ppp-scalping-v1"

    EMA_FAST: ClassVar[int] = _EMA_FAST
    EMA_MID: ClassVar[int] = _EMA_MID
    EMA_SLOW: ClassVar[int] = _EMA_SLOW
    MIN_HISTORY: ClassVar[int] = _EMA_SLOW + 2  # 242

    stop_loss_pct: ClassVar[float] = 0.015
    take_profit_pct: ClassVar[float] = 0.03
    shorts_allowed: ClassVar[bool] = True

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        ema_fast: int | None = None,
        ema_mid: int | None = None,
        ema_slow: int | None = None,
        rsi_len: int = _RSI_LEN,
        stoch_len: int = _STOCH_LEN,
        smooth_k: int = _SMOOTH_K,
        smooth_d: int = _SMOOTH_D,
        tol_pct: float = 0.0015,
        touch_lookback: int = 3,
        require_divergence: bool = False,
        divergence_lookback: int = 14,
        require_zone: bool = False,
        os_level: float = 25.0,
        ob_level: float = 75.0,
        allow_long: bool = True,
        allow_short: bool = True,
        btc_regime_gate: bool = False,
        sl_mode: str = "fixed",
        tp_mode: str = "fixed",
        sl_buffer_pct: float = 0.003,
        bb_period: int = 20,
        bb_std: float = 2.0,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        max_concurrent_positions: int | None = None,
        # A+B+C 진입 필터 passthrough (LiveScannerMixin).
        **filter_kwargs: object,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = float(default_size)

        self.ema_fast = int(ema_fast) if ema_fast is not None else self.EMA_FAST
        self.ema_mid = int(ema_mid) if ema_mid is not None else self.EMA_MID
        self.ema_slow = int(ema_slow) if ema_slow is not None else self.EMA_SLOW
        if not (self.ema_fast < self.ema_mid < self.ema_slow):
            raise ValueError(
                f"EMA 길이는 fast<mid<slow 이어야 함 "
                f"(got {self.ema_fast}/{self.ema_mid}/{self.ema_slow})"
            )
        self.min_history = self.ema_slow + 2

        self.rsi_len = int(rsi_len)
        self.stoch_len = int(stoch_len)
        self.smooth_k = int(smooth_k)
        self.smooth_d = int(smooth_d)

        if not 0 < tol_pct < 0.1:
            raise ValueError(f"tol_pct must be in (0, 0.1), got {tol_pct}")
        self.tol_pct = float(tol_pct)
        if touch_lookback < 1:
            raise ValueError(f"touch_lookback >= 1 required, got {touch_lookback}")
        self.touch_lookback = int(touch_lookback)

        # 4P 중첩 근거 (다이버전스). require_divergence=False 면 가산점(confidence)
        # 으로만, True 면 진입 필수 조건 (원 강의: "3P로도 진입, 4P 더하면 더 좋은 타점").
        self.require_divergence = bool(require_divergence)
        if divergence_lookback < 2:
            raise ValueError(f"divergence_lookback >= 2 required, got {divergence_lookback}")
        self.divergence_lookback = int(divergence_lookback)

        # OB/OS 구간 가중 — 과매도(<os)에서 골크 / 과매수(>ob)에서 데크일 때 신뢰도↑.
        # require_zone=True 면 구간 정렬을 진입 필수 조건으로.
        self.require_zone = bool(require_zone)
        if not 0 <= os_level < ob_level <= 100:
            raise ValueError(
                f"0 <= os_level < ob_level <= 100 필요 (got {os_level}/{ob_level})"
            )
        self.os_level = float(os_level)
        self.ob_level = float(ob_level)

        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)
        self.btc_regime_gate = bool(btc_regime_gate)

        # 동적 per-entry 청산 모드 (오케스트레이터 수정 0 — Signal override 경유).
        # 강의 원문 청산룰을 모드로 선택: 손절/익절을 진입 시점에 가격레벨로 계산해
        # stop_loss_pct_override / take_profit_pct_override 로 전달.
        #   sl_mode: "fixed"(정적 pct) | "ema"(지지/저항 이평 이탈)
        #   tp_mode: "fixed"(정적 pct) | "next_ema"(다음 이평 목표)
        #          | "bb_mid"(볼린저 중앙선) | "bb_upper"(볼린저 상단/숏은 하단)
        if sl_mode not in ("fixed", "ema"):
            raise ValueError(f"sl_mode must be 'fixed'/'ema', got {sl_mode!r}")
        if tp_mode not in ("fixed", "next_ema", "bb_mid", "bb_upper"):
            raise ValueError(
                f"tp_mode must be 'fixed'/'next_ema'/'bb_mid'/'bb_upper', got {tp_mode!r}"
            )
        self.sl_mode = sl_mode
        self.tp_mode = tp_mode
        if not 0 < sl_buffer_pct < 0.5:
            raise ValueError(f"sl_buffer_pct must be in (0, 0.5), got {sl_buffer_pct}")
        self.sl_buffer_pct = float(sl_buffer_pct)
        if bb_period < 2:
            raise ValueError(f"bb_period >= 2 required, got {bb_period}")
        self.bb_period = int(bb_period)
        if bb_std <= 0:
            raise ValueError(f"bb_std > 0 required, got {bb_std}")
        self.bb_std = float(bb_std)

        if stop_loss_pct is not None:
            self.stop_loss_pct = stop_loss_pct
        if take_profit_pct is not None:
            self.take_profit_pct = take_profit_pct
        if trailing_stop_pct is not None:
            self.trailing_stop_pct = trailing_stop_pct

        if max_concurrent_positions is not None:
            if int(max_concurrent_positions) < 1:
                raise ValueError(
                    f"max_concurrent_positions >= 1 required, got {max_concurrent_positions}"
                )
            self.max_concurrent_positions = int(max_concurrent_positions)

        # 레버리지 ROI 익절/손절 (선택) + A+B+C 필터 passthrough.
        roi_keys = ("take_profit_roi", "stop_loss_roi", "leverage")
        roi_kwargs = {k: filter_kwargs.pop(k, None) for k in roi_keys}
        if any(v is not None for v in roi_kwargs.values()):
            self._apply_roi_targets(**roi_kwargs)  # type: ignore[arg-type]
        if filter_kwargs:
            self._apply_filter_kwargs(**filter_kwargs)  # type: ignore[arg-type]

    @classmethod
    def get_interval(cls) -> str:
        return "15m"

    @classmethod
    def get_universe(cls) -> list[str]:
        """24h 거래량 top-100 USDT-perp — venue 자동 라우팅 (airborne/macross 미러)."""
        import os
        venue = os.environ.get("QTA_BROKER_VENUE", "").strip().lower()
        if venue == "bitget":
            from src.portfolio.bitget_top_dynamic import get_top_n_symbols
            return get_top_n_symbols(100)
        from src.portfolio.binance_top_dynamic import get_top_n_symbols
        return get_top_n_symbols(100)

    def _divergence(self, close: pd.Series, side: str) -> tuple[bool, str]:
        """4P 중첩 근거: RSI 다이버전스가 진입 방향과 정렬되는가.

        long → 상승(bullish) 다이버전스, short → 하락(bearish) 다이버전스.
        강의의 "페이크 거르기"(다이버전스가 트리거 방향과 같으면 신뢰)와 일치 —
        방향 불일치 다이버전스는 자동으로 가산점에서 제외된다.
        Returns (정렬여부, 라벨). 라벨은 reason 표기용.
        """
        rsi_s = _rsi(close, self.rsi_len)
        div = detect_divergence(close, rsi_s, self.divergence_lookback)
        latest = div.iloc[-1] if len(div) > 0 else None
        want = "bullish" if side == "long" else "bearish"
        return (latest == want), (str(latest) if latest is not None else "none")

    def _zone(self, close: pd.Series, side: str) -> tuple[bool, str]:
        """OB/OS 구간 정렬: long→과매도(<os)에서 골크, short→과매수(>ob)에서 데크.

        강의: 과매도에서 골크 / 과매수에서 데크일 때 신뢰도가 더 높다.
        Returns (정렬여부, QPP 본선값 라벨).
        """
        main, _ = stoch_rsi(
            close, rsi_len=self.rsi_len, stoch_len=self.stoch_len,
            smooth_k=self.smooth_k, smooth_d=self.smooth_d,
        )
        v = main.iloc[-1] if len(main) > 0 else None
        if v is None or pd.isna(v):
            return False, "na"
        v = float(v)
        ok = (v <= self.os_level) if side == "long" else (v >= self.ob_level)
        return ok, f"{v:.0f}"

    def _confidence(self, div_ok: bool, zone_ok: bool) -> float:
        """4P 다이버전스 + OB/OS 구간 가산점 → 0.5(base)~0.8."""
        return 0.5 + (0.15 if div_ok else 0.0) + (0.15 if zone_ok else 0.0)

    def _bollinger(self, close: pd.Series) -> tuple[float | None, float | None, float | None]:
        """볼린저밴드 마지막값 (mid, upper, lower). 데이터 부족 시 (None,None,None)."""
        mid = close.rolling(self.bb_period).mean().iloc[-1]
        sd = close.rolling(self.bb_period).std().iloc[-1]
        if pd.isna(mid) or pd.isna(sd):
            return None, None, None
        mid = float(mid); sd = float(sd)
        return mid, mid + self.bb_std * sd, mid - self.bb_std * sd

    def _exit_overrides(
        self, close: pd.Series, c_now: float, emas_last: list, side: str,
    ) -> tuple[float | None, float | None]:
        """진입 시점 per-entry 손절/익절 (% of entry price) — sl_mode/tp_mode 별.

        강의 청산룰을 Signal override 로 **오케스트레이터 수정 없이** 건다.
          sl_mode=ema      → 손절 = 지지(롱)/저항(숏) 이평 이탈
          tp_mode=next_ema → 익절 = 다음 이평 목표
          tp_mode=bb_mid   → 익절 = 볼린저 중앙선
          tp_mode=bb_upper → 익절 = 볼린저 상단(롱)/하단(숏)
        계산 불가/0.2% 미만/음수는 None → 정적 ClassVar pct 로 폴백.
        """
        if c_now <= 0:
            return None, None
        vals = [float(e) for e in emas_last if e is not None and not pd.isna(e)]
        below = [e for e in vals if e <= c_now]
        above = [e for e in vals if e >= c_now]

        stop_pct = None
        if self.sl_mode == "ema":
            if side == "long" and below:
                stop_pct = (c_now - max(below) * (1 - self.sl_buffer_pct)) / c_now
            elif side == "short" and above:
                stop_pct = (min(above) * (1 + self.sl_buffer_pct) - c_now) / c_now

        tp_pct = None
        if self.tp_mode == "next_ema":
            if side == "long" and above:
                tp_pct = (min(above) - c_now) / c_now
            elif side == "short" and below:
                tp_pct = (c_now - max(below)) / c_now
        elif self.tp_mode in ("bb_mid", "bb_upper"):
            mid, up, lo = self._bollinger(close)
            if mid is not None:
                tgt = mid if self.tp_mode == "bb_mid" else (up if side == "long" else lo)
                tp_pct = (tgt - c_now) / c_now if side == "long" else (c_now - tgt) / c_now

        stop_pct = stop_pct if (stop_pct is not None and stop_pct >= 0.002) else None
        tp_pct = tp_pct if (tp_pct is not None and tp_pct >= 0.002) else None
        return stop_pct, tp_pct

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.min_history:
            return Signal(action="hold", size=0.0, reason="warmup")

        # B(+A/C) 진입 필터 (LiveScannerMixin, 기본 anomaly 만 ON).
        filt = self._check_entry_filters(history)
        if filt is not None:
            return Signal(action="hold", size=0.0, reason=filt)

        close = history["close"]
        e_fast = _ema(close, self.ema_fast)
        e_mid = _ema(close, self.ema_mid)
        e_slow = _ema(close, self.ema_slow)
        emas = [e_fast, e_mid, e_slow]

        # 1P 방향
        m_now, s_now = e_mid.iloc[-1], e_slow.iloc[-1]
        if pd.isna(m_now) or pd.isna(s_now):
            return Signal(action="hold", size=0.0, reason="ema_warmup")
        bull = float(m_now) > float(s_now)
        bear = float(m_now) < float(s_now)

        # 3P 트리거 (QPP 크로스)
        cross = qpp_cross(
            close, rsi_len=self.rsi_len, stoch_len=self.stoch_len,
            smooth_k=self.smooth_k, smooth_d=self.smooth_d,
        )
        if cross is None:
            return Signal(action="hold", size=0.0, reason="no_qpp_cross")

        # (선택) BTC 레짐 게이트
        btc_regime = None
        if self.btc_regime_gate:
            universe = snap.get("universe_ohlcv") if isinstance(snap, dict) else None
            btc_hist = universe.get(_BTC_SYMBOL) if isinstance(universe, dict) else None
            btc_regime = _btc_regime(btc_hist)
            if btc_regime is None:
                return Signal(action="hold", size=0.0, reason="btc_regime_unavailable")

        c_now = float(close.iloc[-1])

        if cross == _CROSS_GOLDEN:
            if not self.allow_long:
                return Signal(action="hold", size=0.0, reason="long_disabled")
            if not bull:
                return Signal(action="hold", size=0.0, reason="regime_gate:golden_not_bull")
            if self.btc_regime_gate and btc_regime != "up":
                return Signal(action="hold", size=0.0,
                              reason=f"btc_regime_gate:golden_in_{btc_regime}")
            if not _touched_recent(history, emas, tol=self.tol_pct,
                                   lookback=self.touch_lookback, side="long"):
                return Signal(action="hold", size=0.0, reason="no_ema_support")
            # 4P 중첩 근거 (다이버전스) — 가산점 / require 시 필수.
            div_ok, div_lbl = self._divergence(close, "long")
            if self.require_divergence and not div_ok:
                return Signal(action="hold", size=0.0, reason=f"no_divergence:4P={div_lbl}")
            zone_ok, zone_lbl = self._zone(close, "long")
            if self.require_zone and not zone_ok:
                return Signal(action="hold", size=0.0, reason=f"no_zone:qpp={zone_lbl}")
            stop_ov, tp_ov = self._exit_overrides(
                close, c_now, [e_fast.iloc[-1], e_mid.iloc[-1], e_slow.iloc[-1]], "long")
            return Signal(action="buy", size=self.default_size,
                          confidence=self._confidence(div_ok, zone_ok),
                          stop_loss_pct_override=stop_ov,
                          take_profit_pct_override=tp_ov,
                          reason=f"ppp_long:1P=bull,2P=support,3P=golden,4P={div_lbl},zone={zone_lbl},c={c_now:.6g}")

        # death cross → 숏
        if not self.allow_short:
            return Signal(action="hold", size=0.0, reason="short_disabled")
        if not bear:
            return Signal(action="hold", size=0.0, reason="regime_gate:death_not_bear")
        if self.btc_regime_gate and btc_regime != "down":
            return Signal(action="hold", size=0.0,
                          reason=f"btc_regime_gate:death_in_{btc_regime}")
        if not _touched_recent(history, emas, tol=self.tol_pct,
                               lookback=self.touch_lookback, side="short"):
            return Signal(action="hold", size=0.0, reason="no_ema_resistance")
        div_ok, div_lbl = self._divergence(close, "short")
        if self.require_divergence and not div_ok:
            return Signal(action="hold", size=0.0, reason=f"no_divergence:4P={div_lbl}")
        zone_ok, zone_lbl = self._zone(close, "short")
        if self.require_zone and not zone_ok:
            return Signal(action="hold", size=0.0, reason=f"no_zone:qpp={zone_lbl}")
        stop_ov, tp_ov = self._exit_overrides(
            close, c_now, [e_fast.iloc[-1], e_mid.iloc[-1], e_slow.iloc[-1]], "short")
        return Signal(action="sell", size=self.default_size,
                      confidence=self._confidence(div_ok, zone_ok),
                      stop_loss_pct_override=stop_ov,
                      take_profit_pct_override=tp_ov,
                      reason=f"ppp_short:1P=bear,2P=resistance,3P=death,4P={div_lbl},zone={zone_lbl},c={c_now:.6g}")
