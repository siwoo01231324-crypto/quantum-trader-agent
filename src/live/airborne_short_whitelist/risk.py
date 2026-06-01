"""AirborneShortWhitelistRisk — SHORT + whitelist 게이트 추가 (composition).

기존 ``AirborneTraderRisk.evaluate()`` 를 *수정하지 않고* 상속한다.
두 게이트가 모든 기존 게이트보다 *먼저* 평가되어 빠른 reject 보장.

평가 순서:
    Gate -2:  side filter      (fire.side == "short" 만 통과)
    Gate -1:  whitelist filter (fire.symbol in active_set 만 통과)
    Gate 0+:  super().evaluate()  — kill switch, KST hour, stale, max
              concurrent, dup-symbol, cooldown, daily loss limit

decision.reason 은 ``"short_only:side=<x>"`` / ``"not_whitelisted:<sym>"`` 형식.
audit log + dashboard 에서 종목별 reject 사유 분석 가능.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from live.airborne_fire_listener import FireRecord
from live.airborne_trader.config import AirborneTraderConfig
from live.airborne_trader.risk import AirborneTraderRisk, RiskDecision
from live.airborne_trader.state import AirborneTraderState


class AirborneShortWhitelistRisk(AirborneTraderRisk):
    """SHORT-only + symbol whitelist 게이트 추가.

    ``active_symbols`` 는 frozenset 또는 어떤 ``Iterable[str]`` 도 가능.
    재시작 없이 갱신은 미지원 — daemon 재시작 시 yaml 재로드.
    """

    def __init__(
        self,
        config: AirborneTraderConfig,
        state: AirborneTraderState,
        active_symbols: Iterable[str],
    ) -> None:
        super().__init__(config, state)
        self._active = frozenset(s.upper() for s in active_symbols)
        if not self._active:
            raise ValueError(
                "AirborneShortWhitelistRisk: active_symbols 비어 있음 — "
                "config/airborne_short_whitelist.yaml 의 status=active 확인"
            )

    @property
    def active_symbols(self) -> frozenset[str]:
        return self._active

    def evaluate(self, fire: FireRecord, *, now_utc: datetime) -> RiskDecision:
        # Gate -2: side filter
        if fire.side != "short":
            return RiskDecision(
                False, f"short_only:side={fire.side}",
            )

        # Gate -1: whitelist filter
        sym = fire.symbol.upper()
        if sym not in self._active:
            return RiskDecision(
                False, f"not_whitelisted:{sym}",
            )

        # 기존 게이트 0~6 통과 위임
        return super().evaluate(fire, now_utc=now_utc)
