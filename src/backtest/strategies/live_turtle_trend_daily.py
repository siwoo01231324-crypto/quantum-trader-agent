"""Turtle trend-following — Binance top-N daily bars, LONG only (CANDIDATE).

리서치(2026-06-24, [[project_turtle_daily_candidate]]) 결과 우리 신호 검증 전체에서
유일하게 5y·random·생존편향·포트폴리오 전부 통과한 전략. 영상(슈퍼트레이더
sTSvaQ9336M)의 리처드 데니스 터틀 시스템을 크립토 일봉에 이식.

규칙 (universe-scan, on_bar 상태머신 — breakout_donchian 미러):
  진입: close > rolling_max(high, entry_window=20).shift(1)  (20봉 신고가 돌파)
        AND close > SMA(200)  (200MA 추세필터 — 상승추세만 롱)
  청산: close < rolling_min(low, exit_window=10).shift(1)  (10봉 신저가 트레일링)
        OR  close <= entry - atr_mult(2.0)*ATR(20)_at_entry  (2×ATR 하드스톱)
  롱 전용 (crypto 숏은 5y 구조적 손실 — [[project_research_signal_screen_summary]]).
  top_n 슬롯 동시보유, 일봉 리밸 (UTC 00:00 경계).

5y 백테스트 (top-24, 비용 0.16%): 롱 PF 2.33 / 기대값 +8.66% / 매년 robust.
포트폴리오(위험1%/거래·동시6·복리): CAGR +24.8% / MDD 22.7% / Sharpe 0.68.
생존편향 breakeven 사망률 16.6% (200MA위+신고가 진입이 죽는코인 구조적 배제).
status: candidate — production.yaml 비활성. 실거래 모니터링 후 활성화 판단.
"""
from __future__ import annotations

from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import Signal
import signals


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


class LiveTurtleTrendDaily:
    """터틀 추세추종 — Binance 일봉, 롱 전용, universe-scan.

    Tunable (param grid <= 3 axes):
      - entry_window: 진입 Donchian lookback (default 20)
      - exit_window: 청산 Donchian lookback (default 10)
      - atr_mult: ATR 손절 배수 (default 2.0)

    Symbol: "TURTLE_BASKET" (포트폴리오 레벨 single Signal).
    Instrument type: crypto. Bar boundary: UTC 00:00 일봉.
    """

    required_factors: ClassVar[list[str]] = ["donchian", "atr"]

    SYMBOL = "TURTLE_BASKET"
    MIN_HISTORY = 200  # SMA200 warmup
    is_universe_scan: ClassVar[bool] = True

    def __init__(
        self,
        *,
        entry_window: int = 20,
        exit_window: int = 10,
        atr_window: int = 20,
        atr_mult: float = 2.0,
        ma_window: int = 200,
        top_n: int = 6,
        risk_per_trade: float = 0.01,
        universe_codes: list[str] | None = None,
    ) -> None:
        self.entry_window = entry_window
        self.exit_window = exit_window
        self.atr_window = atr_window
        self.atr_mult = atr_mult
        self.ma_window = ma_window
        self.top_n = top_n
        self.risk_per_trade = risk_per_trade
        self._universe_codes: list[str] | None = universe_codes
        # 보유 포지션: code -> {"entry": float, "stop": float}
        self._positions: dict[str, dict[str, float]] = {}

    # ── bar boundary: 일봉 (UTC 00:00) ──────────────────────────────────────
    def _is_my_bar_boundary(self, ts: pd.Timestamp) -> bool:
        try:
            t = ts.tz_convert("UTC") if ts.tzinfo is not None else ts
        except (TypeError, AttributeError):
            t = ts
        return t.hour == 0 and t.minute == 0

    # ── 진입 후보: 20봉 신고가 돌파 AND close > SMA200 ──────────────────────
    def _rank_candidates(
        self, ohlcv_history: dict[str, pd.DataFrame],
    ) -> list[tuple[str, float, float, float]]:
        """Return (code, strength, entry_close, atr_at_entry) — 진짜 돌파+추세만."""
        out: list[tuple[str, float, float, float]] = []
        for code, hist in ohlcv_history.items():
            if hist is None or len(hist) < max(self.entry_window + 1, self.ma_window):
                continue
            high, low, close = hist["high"], hist["low"], hist["close"]
            donchian = signals.compute("donchian", high=high, low=low, window=self.entry_window)
            atr_series = signals.compute("atr", high=high, low=low, close=close, window=self.atr_window)
            upper = donchian["upper"].shift(1)
            atr_prev = atr_series.shift(1)
            sma = close.rolling(self.ma_window).mean()

            last_close = float(close.iloc[-1])
            last_upper = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else float("inf")
            last_atr = float(atr_prev.iloc[-1]) if not pd.isna(atr_prev.iloc[-1]) else 0.0
            last_sma = float(sma.iloc[-1]) if not pd.isna(sma.iloc[-1]) else float("inf")

            # 진입: 20봉 신고가 돌파 + 200MA 위 (상승추세) + ATR 유효
            if last_close > last_upper and last_close > last_sma and last_atr > 0:
                strength = (last_close - last_upper) / last_atr
                out.append((code, strength, last_close, last_atr))
        out.sort(key=lambda x: x[1], reverse=True)
        return out

    # ── 청산: 10봉 신저가 이탈 OR 2×ATR 하드스톱 ─────────────────────────────
    def _check_exits(self, ohlcv_history: dict[str, pd.DataFrame]) -> list[str]:
        to_exit: list[str] = []
        for code in list(self._positions):
            hist = ohlcv_history.get(code)
            if hist is None or len(hist) < self.exit_window + 1:
                continue
            low, close = hist["low"], hist["close"]
            donchian_exit = signals.compute("donchian", high=hist["high"], low=low, window=self.exit_window)
            lower_exit = donchian_exit["lower"].shift(1)
            last_close = float(close.iloc[-1])
            last_low = float(low.iloc[-1])
            last_lower = float(lower_exit.iloc[-1]) if not pd.isna(lower_exit.iloc[-1]) else float("-inf")
            stop = self._positions[code]["stop"]
            # 트레일링(10봉 저가 종가이탈) 또는 2×ATR 하드스톱(저가 터치)
            if last_close < last_lower or last_low <= stop:
                to_exit.append(code)
        return to_exit

    async def on_bar(self, ctx: object) -> Signal | None:
        ts = ctx["ts"]
        if not self._is_my_bar_boundary(ts):
            return Signal(action="hold", size=0.0, reason="not my bar")

        snap = ctx["market_snapshot"]
        ohlcv_history: dict[str, pd.DataFrame] = snap.get("ohlcv_history", {})
        if self._universe_codes is not None:
            ohlcv_history = {k: v for k, v in ohlcv_history.items() if k in self._universe_codes}
        if not ohlcv_history:
            return Signal(action="hold", size=0.0, reason="insufficient history")

        # 1. 청산 처리
        for code in self._check_exits(ohlcv_history):
            self._positions.pop(code, None)

        # 2. 빈 슬롯에 신규 돌파 진입 (entry price + 2×ATR stop 기록)
        open_slots = self.top_n - len(self._positions)
        if open_slots > 0:
            for code, _strength, entry_close, atr_at in self._rank_candidates(ohlcv_history):
                if open_slots <= 0:
                    break
                if code not in self._positions:
                    self._positions[code] = {
                        "entry": entry_close,
                        "stop": entry_close - self.atr_mult * atr_at,
                    }
                    open_slots -= 1

        if not self._positions:
            return Signal(action="hold", size=0.0, reason="no active positions")

        # 3. 사이징: 거래당 위험 risk_per_trade → 포지션수 비례 (동등가중 슬롯).
        #    명목 합은 orchestrator 가 size 로 스케일. 보유 슬롯 수 / top_n.
        size = _clip01(len(self._positions) / self.top_n)

        # confidence: 평균 돌파강도 (clip)
        conf_vals = []
        for code in self._positions:
            hist = ohlcv_history.get(code)
            if hist is None or len(hist) < self.entry_window + 1:
                continue
            donchian = signals.compute("donchian", high=hist["high"], low=hist["low"], window=self.entry_window)
            atr_series = signals.compute("atr", high=hist["high"], low=hist["low"], close=hist["close"], window=self.atr_window)
            upper = donchian["upper"].shift(1)
            atr_prev = atr_series.shift(1)
            lc = float(hist["close"].iloc[-1])
            lu = float(upper.iloc[-1]) if not pd.isna(upper.iloc[-1]) else lc
            la = float(atr_prev.iloc[-1]) if not pd.isna(atr_prev.iloc[-1]) else 1.0
            if la > 0:
                conf_vals.append(_clip01(abs(lc - lu) / la))
        confidence = float(np.mean(conf_vals)) if conf_vals else 0.0

        # expected return: 60일 바스켓 평균 수익률
        rets = []
        for code in self._positions:
            hist = ohlcv_history.get(code)
            if hist is not None and len(hist) >= 60:
                rets.append(hist["close"].pct_change().dropna().iloc[-60:])
        expected_return = (
            float(pd.concat(rets, axis=1).mean(axis=1).dropna().mean()) if rets else 0.0
        )

        return Signal(
            action="buy",
            size=size,
            reason=f"turtle breakout basket n={len(self._positions)}",
            confidence=confidence,
            expected_return=expected_return,
        )
