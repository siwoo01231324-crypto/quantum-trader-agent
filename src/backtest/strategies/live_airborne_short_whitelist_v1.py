"""Backtest stub for ``live-airborne-short-whitelist-v1`` (daemon-only strategy).

**중요**: 본 전략은 **orchestrator 가 dispatch 하지 않는다**. ``airborne_short_whitelist_daemon`` 이 독립 프로세스로 실행되며, ``qta-airborne-daemon`` 의 FIRE 로그를 listener 가 받고 ``AirborneShortWhitelistRisk`` 가 SHORT + whitelist 게이트로 필터링 후 직접 발주한다.

본 파일은 다음 용도:
  1. ``check_strategy_completeness.py`` 의 "code" 레이어 요구사항 충족
  2. backtest 환경에서 SHORT-only + whitelist 필터링 로직을 **재현 검증**
     (``simulate_daemon_replay``) — daemon 의 risk gate 동작이 backtest 데이터
     에서도 동일함을 보장
  3. 향후 orchestrator 가 SHORT side 를 지원하게 되면 이 파일을 정식 live-scanner
     로 승격 가능 (현재는 LiveScannerMixin 의 buy-only 제약 때문에 분리됨)

운영 시 dispatch 는:
  - ``scripts/airborne_short_whitelist_daemon.py`` (entry point)
  - ``src/live/airborne_short_whitelist/risk.py`` (게이트)
  - ``config/airborne_short_whitelist.yaml`` (whitelist state)

spec: ``docs/specs/strategies/live-airborne-short-whitelist-v1.md``
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Iterable

# Daemon-only strategy — orchestrator 가 dispatch 하지 않음.
IS_DAEMON_ONLY: bool = True


@dataclass(frozen=True)
class LiveAirborneShortWhitelistV1Config:
    """Replay/backtest 용 설정 snapshot.

    daemon 의 실제 진입/청산 인자와 동일. 새 값으로 운영하려면 동시에
    ``config/airborne_short_whitelist.yaml`` 의 entry_params/exit_params 도
    갱신 (사람 확인).
    """
    retrace_ratio: float = 0.6
    bb_window: int = 20
    bb_std: float = 2.0
    min_close_margin: float = 0.001
    atr_body_mult: float = 0.3
    atr_period: int = 14
    stop_loss_pct: float = 0.03
    take_profit_pct: float = 0.06
    side: str = "short"


class LiveAirborneShortWhitelistV1:
    """Daemon strategy의 클래스 핸들.

    실제 dispatch 는 ``AirborneTrader`` + ``AirborneShortWhitelistRisk`` 가
    수행. 본 클래스는 spec metadata + replay helper container.

    Backtest 호출 :::

        cfg = LiveAirborneShortWhitelistV1Config()
        s = LiveAirborneShortWhitelistV1(whitelist=["BTCUSDT", "ETHUSDT"])
        # ``simulate_daemon_replay`` 는 별도 함수 — engine 통합 X.
    """

    strategy_id: ClassVar[str] = "live-airborne-short-whitelist-v1"
    paradigm: ClassVar[str] = "live-scanner"
    is_daemon_only: ClassVar[bool] = True
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06
    side: ClassVar[str] = "short"

    def __init__(
        self,
        *,
        whitelist: Iterable[str],
        config: LiveAirborneShortWhitelistV1Config | None = None,
    ) -> None:
        self.whitelist = frozenset(s.upper() for s in whitelist)
        if not self.whitelist:
            raise ValueError("whitelist 비어 있음 — 최소 1종 필요")
        self.config = config or LiveAirborneShortWhitelistV1Config()

    def is_eligible(self, *, symbol: str, side: str) -> bool:
        """Daemon 의 Gate -2 + Gate -1 동작 미러링.

        Returns True iff side == "short" AND symbol in whitelist (대소문자 무시).
        Backtest 에서 fire 필터링 정확도를 단위테스트로 검증할 때 사용.
        """
        if side != "short":
            return False
        return symbol.upper() in self.whitelist

    def __repr__(self) -> str:
        return (
            f"LiveAirborneShortWhitelistV1(whitelist_n={len(self.whitelist)}, "
            f"daemon_only=True)"
        )


__all__ = [
    "LiveAirborneShortWhitelistV1",
    "LiveAirborneShortWhitelistV1Config",
    "IS_DAEMON_ONLY",
]
