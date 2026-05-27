"""AirborneTraderRisk — fire-by-fire risk gating.

Inputs:
  - FireRecord (from listener)
  - AirborneTraderConfig
  - AirborneTraderState (open positions, last stop ts, today realized PnL)
  - now (UTC) — caller injects (testability)

Output:
  - RiskDecision: (ok: bool, reason: str)
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from live.airborne_fire_listener import FireRecord

from .config import AirborneTraderConfig
from .state import AirborneTraderState

_KST = ZoneInfo("Asia/Seoul")


@dataclass(frozen=True)
class RiskDecision:
    ok: bool
    reason: str


class AirborneTraderRisk:
    """주문 전 단일 게이트. 모든 거부 사유는 audit 용 ``reason`` 으로 명확화."""

    def __init__(
        self,
        config: AirborneTraderConfig,
        state: AirborneTraderState,
    ) -> None:
        self.config = config
        self.state = state

    def evaluate(self, fire: FireRecord, *, now_utc: datetime) -> RiskDecision:
        """fire 한 건에 대해 모든 risk gate 적용."""
        if now_utc.tzinfo is None:
            raise ValueError("now_utc must be tz-aware")
        now_utc = now_utc.astimezone(timezone.utc)

        # 1. KST hour gate {8, 11, 16, 22}
        kst_hour = fire.ts.astimezone(_KST).hour
        if kst_hour not in self.config.kst_entry_hours:
            return RiskDecision(
                False,
                f"kst_hour={kst_hour} not in {sorted(self.config.kst_entry_hours)}",
            )

        # 2. Stale fire (5분 이상 지난 fire 무시)
        age_seconds = (now_utc - fire.ts).total_seconds()
        if age_seconds > self.config.fire_max_age_seconds:
            return RiskDecision(
                False,
                f"stale_fire age={age_seconds:.1f}s > {self.config.fire_max_age_seconds}s",
            )
        if age_seconds < 0:
            return RiskDecision(
                False,
                f"future_fire age={age_seconds:.1f}s (clock skew?)",
            )

        # 3. Max concurrent positions
        open_count = self.state.count_open()
        if open_count >= self.config.max_concurrent_positions:
            return RiskDecision(
                False,
                f"max_concurrent={open_count}>={self.config.max_concurrent_positions}",
            )

        # 4. Same-symbol open position 차단 (한 종목 1 포지션)
        existing = self.state.find_open_by_symbol(fire.symbol)
        if existing is not None:
            return RiskDecision(
                False,
                f"already_open symbol={fire.symbol} pos_id={existing.id} side={existing.side}",
            )

        # 5. Cooldown after stop_loss
        last_stop_iso = self.state.last_stop_close_ts(fire.symbol)
        if last_stop_iso is not None:
            try:
                last_stop = datetime.fromisoformat(last_stop_iso)
                if last_stop.tzinfo is None:
                    last_stop = last_stop.replace(tzinfo=timezone.utc)
                elapsed = (now_utc - last_stop).total_seconds()
                if elapsed < self.config.cooldown_after_stop_sec:
                    return RiskDecision(
                        False,
                        f"cooldown elapsed={elapsed:.1f}s < {self.config.cooldown_after_stop_sec}s "
                        f"(last_stop={last_stop_iso})",
                    )
            except (ValueError, TypeError):
                pass  # malformed timestamp — fail-open

        # 6. Daily loss limit (KST 자정 기준)
        kst_now = now_utc.astimezone(_KST)
        kst_midnight = kst_now.replace(hour=0, minute=0, second=0, microsecond=0)
        midnight_utc = kst_midnight.astimezone(timezone.utc).isoformat()
        today_pnl = self.state.realized_pnl_since(midnight_utc)
        if today_pnl <= self.config.daily_loss_limit_usd:
            return RiskDecision(
                False,
                f"daily_loss_limit pnl={today_pnl:.2f}<= {self.config.daily_loss_limit_usd:.2f} USDT "
                f"(KST 자정 기준)",
            )

        return RiskDecision(True, f"ok kst_hour={kst_hour} pnl_today={today_pnl:.2f}")
