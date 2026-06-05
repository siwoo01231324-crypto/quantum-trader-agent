"""Airborne SHORT-only Whitelist trader (live-airborne-short-whitelist-v1).

기존 ``src/live/airborne_trader`` 를 *수정하지 않고* 다음 두 게이트를 추가:
  1. side == "short" 만 통과
  2. fire.symbol 이 active whitelist 에 있어야 통과

구현: ``AirborneTraderRisk`` 를 상속한 ``AirborneShortWhitelistRisk`` 가
기존 게이트 위에 위 2개 게이트를 추가 (composition). Broker / state /
trader main loop 는 기존 코드 100% 재사용.

상세: ``docs/specs/strategies/live-airborne-short-whitelist-v1.md``
"""
from .risk import AirborneShortWhitelistRisk
from .whitelist_loader import (
    WhitelistConfig,
    WhitelistEntry,
    WhitelistValidationError,
    active_symbols,
    candidate_symbols,
    load_whitelist,
)

__all__ = [
    "AirborneShortWhitelistRisk",
    "WhitelistConfig",
    "WhitelistEntry",
    "WhitelistValidationError",
    "load_whitelist",
    "active_symbols",
    "candidate_symbols",
]
