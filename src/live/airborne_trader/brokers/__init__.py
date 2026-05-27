"""airborne_trader 의 broker 구현체.

본 PR (#329 follow-up):
  - ``BinanceFuturesBroker`` — 실 Binance USDT-M Futures client wrap.

dry_run 용 ``DummyBroker`` 는 ``..trader`` 에 남김 (단위 테스트 + skeleton).
"""
from __future__ import annotations

from .binance_futures import BinanceFuturesBroker

__all__ = ["BinanceFuturesBroker"]
