"""Live-scanner: Pine v1.2 airborne BB-reversal (bidir) + KST {1,2,3,6,7,8,23}시 게이트 (v3).

[[live-airborne-bb-reversal-kst-morning]] (rejected, PF 0.906) 의 후속.
*시각 단일 블록* (06-12) 이 over-fit 임이 5y 데이터로 증명된 후, v2 는
5y 분석 + 30d sim_cache 데이터 기반으로 {7,8,16,20,22} 로 설정됐으나
13일 1분봉 실측 분석에서 v3 로 재설계.

## v3 게이트 선정 근거 (2026-06-06)

`logs/airborne_fires/sim_cache_1m.jsonl` 의 13일 1분봉 실측 hour-of-day 분석:

새벽~아침 {1,2,3,6,7,8,23} 이 순손익/PF 최상위 (net +68%, PF 2.39).
옛 v2 의 16시 (PF 0.15) · 22시 (PF 0.61) 가 손실 누적 확인.

⚠️ CAVEAT: 13일 in-sample 선정 — 5y bench 미검증이며 5y hourly 분석은 다른
시각 ({8,11,16,22}) 을 선호. hour-of-day 알파가 윈도우마다 불안정 →
과적합 위험. 운영자 직접 판단으로 적용, 5y walk-forward 검증 전까지 모니터링 필요.

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

# v3 (2026-06-06): 13일 1분봉 실측(logs/airborne_fires/sim_cache_1m.jsonl)
# hour-of-day 분석에서 새벽~아침 {1,2,3,6,7,8,23} 이 순손익/PF 최상위
# (net +68%, PF 2.39), 옛 v2 의 16시(PF 0.15)·22시(PF 0.61)가 손실 누적.
# 23시 추가(2026-06-06): 23시 숏 PF 2.09(+7.5%), 롱은 손실(PF 0.69)이나 BTC trend filter 가 약세장 23시 롱을 차단해 숏만 잔존 → 운영자 추가. 동일 in-sample caveat 적용.
# ⚠️ CAVEAT: 13일 in-sample 선정 — 5y bench 미검증이며 5y hourly 분석은
# 다른 시각({8,11,16,22})을 선호. hour-of-day 알파가 윈도우마다 불안정 →
# 과적합 위험. 운영자 직접 판단으로 적용, 5y walk-forward 검증 전까지 모니터링 필요.
_KST_TOP_HOURS_V3: frozenset[int] = frozenset({1, 2, 3, 6, 7, 8, 23})

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
      1. KST entry hours = {1,2,3,6,7,8,23} (v3, 13일 1m 기반) — 새벽~아침+23시 시각.
         ⚠️ 13일 in-sample 선정, 5y 미검증, 과적합 위험.
      2. BTC trend filter — BTC 가 하락추세이면 LONG entry 자체 차단 (short 은
         그대로). 시장 동조 손실 (6/04 incident) 차단.

    BTC trend filter 는 default 활성 (instance kwarg ``btc_trend_filter_enabled``
    로 끄기 가능 — 옛 동작 byte-identical 회귀 가드용).
    """

    # ClassVar 명시 — completeness check (static AST scan) 가 inheritance 추적
    # 안 하므로 stop/TP 도 명시. 값은 부모와 동일 (instance ctor 가 override 가능).
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    kst_entry_hours: ClassVar[frozenset[int]] = _KST_TOP_HOURS_V3

    # 새 instance attr — BTC trend filter 토글. default True (활성).
    btc_trend_filter_enabled: bool = True

    def __init__(self, *args, btc_trend_filter_enabled: bool = True, **kwargs):
        super().__init__(*args, **kwargs)
        self.btc_trend_filter_enabled = bool(btc_trend_filter_enabled)
        # 봉마감 게이트 dedup — symbol → 마지막 진입한 마감봉 ts. 같은 마감봉엔
        # 한 번만 진입(데몬은 봉당 1회 알림 → 트레이더도 봉당 1회 진입, TP/SL
        # 청산 후 같은 봉 재진입 폭주 방지). live 에서만 채워짐.
        self._fired_bar_ts: dict[str, object] = {}

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

    def _bar_interval_sec(self) -> int:
        iv = str(self.get_interval())
        try:
            if iv.endswith("h"):
                return int(iv[:-1]) * 3600
            if iv.endswith("m"):
                return int(iv[:-1]) * 60
            if iv.endswith("d"):
                return int(iv[:-1]) * 86400
        except ValueError:
            pass
        return 3600

    def _bar_close_gate(self, ctx):
        """live 에서 형성 중인 마지막 봉을 마감봉으로 치환 (데몬과 정렬).

        데몬(텔레그램 알림)은 마감된 1h봉에서만 발화하는데, base 전략은
        history 의 iloc[-1](live 에선 진행 중 봉)을 평가해 봉 한가운데서도
        발화 → 알림에 없는 종목 매수 (2026-06-08 PIPPINUSDT 사고). live
        (ctx['live_run']=True) 이고 마지막 봉이 아직 형성 중이면 그 봉을 떼고
        마감봉 기준으로 평가하도록 ctx 를 치환한다.

        backtest(bench_live_scanner)는 on_bar 를 직접 호출하며 ctx 에
        ``live_run`` 이 없어 그대로 통과 → 기존 동작 byte-identical.

        반환: (평가용 ctx, 마감봉 ts). 마감봉 부족 시 (None, None).
        """
        if not (isinstance(ctx, dict) and ctx.get("live_run")):
            return ctx, None
        snap = ctx.get("market_snapshot")
        if not isinstance(snap, dict):
            return ctx, None
        hist = snap.get("history")
        if hist is None or len(hist) < 2:
            return ctx, None
        try:
            now = pd.Timestamp(ctx.get("ts"))
            last_open = pd.Timestamp(hist.index[-1])
        except (ValueError, TypeError):
            return ctx, None
        if now.tzinfo is None:
            now = now.tz_localize("UTC")
        if last_open.tzinfo is None:
            last_open = last_open.tz_localize("UTC")
        interval = pd.Timedelta(seconds=self._bar_interval_sec())
        if now >= last_open + interval:
            # 마지막 봉이 이미 마감 (종료시각 지남) → 그대로 평가.
            return ctx, last_open
        # 형성 중 → 마지막(미완성) 봉 제거, 마감봉으로 평가.
        trimmed = hist.iloc[:-1]
        if len(trimmed) < 2:
            return None, None
        new_snap = dict(snap)
        new_snap["history"] = trimmed
        try:
            new_snap["price"] = float(trimmed["close"].iloc[-1])
        except (ValueError, TypeError, KeyError):
            pass
        new_ctx = dict(ctx)
        new_ctx["market_snapshot"] = new_snap
        return new_ctx, pd.Timestamp(trimmed.index[-1])

    async def on_bar(self, ctx):
        """parent 의 시그널 평가 → buy intent 면 BTC trend filter 적용.

        BTC 가 하락추세 (200 EMA 아래 OR 24h drawdown < -1%) 면 long entry
        자체 차단. short entry 는 그대로 통과 (시장 하락에 short 는 정상 진입).

        BTC ohlcv 는 orchestrator 가 per_symbol_snap["universe_ohlcv"] 로 박아줌
        (2026-06-05 orchestrator 변경). 그 key 없으면 (legacy 환경 / backtest
        구버전) BTC trend check 생략 → 기존 동작 byte-identical.
        """
        # 2026-06-08 봉마감 게이트 (live) — 미완성 봉 진입 차단, 마감봉만 평가.
        gated, closed_ts = self._bar_close_gate(ctx)
        if gated is None:
            return Signal(action="hold", size=0.0, reason="await_bar_close")
        ctx = gated
        sig = await super().on_bar(ctx)
        # ── BTC trend filter — long entry 만 적용 (short/hold 통과) ──
        if (
            self.btc_trend_filter_enabled
            and sig is not None
            and getattr(sig, "action", None) == "buy"
        ):
            snap = ctx.get("market_snapshot") if isinstance(ctx, dict) else None
            universe = snap.get("universe_ohlcv") if isinstance(snap, dict) else None
            if isinstance(universe, dict):
                btc_hist = universe.get(_BTC_SYMBOL)
                if btc_hist is not None and len(btc_hist) > 0:
                    is_down, reason = _btc_is_downtrend(btc_hist)
                    if is_down:
                        # downtrend → long entry 차단.
                        existing_reason = getattr(sig, "reason", "") or ""
                        sig = Signal(
                            action="hold", size=0.0,
                            reason=f"btc_trend_filter_long_blocked:{reason} ({existing_reason})",
                        )
        # ── 마감봉 1회 진입 dedup (live) — 같은 마감봉엔 한 번만 진입 ──
        if closed_ts is not None and getattr(sig, "action", None) in ("buy", "sell"):
            snap = ctx.get("market_snapshot") if isinstance(ctx, dict) else None
            symbol = snap.get("symbol") if isinstance(snap, dict) else None
            if symbol is not None:
                if self._fired_bar_ts.get(symbol) == closed_ts:
                    return Signal(
                        action="hold", size=0.0,
                        reason=f"already_entered_bar:{closed_ts}",
                    )
                self._fired_bar_ts[symbol] = closed_ts
        return sig
