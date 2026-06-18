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
2. **BTC SMA200 레짐 게이트** (엣지의 핵심):
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

import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin

# detect_cross 규약 (scripts/ma_cross_alert_daemon.py 와 동일).
_FAST: int = 25
_SLOW: int = 200

_CROSS_GOLDEN = "golden"
_CROSS_DEATH = "death"

# BTC 레짐 게이트 — airborne 의 universe_ohlcv["BTCUSDT"] 패턴 재사용.
_BTC_SYMBOL: str = "BTCUSDT"
_BTC_SMA_PERIOD: int = 200


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
    ) -> None:
        if not 0 < default_size <= 1.0:
            raise ValueError(f"default_size must be in (0, 1], got {default_size}")
        self.default_size = float(default_size)

        self.fast = int(fast) if fast is not None else self.FAST
        self.slow = int(slow) if slow is not None else self.SLOW
        if self.fast >= self.slow:
            raise ValueError(f"fast({self.fast}) must be < slow({self.slow})")

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
        """24h 거래량 top-100 USDT-perp — venue 자동 라우팅 (airborne 미러)."""
        import os
        venue = os.environ.get("QTA_BROKER_VENUE", "").strip().lower()
        if venue == "bitget":
            from src.portfolio.bitget_top_dynamic import get_top_n_symbols
            return get_top_n_symbols(100)
        from src.portfolio.binance_top_dynamic import get_top_n_symbols
        return get_top_n_symbols(100)

    async def on_bar(self, ctx: object) -> Signal | None:
        snap = ctx["market_snapshot"]  # type: ignore[index]
        history: pd.DataFrame | None = snap.get("history")
        if history is None or len(history) < self.MIN_HISTORY:
            return Signal(action="hold", size=0.0, reason="warmup")

        # ── SMA 크로스 판정 (마지막 확정봉) ─────────────────────────────
        cross = detect_cross(history["close"], self.fast, self.slow)
        if cross is None:
            return Signal(action="hold", size=0.0, reason="no_cross")

        # ── BTC 레짐 게이트 (엣지의 핵심) ──────────────────────────────
        universe = snap.get("universe_ohlcv") if isinstance(snap, dict) else None
        btc_hist = universe.get(_BTC_SYMBOL) if isinstance(universe, dict) else None
        regime = _btc_regime(btc_hist)
        if regime is None:
            # BTC 데이터 부재 / warmup → 보수적으로 진입 skip.
            return Signal(action="hold", size=0.0,
                          reason="btc_regime_unavailable")

        c_now = float(history["close"].iloc[-1])
        if cross == _CROSS_GOLDEN:
            if regime != "up":
                return Signal(action="hold", size=0.0,
                              reason=f"regime_gate:golden_in_{regime}market")
            return Signal(action="buy", size=self.default_size,
                          reason=f"macross_golden_long:regime=up,c={c_now:.6g}")
        # death cross
        if regime != "down":
            return Signal(action="hold", size=0.0,
                          reason=f"regime_gate:death_in_{regime}market")
        return Signal(action="sell", size=self.default_size,
                      reason=f"macross_death_short:regime=down,c={c_now:.6g}")
