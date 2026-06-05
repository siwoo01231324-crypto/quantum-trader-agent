"""Live-scanner: Pine v1.2 airborne BB-reversal (bidir) + KST {8, 11, 16, 22}시 게이트.

[[live-airborne-bb-reversal-kst-morning]] (rejected, PF 0.906) 의 후속.
*시각 단일 블록* (06-12) 이 over-fit 임이 5y 데이터로 증명된 후, 5y 19,924
fire 의 hour-of-day 분석에서 PF >= 1.0 AND n >= 100 통과한 시각만 골랐을
때 PF 1.081 / Sharpe 0.96 가 나옴을 발견. 그 4 개 시각으로 게이트 재설정.

## 데이터 기반 시각 선정 (over-fit 회피)

`reports/airborne_hourly_pf_5y.json`:

| KST | n     | 승률   | PF    | 강한 방향         |
|----:|------:|------:|-----:|------------------|
|  8  |  783  | 36.7% | 1.049 | long (1.12)      |
| 11  |  948  | 38.5% | 1.135 | bidir (L 1.21/S 1.05) |
| 16  |  642  | 36.8% | 1.054 | short (1.32)     |
| 22  |  897  | 37.2% | 1.075 | short (1.31)     |

PF<1.0 인 나머지 20 개 시각은 제외. *분산된 4 시각* 구조가 over-fit 으로
보일 수 있지만 5y / 19,924 sample 로 통과 — 1년 단위 walk-forward 도 평균에서
±20% 안에서 안정. 의미 있는 sub-pattern.

## 데몬과 분리

`scripts/airborne_alert_daemon.py` 의 Telegram FIRE 알림은 24h 그대로 발화.
본 전략은 같은 signal 모듈을 orchestrator 안에서 직접 호출하므로 daemon
코드/설정 일체 무수정.
"""
from __future__ import annotations

from typing import ClassVar

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
)

# 2026-06-05 데이터 기반 재설계 — 5y `airborne_hourly_pf_5y.json` 의 PF>=1 시각
# 외에 30d 실거래 sim_cache 의 KST hour PF 도 같이 본 결과. 5y 옛 set:
# {8, 11, 16, 22} 의 PF=1.081 / Sharpe 0.96 / 3,270 trades 검증값을 기반으로,
# 최근 30d 시장에서 다음과 같이 재조정:
# - 11시 제거: 30d PF 0.692 (n=77) 손실시각. 5y 시점엔 PF 1.135 였으나 최근
#   바뀜. 게이트에서 빼서 손실 차단.
# - 07시 추가: 30d PF 4.66 (n=105) 압도. 가장 강한 시각. 5y bench 안 검증
#   됐지만 30d sample 통계적 신뢰 (n=105).
# - 20시 추가: 30d PF 2.32 (n=57). 8/16/22 보다 우월.
# 새 set: {7, 8, 16, 20, 22}. 5y bench 결과 (PF 1.081) 무효 — verdict 갱신.
_KST_TOP_HOURS_V2: frozenset[int] = frozenset({7, 8, 16, 20, 22})

# BTC trend filter (2026-06-05) — airborne 이 시장 전체 하락추세에서 LONG 잡는
# 사고 차단. 6/04 incident: bb-reversal 보유 14 LONG 종목이 새벽~오전에 전량
# -3% SL 동시 청산. 동일 stop_loss_pct + LONG 편향에서 시장 동조 손실. journal
# 분석의 "portfolio-level stop 또는 correlation-aware position sizing" 권고
# 반영 — 더 단순한 접근: BTC 하락추세 시 LONG entry 자체 차단.
_BTC_SYMBOL: str = "BTCUSDT"
_BTC_EMA_PERIOD_HOURS: int = 200      # 약 8일
_BTC_DOWNTREND_PCT: float = -0.01    # 직전 24h BTC < -1% 면 downtrend


def _btc_is_downtrend(
    btc_hist: pd.DataFrame,
    *,
    ema_period: int = _BTC_EMA_PERIOD_HOURS,
    drawdown_threshold: float = _BTC_DOWNTREND_PCT,
) -> tuple[bool, str]:
    """BTC 가 하락추세인지 — 두 조건 OR (둘 다 다른 timescale 가드).

    1. 200h EMA 아래 close (medium-term trend)
    2. 직전 24h % change < -1% (short-term momentum)

    데이터 부족 시 False (graceful — long block 안 함).

    Returns:
      (is_downtrend, reason)
    """
    if btc_hist is None or len(btc_hist) < ema_period:
        return False, "insufficient_btc_history"
    close = btc_hist["close"]
    last_close = float(close.iloc[-1])
    # 1) EMA200 cross
    ema = close.ewm(span=ema_period, adjust=False).mean()
    if last_close < float(ema.iloc[-1]):
        return True, f"btc_below_ema200 (close={last_close:.2f} < ema={float(ema.iloc[-1]):.2f})"
    # 2) 24h drawdown
    if len(close) >= 25:
        prev_24h = float(close.iloc[-25])
        ret_24h = (last_close - prev_24h) / prev_24h
        if ret_24h < drawdown_threshold:
            return True, (
                f"btc_24h_drawdown ({ret_24h*100:.2f}% < "
                f"{drawdown_threshold*100:.1f}%)"
            )
    return False, "btc_uptrend_or_neutral"


class LiveAirborneBbReversalKstHours(LiveAirborneBbReversalKstMorning):
    """v1.2 bidir airborne + KST hour gate + BTC trend filter (2026-06-05).

    Parent 와 동일한 시그널·청산·warmup. 두 가지 차이:
      1. KST entry hours = {7, 8, 16, 20, 22} (옛 {8, 11, 16, 22} 에서 11 제거,
         7/20 추가 — 30d 실거래 데이터 기반).
      2. BTC trend filter — BTC 가 하락추세이면 LONG entry 자체 차단 (short 은
         그대로). 시장 동조 손실 (6/04 incident) 차단.

    BTC trend filter 는 default 활성 (instance kwarg ``btc_trend_filter_enabled``
    로 끄기 가능 — 옛 동작 byte-identical 회귀 가드용).
    """

    # ClassVar 명시 — completeness check (static AST scan) 가 inheritance 추적
    # 안 하므로 stop/TP 도 명시. 값은 부모와 동일 (instance ctor 가 override 가능).
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    kst_entry_hours: ClassVar[frozenset[int]] = _KST_TOP_HOURS_V2

    # 새 instance attr — BTC trend filter 토글. default True (활성).
    btc_trend_filter_enabled: bool = True

    def __init__(self, *args, btc_trend_filter_enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.btc_trend_filter_enabled = bool(btc_trend_filter_enabled)

    # Dynamic Universe Architecture (2026-05-28):
    # - Phase 1: interval = "1h" (이전 1d → 사실상 무용지물이던 문제 해결)
    # - Phase 2: universe = daemon top-100 dynamic (24h 거래량 기반).
    #   binance_top_dynamic.get_top_n_symbols(100) — 5분 캐시 + fetch 실패 시
    #   정적 BINANCE_USDT_TOP30 fallback (graceful, 매매 안 멈춤).
    @classmethod
    def get_interval(cls) -> str:
        return "1h"

    @classmethod
    def get_universe(cls) -> list[str]:
        """24h 거래량 top-100 USDT-perp — venue 자동 라우팅.

        2026-06-05 — Binance / Bitget 동시 운영. env ``QTA_BROKER_VENUE`` 가
        ``bitget`` 이면 Bitget 거래량 기준 (Bitget 미상장 종목 사전 제외 →
        ``status=400`` 폭주 + API rate-limit 낭비 차단). 그 외 (기본/binance)
        는 기존 Binance 동작 byte-identical.
        """
        import os
        venue = os.environ.get("QTA_BROKER_VENUE", "").strip().lower()
        if venue == "bitget":
            from src.portfolio.bitget_top_dynamic import get_top_n_symbols
            return get_top_n_symbols(100)
        from src.portfolio.binance_top_dynamic import get_top_n_symbols
        return get_top_n_symbols(100)

    async def on_bar(self, ctx):
        """parent 의 시그널 평가 → buy intent 면 BTC trend filter 적용.

        BTC 가 하락추세 (200 EMA 아래 OR 24h drawdown < -1%) 면 long entry
        자체 차단. short entry 는 그대로 통과 (시장 하락에 short 는 정상 진입).

        BTC ohlcv 는 orchestrator 가 per_symbol_snap["universe_ohlcv"] 로 박아줌
        (2026-06-05 orchestrator 변경). 그 key 없으면 (legacy 환경 / backtest
        구버전) BTC trend check 생략 → 기존 동작 byte-identical.
        """
        sig = await super().on_bar(ctx)
        # short entry / hold 는 통과 — long entry 만 BTC trend gate 적용
        if not self.btc_trend_filter_enabled:
            return sig
        if sig is None:
            return sig
        action = getattr(sig, "action", None)
        if action != "buy":
            return sig
        # BTC history lookup
        snap = ctx.get("market_snapshot") if isinstance(ctx, dict) else None
        if not isinstance(snap, dict):
            return sig
        universe = snap.get("universe_ohlcv")
        if not isinstance(universe, dict):
            return sig
        btc_hist = universe.get(_BTC_SYMBOL)
        if btc_hist is None or len(btc_hist) == 0:
            return sig
        is_down, reason = _btc_is_downtrend(btc_hist)
        if not is_down:
            return sig
        # downtrend 면 long entry 차단. reason 에 BTC trend 정보 명시.
        existing_reason = getattr(sig, "reason", "") or ""
        return Signal(
            action="hold", size=0.0,
            reason=f"btc_trend_filter_long_blocked:{reason} ({existing_reason})",
        )
