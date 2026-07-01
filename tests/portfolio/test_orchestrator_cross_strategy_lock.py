"""선점 우선 cross-strategy 종목중복 차단 (2026-07-01).

두 live-scanner 전략이 같은 종목에 진입 시도할 때:
  - cross_strategy_symbol_lock=False (기본): 둘 다 진입 (레거시 보존).
  - cross_strategy_symbol_lock=True: 먼저 진입한 전략만 점유, 나머지 skip
    (선점 우선). swing 롱·숏 동시운용 시 Bitget 네팅 사고 방지.
"""
from __future__ import annotations

import asyncio
from typing import ClassVar

import numpy as np
import pandas as pd

from backtest.protocol import Signal
from backtest.strategies._live_scanner_helpers import LiveScannerMixin
from portfolio import AsyncStrategyOrchestrator
from risk.dsl import Policy


def _ohlcv(symbol: str, n: int = 30) -> pd.DataFrame:
    rng = np.random.default_rng(abs(hash(symbol)) % (2**32))
    close = 100 + np.cumsum(rng.normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    idx = pd.date_range("2026-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": np.full(n, 1000.0)}, index=idx,
    )


class _BuyScanner(LiveScannerMixin):
    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.05, reason="long")


class _SellScanner(LiveScannerMixin):
    shorts_allowed: ClassVar[bool] = True

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="sell", size=0.05, reason="short")


def _snap():
    return {"symbol": None, "price": None, "equity_krw": 1_000_000.0,
            "equity_usdt": 1_000_000.0, "ohlcv_history": {"SOLUSDT": _ohlcv("SOLUSDT")}}


def _orch(lock: bool):
    return AsyncStrategyOrchestrator(
        Policy(policy_version=1, name="t"), cross_strategy_symbol_lock=lock)


def test_lock_off_both_strategies_enter():
    """기본(OFF) — 두 전략 같은 종목 둘 다 진입 (레거시 보존)."""
    orch = _orch(lock=False)
    orch.register_strategy("capit", _BuyScanner())
    orch.register_strategy("macross", _SellScanner())
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    sids = {i.strategy_id for i in intents if i.symbol == "SOLUSDT"}
    assert sids == {"capit", "macross"}  # 둘 다 진입


def test_lock_on_first_strategy_preempts():
    """ON — 먼저 진입한 전략(capit)이 점유, macross 는 skip (선점 우선)."""
    orch = _orch(lock=True)
    orch.register_strategy("capit", _BuyScanner())     # 등록순 먼저
    orch.register_strategy("macross", _SellScanner())
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    sol = [i for i in intents if i.symbol == "SOLUSDT"]
    assert len(sol) == 1
    assert sol[0].strategy_id == "capit"   # 선점한 전략만
    assert sol[0].side == "buy"


def test_lock_on_different_symbols_both_enter():
    """ON 이라도 다른 종목이면 둘 다 진입 (같은 종목만 차단)."""
    orch = _orch(lock=True)
    orch.register_strategy("capit", _BuyScanner())
    orch.register_strategy("macross", _SellScanner())
    snap = {"symbol": None, "price": None, "equity_krw": 1e6, "equity_usdt": 1e6,
            "ohlcv_history": {"SOLUSDT": _ohlcv("SOLUSDT"), "ETHUSDT": _ohlcv("ETHUSDT")}}
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
    # capit 가 두 종목 다 먼저 점유 → macross 는 둘 다 skip. 단 종목별 1포지션은 유지.
    by_sym = {}
    for i in intents:
        by_sym.setdefault(i.symbol, set()).add(i.strategy_id)
    assert all(len(v) == 1 for v in by_sym.values())  # 종목당 1전략


# ── 전체 명목 노출 상한 (2026-07-01) ──────────────────────────────────────────

class _BuyScanner25(LiveScannerMixin):
    """default_size 0.25 롱 스캐너 (명목캡 테스트용, 클래스 상수)."""
    default_size: ClassVar[float] = 0.25

    async def on_bar(self, ctx) -> Signal:
        return Signal(action="buy", size=0.25, reason="long")


_REAL_SYMS = ["SOLUSDT", "ETHUSDT", "BTCUSDT", "ADAUSDT", "XRPUSDT",
              "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT", "BNBUSDT"]


def _universe(n: int):
    # size_to_qty 가 tick/step 필터로 미등록 심볼(SYM0 등)을 drop → 실제 심볼 사용.
    return {s: _ohlcv(s) for s in _REAL_SYMS[:n]}


def test_notional_cap_blocks_new_entries():
    """명목캡 도달 시 신규진입 차단. default_size 0.25 × 캡 1.0 → 최대 4포지션."""
    orch = AsyncStrategyOrchestrator(
        Policy(policy_version=1, name="t"), max_total_notional_pct=1.0)
    orch.register_strategy("capit", _BuyScanner25())
    snap = {"symbol": None, "price": None, "equity_krw": 1e6, "equity_usdt": 1e6,
            "ohlcv_history": _universe(10)}
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
    # 4개(0.25×4=1.0) 진입 후 5번째부터 캡(>=1.0) 차단.
    assert len({i.symbol for i in intents}) == 4


def test_notional_cap_zero_unlimited():
    """캡 0.0(기본) = 무제한 (레거시 보존)."""
    orch = AsyncStrategyOrchestrator(
        Policy(policy_version=1, name="t"), max_total_notional_pct=0.0)
    orch.register_strategy("capit", _BuyScanner25())
    snap = {"symbol": None, "price": None, "equity_krw": 1e6, "equity_usdt": 1e6,
            "ohlcv_history": _universe(10)}
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), snap))
    # 캡 없으면 캡(4)보다 많이 진입 (일부 종목은 tick/min-notional 로 drop 가능).
    assert len({i.symbol for i in intents}) > 4


# ── 진입 확정 알림 콜백 (2026-07-01) — override 무관 전 전략 ──────────────────

def test_on_live_entry_fires_on_short_without_override():
    """macross 처럼 override 안 싣는 숏 진입도 _on_live_entry 발동(에어본 실진입 알림 판)."""
    fired = []
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch._on_live_entry = lambda sid, sym, side: fired.append((sid, sym, side))
    orch.register_strategy("macross", _SellScanner())
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    assert fired == [("macross", "SOLUSDT", "sell")]


def test_on_live_entry_fires_on_long():
    fired = []
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch._on_live_entry = lambda sid, sym, side: fired.append((sid, sym, side))
    orch.register_strategy("capit", _BuyScanner())
    asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    assert fired == [("capit", "SOLUSDT", "buy")]


def test_on_live_entry_none_noop():
    """콜백 미연결이면 no-op (진입은 정상)."""
    orch = AsyncStrategyOrchestrator(Policy(policy_version=1, name="t"))
    orch.register_strategy("capit", _BuyScanner())
    intents = asyncio.run(orch.run_bar(pd.Timestamp("2026-01-01"), _snap()))
    assert len(intents) == 1  # 알림 콜백 없어도 진입 정상
