"""Standalone airborne trader — daemon listener → broker order (orchestrator 통과 X).

기존 orchestrator (symbol-fixed bar dispatch) 는 daemon 의 dynamic universe FIRE
(매시간 다른 100 종목) 와 패러다임 불일치. 그래서 본 모듈은 *독립* trader
process 로 daemon log 를 listen 하고 자체 risk + 자체 broker client + 자체 WAL
로 발주한다. cs-tsmom 등 다른 자동매매 entity 와 완전 분리.

Components:
  - ``AirborneTraderConfig`` — pydantic-style settings (env + defaults)
  - ``AirborneTraderState`` — SQLite WAL (positions + fires_processed)
  - ``AirborneTraderRisk`` — KST hour gate + max position + daily loss + cooldown
  - ``AirborneTrader`` — async main loop (poll listener → decide → place order →
    monitor stop/TP)
  - ``scripts/airborne_trader_daemon.py`` — entry point + asyncio.run
"""
from __future__ import annotations

from .config import AirborneTraderConfig
from .state import AirborneTraderState, PositionRecord, FireDecision
from .risk import AirborneTraderRisk
from .trader import AirborneTrader

__all__ = [
    "AirborneTraderConfig",
    "AirborneTraderState",
    "PositionRecord",
    "FireDecision",
    "AirborneTraderRisk",
    "AirborneTrader",
]
