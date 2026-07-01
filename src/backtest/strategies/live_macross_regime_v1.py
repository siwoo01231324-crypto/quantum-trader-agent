"""Live-scanner: 1h SMA(25)/SMA(200) 크로스 + BTC SMA200 레짐 게이트 (bidir).

`scripts/ma_cross_alert_daemon.py` (Bitget MA-cross 알림 데몬) 의 ``detect_cross``
규약을 그대로 가져와 orchestrator 안에서 직접 평가하는 live-scanner 전략.
시그널은 daemon 과 동일 (1h 종가 SMA fast/slow 골든·데드 크로스), 그 위에
**BTC SMA200 레짐 게이트** 를 얹어 추세 정렬된 진입만 통과시킨다.

## 발화 규칙

매 *마지막 확정봉(closed bar)* 에서:

1. SMA(25)/SMA(200) 크로스 (``detect_cross`` 규약):
   - golden: 직전 fast<=slow 이고 현재 fast>slow → 롱 후보 (buy)
   - death : 직전 fast>=slow 이고 현재 fast<slow → 숏 후보 (sell)
2. **SMA200 기울기 필터** (ranging 감지 1차):
   - 골든→롱: SMA200[-1] > SMA200[-6] (상향 기울기) 일 때만 통과
   - 데드→숏: SMA200[-1] < SMA200[-6] (하향 기울기) 일 때만 통과
   - flat SMA200 위 크로스는 range 내 표류 → hold
3. **ADX(14) ≥ 20 필터** (ranging 감지 2차, Wilder 기준 ADX_TREND_DEFAULT):
   - ADX < 20 → ranging/choppy 환경 → hold
   - ADX 데이터 부족(warmup) → 보수적으로 진입 skip (hold)
4. **BTC SMA200 레짐 게이트** (엣지의 핵심):
   - golden→롱은 **BTC close ≥ BTC SMA200 (상승장)** 일 때만 통과
   - death →숏은 **BTC close <  BTC SMA200 (하락장)** 일 때만 통과
   - 역행 (골든+하락장 / 데드+상승장) → hold (진입 안 함)
   - BTC ohlcv 부재 → 보수적으로 진입 skip (hold)

BTC ohlcv 는 airborne 처럼 orchestrator 가
``market_snapshot["universe_ohlcv"]["BTCUSDT"]`` 로 박아준다.

## 청산

``stop_loss_pct = 0.02`` (−2% 가격) / ``take_profit_pct = 0.12`` (+12% 가격)
= 손익비 1:6. ``LivePositionRiskManager`` (live-scanner 공통) 가 24h 어느
시각이든 stop/TP 도달 시 즉시 청산 — 전략은 sell 청산 시그널을 내지 않는다.

## status: candidate (비활성)

2년 broad(top-65) 레짐필터+1:6 backtest 는 PF 1.22 / 거래당 기대값 +0.39%
(전 반기 양수) 로 양(+) 이나, 5년 BTC/ETH 는 PF 1.01 (본전, 2022·2025 손실
연도) 로 활성화 게이트(5y PF>1 AND 기대값>0) 미충족. breadth 의존 + 저승률
변동성으로 production 활성화 전 추가 검증 필요 → candidate. production.yaml
미등록, orchestrator register/returns export 미실시.
"""
from __future__ import annotations

from typing import ClassVar
from zoneinfo import ZoneInfo

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._indicators import ADX_TREND_DEFAULT, adx
from backtest.strategies._live_scanner_helpers import LiveScannerMixin

_KST = ZoneInfo("Asia/Seoul")

# detect_cross 규약 (scripts/ma_cross_alert_daemon.py 와 동일).
_FAST: int = 25
_SLOW: int = 200

_CROSS_GOLDEN = "golden"
_CROSS_DEATH = "death"

# BTC 레짐 게이트 — airborne 의 universe_ohlcv["BTCUSDT"] 패턴 재사용.
_BTC_SYMBOL: str = "BTCUSDT"
_BTC_SMA_PERIOD: int = 200

# SMA200 기울기 필터 — N봉 전 대비 현재 SMA200 방향 확인.
# 5봉(= 5시간) 전과 비교: 충분히 짧아 최근 추세를 반영하되
# 단봉 노이즈를 회피. 필요 시 생성자에서 오버라이드 가능.
_SLOPE_LOOKBACK: int = 5

# ADX 필터 — Wilder 기준 20 이상이면 추세 환경 (ADX_TREND_DEFAULT = 20.0).
_ADX_PERIOD: int = 14

# 리서치 confluence 필터 default (opt-in — 기본 OFF, 기존 bidir 동작 보존).
# 5y/2y/split-half OOS 검증으로 채택된 숏-집중 스택. 근거·수치는
# docs/specs/strategies/live-macross-regime-v1.md "리서치 confluence" 섹션.
#   - KST 시간게이트: **MA크로스 데드숏 자체 데이터 도출 (2026-07-01)**.
#     기존엔 airborne kst-hours {1,2,3,5,6,7,8,23} 를 차용했으나 — 그건 에어본 BB
#     신호(랜덤·sim착시) 기준이라 MA크로스와 무관(8h·23h 는 MA크로스선 PF<0.8 손실,
#     12·13·14·19h 좋은시각은 누락). R+DdPX 데드숏을 시각별로 분해해 **5y AND 2y
#     둘 다 PF>1 + n>=8** 인 시각만 채택 → {2,3,4,5,6,7,12,13,14,19,22}.
#     결과: 5y PF 1.31→1.61, 거래 361→515, split-half 전반1.43/후반1.81 통과
#     (에어본차용 대비 거래↑·PF↑). 분석 scripts/_macross_hour_analysis(연구).
#   - 과확장 회피: 진입가가 SMA200 에서 10% 초과 이탈 시 추격 금지
_KST_HOURS_DEFAULT: tuple[int, ...] = (2, 3, 4, 5, 6, 7, 12, 13, 14, 19, 22)
_OVEREXTENSION_MAX_DEFAULT: float = 0.10


def detect_cross(close: pd.Series, fast: int = _FAST, slow: int = _SLOW) -> str | None:
    """마지막 확정봉에서 SMA(fast) × SMA(slow) 크로스 판정.

    ``scripts/ma_cross_alert_daemon.py::detect_cross`` 와 동일 규약:
      - golden: 직전 fast<=slow 이고 현재 fast>slow  (상향 돌파)
      - death : 직전 fast>=slow 이고 현재 fast<slow  (하향 돌파)
    그 외 (추세 유지·MA 미확보) 는 ``None``.

    SMA 산출에 slow 봉, 직전 봉 비교에 +1 봉 → 최소 slow+2 봉 필요.
    """
    if fast >= slow:
        raise ValueError(f"fast({fast}) must be < slow({slow})")
    if close is None or len(close) < slow + 2:
        return None
    ma_fast = close.rolling(fast).mean()
    ma_slow = close.rolling(slow).mean()
    pf, ps = ma_fast.iloc[-2], ma_slow.iloc[-2]
    cf, cs = ma_fast.iloc[-1], ma_slow.iloc[-1]
    if pd.isna(pf) or pd.isna(ps) or pd.isna(cf) or pd.isna(cs):
        return None
    if pf <= ps and cf > cs:
        return _CROSS_GOLDEN
    if pf >= ps and cf < cs:
        return _CROSS_DEATH
    return None


def _slow_slope(close: pd.Series, slow: int = _SLOW,
                lookback: int = _SLOPE_LOOKBACK) -> str | None:
    """SMA(slow) 기울기 방향 판정.

    Returns:
      "up"   : SMA(slow)[-1] > SMA(slow)[-lookback-1]  (상향)
      "down" : SMA(slow)[-1] < SMA(slow)[-lookback-1]  (하향)
      "flat" : 동일 (flat range)
      None   : 데이터 부족 — 호출자는 보수적으로 진입 skip.
    """
    need = slow + lookback
    if close is None or len(close) < need:
        return None
    ma = close.rolling(slow).mean()
    current = ma.iloc[-1]
    past = ma.iloc[-1 - lookback]
    if pd.isna(current) or pd.isna(past):
        return None
    if current > past:
        return "up"
    if current < past:
        return "down"
    return "flat"


def _btc_regime(btc_hist: pd.DataFrame, *, sma_period: int = _BTC_SMA_PERIOD) -> str | None:
    """BTC 레짐 판정 — close vs SMA200.

    Returns:
      "up"   : BTC close ≥ SMA200 (상승장)
      "down" : BTC close <  SMA200 (하락장)
      None   : 데이터 부족 (warmup) — 호출자는 보수적으로 진입 skip.
    """
    if btc_hist is None or len(btc_hist) < sma_period:
        return None
    close = btc_hist["close"]
    sma = close.rolling(sma_period).mean()
    last_close = close.iloc[-1]
    last_sma = sma.iloc[-1]
    if pd.isna(last_close) or pd.isna(last_sma):
        return None
    return "up" if float(last_close) >= float(last_sma) else "down"


class LiveMacrossRegime(LiveScannerMixin):
    """1h SMA(25)/SMA(200) 크로스 + BTC SMA200 레짐 게이트 (bidir).

    골든크로스→롱은 BTC 상승장에서만, 데드크로스→숏은 BTC 하락장에서만 진입.
    역행 / BTC 데이터 부재 시 hold (보수적 skip). 청산은 LivePositionRiskManager
    가 stop_loss_pct(−2%) / take_profit_pct(+12%) = 1:6 로 처리.
    """

    strategy_id: ClassVar[str] = "live-macross-regime-v1"

    FAST: ClassVar[int] = _FAST
    SLOW: ClassVar[int] = _SLOW
    MIN_HISTORY: ClassVar[int] = _SLOW + 2  # 202

    stop_loss_pct: ClassVar[float] = 0.02
    take_profit_pct: ClassVar[float] = 0.12

    # time-stop 면제 — 추세추종 1:6 은 TP(+12%) 도달에 수일~30일 걸려, airborne
    # 용 global 1h time-stop(AIRBORNE_MAX_HOLD_SEC) 에 강제청산되면 엣지 붕괴.
    # `_register_exit_policies` 가 이 ClassVar 를 읽어 LivePositionRiskManager 에
    # per-strategy 오버라이드(None=면제) 등록. airborne 은 미선언이라 global 유지.
    max_hold_sec: ClassVar[float | None] = None

    # bidir — death 크로스 숏 진입. orchestrator 가 reduce_only=False stamp 필요
    # (airborne 와 동일 — 그렇지 않으면 Binance Futures -2022 reduceOnly reject).
    shorts_allowed: ClassVar[bool] = True

    def __init__(
        self,
        *,
        default_size: float = 0.05,
        fast: int | None = None,
        slow: int | None = None,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        trailing_stop_pct: float | None = None,
        # #380 — orchestrator 가 읽는 동시 보유 종목 상한 (전 전략 공통 옵션).
        max_concurrent_positions: int | None = None,
        # ── 리서치 confluence 필터 (opt-in, 기본 OFF) ──────────────────
        # 5y/2y/split-half OOS 검증으로 채택. 숏-집중 권장 구성:
        #   allow_long=False, kst_hour_gate=True, self_sma200_filter=True,
        #   overextension_max_pct=0.10. 근거 → spec "리서치 confluence" 섹션.
        allow_long: bool = True,
        allow_short: bool = True,
        kst_hour_gate: bool = False,
        kst_hours: tuple[int, ...] | None = None,
        self_sma200_filter: bool = False,
        overextension_max_pct: float | None = None,
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = float(default_size)

        self.fast = int(fast) if fast is not None else self.FAST
        self.slow = int(slow) if slow is not None else self.SLOW
        if self.fast >= self.slow:
            raise ValueError(f"fast({self.fast}) must be < slow({self.slow})")

        # confluence 필터 설정.
        self.allow_long = bool(allow_long)
        self.allow_short = bool(allow_short)
        if not (self.allow_long or self.allow_short):
            raise ValueError("allow_long·allow_short 둘 다 False 면 진입 불가")
        self.kst_hour_gate = bool(kst_hour_gate)
        self.kst_hours = frozenset(kst_hours if kst_hours is not None else _KST_HOURS_DEFAULT)
        self.self_sma200_filter = bool(self_sma200_filter)
        if overextension_max_pct is not None and not 0 < overextension_max_pct < 1.0:
            raise ValueError(
                f"overextension_max_pct must be in (0, 1), got {overextension_max_pct}")
        self.overextension_max_pct = overextension_max_pct

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

    # Dynamic Universe — airborne 과 동일: 24h 거래량 top-100 USDT-perp,
    # venue 자동 라우팅 (QTA_BROKER_VENUE=bitget → Bitget, else Binance).
    @classmethod
    def get_interval(cls) -> str:
        return "1h"

    @classmethod
    def get_universe(cls) -> list[str]:
        """검증 크립토 유니버스 (토큰화주식·상품 오염 제거) — 투매반등과 통일.

        2026-07-01: 동적 24h top-100(bitget/binance_top_dynamic)은 SOXL·SPCX·MSTR
        등 토큰화주식 + XAU/XAG 상품이 섞여 오염(라이브 실측 top-100 중 12종). 토큰화
        주식은 장마감 시 틱死 → 숏 timeout starvation(무한보유) 위험 + 5y 검증(클린
        30종목)과 유니버스 불일치. capitulation 과 동일 정적 크립토 allowlist 사용.
        """
        from src.portfolio.binance_universe import SWING_CRYPTO_UNIVERSE
        return list(SWING_CRYPTO_UNIVERSE[:100])

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        # ── SMA 크로스 판정 (마지막 확정봉) ─────────────────────────────
        cross = detect_cross(history["close"], self.fast, self.slow)
        if cross is None:
            return Signal(action="hold", size=0.0, reason="no_cross")

        # ── SMA200 기울기 필터 (ranging 감지 1차) ───────────────────────
        slope = _slow_slope(history["close"], self.slow, _SLOPE_LOOKBACK)
        if slope is None:
            return Signal(action="hold", size=0.0, reason="slope_warmup")
        if cross == _CROSS_GOLDEN and slope != "up":
            return Signal(action="hold", size=0.0,
                          reason=f"slope_gate:golden_slope={slope}")
        if cross == _CROSS_DEATH and slope != "down":
            return Signal(action="hold", size=0.0,
                          reason=f"slope_gate:death_slope={slope}")

        # ── ADX 필터 (ranging 감지 2차, Wilder ADX_TREND_DEFAULT=20) ────
        adx_val = adx(history, period=_ADX_PERIOD)
        if adx_val is None:
            return Signal(action="hold", size=0.0, reason="adx_warmup")
        if adx_val < ADX_TREND_DEFAULT:
            return Signal(action="hold", size=0.0,
                          reason=f"adx_gate:adx={adx_val:.1f}<{ADX_TREND_DEFAULT}")

        # ── BTC 레짐 게이트 (엣지의 핵심) ──────────────────────────────
        universe = snap.get("universe_ohlcv") if isinstance(snap, dict) else None
        btc_hist = universe.get(_BTC_SYMBOL) if isinstance(universe, dict) else None
        regime = _btc_regime(btc_hist)
        if regime is None:
            # BTC 데이터 부재 / warmup → 보수적으로 진입 skip.
            return Signal(action="hold", size=0.0,
                          reason="btc_regime_unavailable")

        c_now = float(history["close"].iloc[-1])
        side = "long" if cross == _CROSS_GOLDEN else "short"

        # ── 방향 허용 (숏-집중 구성 시 allow_long=False) ────────────────
        if side == "long" and not self.allow_long:
            return Signal(action="hold", size=0.0, reason="long_disabled")
        if side == "short" and not self.allow_short:
            return Signal(action="hold", size=0.0, reason="short_disabled")

        # ── BTC 레짐 게이트 (엣지의 핵심) ──────────────────────────────
        want_regime = "up" if side == "long" else "down"
        if regime != want_regime:
            return Signal(action="hold", size=0.0,
                          reason=f"regime_gate:{side}_in_{regime}market")

        # ── 리서치 confluence 필터 (opt-in) ────────────────────────────
        if self.kst_hour_gate:
            ts = history.index[-1]
            ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts
            hour_kst = ts.astimezone(_KST).hour
            if hour_kst not in self.kst_hours:
                return Signal(action="hold", size=0.0,
                              reason=f"kst_gate:hour={hour_kst}")

        if self.self_sma200_filter or self.overextension_max_pct is not None:
            sma200 = float(history["close"].rolling(self.slow).mean().iloc[-1])
            # 자기 SMA200 정렬: 롱은 가격이 자기 200선 위, 숏은 아래.
            if self.self_sma200_filter:
                aligned = (c_now > sma200) if side == "long" else (c_now < sma200)
                if not aligned:
                    return Signal(action="hold", size=0.0,
                                  reason=f"self_sma200:c={c_now:.6g},sma={sma200:.6g}")
            # 과확장 회피: 진입가가 자기 200선에서 너무 멀어졌으면 추격 금지.
            if self.overextension_max_pct is not None and c_now > 0:
                ext = (c_now - sma200) / c_now if side == "long" else (sma200 - c_now) / c_now
                if ext > self.overextension_max_pct:
                    return Signal(action="hold", size=0.0,
                                  reason=f"overextended:ext={ext:.3f}>{self.overextension_max_pct}")

        if side == "long":
            return Signal(action="buy", size=self.default_size,
                          reason=f"macross_golden_long:regime=up,adx={adx_val:.1f},c={c_now:.6g}")
        return Signal(action="sell", size=self.default_size,
                      reason=f"macross_death_short:regime=down,adx={adx_val:.1f},c={c_now:.6g}")
