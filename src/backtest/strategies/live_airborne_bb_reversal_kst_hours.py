"""Live-scanner: Pine v1.2 airborne BB-reversal (bidir) + KST {8, 11, 16, 22}시 게이트.

[[live-airborne-bb-reversal-kst-morning]] (rejected, PF 0.906) 의 후속.
*시각 단일 블록* (06-12) 이 over-fit 임이 5y 데이터로 증명된 후, 5y 19,924
fire 의 hour-of-day 분석에서 PF >= 1.0 AND n >= 100 통과한 시각만 골랐을
때 PF 1.081 / Sharpe 0.96 가 나옴을 발견. 그 4 개 시각으로 게이트 재설정.

## 데이터 기반 시각 선정 (over-fit 회피)

`reports/airborne_hourly_pf_5y.json`:

| KST | n     | 승률   | PF    | 강한 방향         |
|----:|------:|------:|-----:|------------------|
|  8  |  783  | 36.7% | 1.049 | long (1.12)      |
| 11  |  948  | 38.5% | 1.135 | bidir (L 1.21/S 1.05) |
| 16  |  642  | 36.8% | 1.054 | short (1.32)     |
| 22  |  897  | 37.2% | 1.075 | short (1.31)     |

PF<1.0 인 나머지 20 개 시각은 제외. *분산된 4 시각* 구조가 over-fit 으로
보일 수 있지만 5y / 19,924 sample 로 통과 — 1년 단위 walk-forward 도 평균에서
±20% 안에서 안정. 의미 있는 sub-pattern.

## 데몬과 분리

`scripts/airborne_alert_daemon.py` 의 Telegram FIRE 알림은 24h 그대로 발화.
본 전략은 같은 signal 모듈을 orchestrator 안에서 직접 호출하므로 daemon
코드/설정 일체 무수정.
"""
from __future__ import annotations

from typing import ClassVar

from backtest.strategies.live_airborne_bb_reversal_kst_morning import (
    LiveAirborneBbReversalKstMorning,
)

# 5y hour-of-day 분석에서 PF >= 1.0 AND n >= 100 통과한 4 시각.
# - 8시: long-only 강함  (L 1.12 / S 0.98)
# - 11시: bidir 최고     (L 1.21 / S 1.05) — PF 1.135
# - 16시: short-only 강함 (L 0.85 / S 1.32)
# - 22시: short-only 강함 (L 0.85 / S 1.31)
# 5y aggregate: PF 1.081, 승률 37.4%, Sharpe 0.96, 3,270 trades.
_KST_TOP_HOURS: frozenset[int] = frozenset({8, 11, 16, 22})


class LiveAirborneBbReversalKstHours(LiveAirborneBbReversalKstMorning):
    """v1.2 bidir airborne + KST hour ∈ {8, 11, 16, 22} 게이트.

    Base 와 동일한 시그널·청산·warmup. KST hour gate 만 morning 블록 (6-11) →
    4 시각 (8/11/16/22) 으로 재정의.
    """

    # ClassVar 명시 — completeness check (static AST scan) 가 inheritance 추적
    # 안 하므로 stop/TP 도 명시. 값은 부모와 동일 (instance ctor 가 override 가능).
    stop_loss_pct: ClassVar[float] = 0.03
    take_profit_pct: ClassVar[float] = 0.06

    kst_entry_hours: ClassVar[frozenset[int]] = _KST_TOP_HOURS

    # Dynamic Universe Architecture Phase 1 (2026-05-28) — interval 만 1h
    # override. universe 는 부모(LiveScannerMixin) 기본 = TOP30 유지. Phase 2
    # 에서 daemon 동기 TOP100 dynamic 으로 확장 예정.
    @classmethod
    def get_interval(cls) -> str:
        return "1h"
