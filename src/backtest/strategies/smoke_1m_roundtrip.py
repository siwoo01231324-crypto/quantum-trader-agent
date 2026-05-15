"""Smoke 1-minute round-trip strategy — operator-only end-to-end verification.

목적: 대시보드 "거래 시작" 버튼을 눌렀을 때 신호 → 주문 → 체결 → WAL → 대시보드
재렌더링까지 전 구간이 실제로 작동하는지 빠르게 확인. 백테스트 알파 가치 0.

거동
- 매 bar `_holding[symbol]` 상태를 토글: 없으면 buy, 있으면 sell.
- 사이즈는 자본의 1% (`SMOKE_SIZE_FRACTION` env 로 조절 가능, default 0.01).
- 결과적으로 1분 봉이면 매 1분 round-trip 1건 발생.

활성화
- production.yaml 의 commented entry 를 uncomment 하면 활성.
- 또는 `SMOKE_TEST_ENABLED=1` env 가 설정돼 있을 때만 신호를 emit (yaml 등록은 됐어도
  env 가 없으면 hold 만 반환 → 운영 환경 휘발 방지).

⚠️ 운영 사용 금지 — 알파 없음, 매 분 거래 비용만 발생.
"""
from __future__ import annotations

import os
from typing import Any

import pandas as pd

from src.backtest.protocol import Bar, Signal


class Smoke1mRoundtrip:
    """Symbol-agnostic 1m round-trip smoke strategy.

    State: `_holding: dict[str, bool]` — per-symbol toggle. `on_bar` receives a
    single `Bar` plus context; we infer symbol from `context["symbol"]` when
    present, else from the bar's optional `.symbol` attribute, else fall back
    to a sentinel.
    """

    # No metalabeler hook, no win_probability — pure smoke.
    is_smoke: bool = True

    def __init__(self, symbol: str | None = None, size_fraction: float | None = None) -> None:
        # `symbol` mirrors momo_kis_v1 / momo_btc_v2 — config_loader passes it via
        # kwargs so two production.yaml entries (KIS + Binance) can share the same
        # class without state cross-talk. Not consumed for routing — orchestrator
        # dispatches bars per-registration — used only as a fallback context tag.
        self.symbol = symbol
        # Allow per-instance override; default reads env at call-time to allow
        # operators to flip without restarting (cheap, no perf concern).
        self._size_override = size_fraction
        self._holding: dict[str, bool] = {}

    def on_init(self, context: dict) -> None:
        # Reset on each run — fresh state.
        self._holding = {}

    def _enabled(self) -> bool:
        return os.environ.get("SMOKE_TEST_ENABLED", "").lower() in ("1", "true", "yes")

    def _size(self) -> float:
        if self._size_override is not None:
            return float(self._size_override)
        try:
            return float(os.environ.get("SMOKE_SIZE_FRACTION", "0.01"))
        except (TypeError, ValueError):
            return 0.01

    def _resolve_symbol(self, bar: Bar, context: dict) -> str:
        # context["symbol"] is set by the live loop when dispatching per-symbol.
        sym = context.get("symbol") if isinstance(context, dict) else None
        if sym:
            return str(sym)
        sym = getattr(bar, "symbol", None)
        if sym:
            return str(sym)
        if self.symbol:
            return self.symbol
        return "_default"

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        if not self._enabled():
            return Signal(action="hold", size=0.0, reason="smoke_disabled")
        symbol = self._resolve_symbol(bar, context)
        # #238 — orchestrator broadcast dispatch 차단. instance 의 symbol 과 다른
        # 종목 bar 면 hold (smoke-1m-roundtrip-kis 인스턴스가 BTCUSDT bar 받는 등).
        if self.symbol and symbol != self.symbol:
            return Signal(action="hold", size=0.0, reason="smoke_wrong_symbol")
        size = self._size()
        if self._holding.get(symbol, False):
            self._holding[symbol] = False
            return Signal(action="sell", size=size, reason=f"smoke_sell_{symbol}")
        self._holding[symbol] = True
        return Signal(action="buy", size=size, reason=f"smoke_buy_{symbol}")
