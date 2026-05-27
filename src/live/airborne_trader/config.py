"""AirborneTraderConfig — env + defaults.

Standalone trader process 의 설정. 기존 production.yaml 과 별개 (orchestrator
통과 X). dataclass 로 type-safe 하고 env var 자동 로드.

Env vars:
  BINANCE_API_KEY               — Futures API key
  BINANCE_API_SECRET            — Futures API secret
  AIRBORNE_TRADER_DAEMON        — daemon container name (기본 qta-airborne-daemon)
  AIRBORNE_TRADER_DRY_RUN       — "1" / "true" → 발주 X, log only
  AIRBORNE_TRADER_MAX_POSITIONS — 동시 보유 최대 (기본 10)
  AIRBORNE_TRADER_POSITION_USD  — trade 당 USDT 노출 (기본 200)
  AIRBORNE_TRADER_LEVERAGE      — 레버리지 (기본 10)
  AIRBORNE_TRADER_STATE_PATH    — SQLite WAL 경로 (기본 logs/airborne_trader/state.db)
  AIRBORNE_TRADER_DAILY_LOSS_USD — 일일 손실 한도 (기본 -200 USDT)
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class AirborneTraderConfig:
    """Standalone trader 설정. 모든 필드 frozen — 런타임 변경 X."""

    # ── Broker auth ────────────────────────────────────────────────────────
    api_key: str = ""
    api_secret: str = ""

    # ── Daemon listener ────────────────────────────────────────────────────
    daemon_container: str = "qta-airborne-daemon"

    # ── Trade params ───────────────────────────────────────────────────────
    position_usd: float = 200.0   # 한 trade 당 USDT 노출 (notional / leverage)
    leverage: int = 10
    max_concurrent_positions: int = 10
    stop_loss_pct: float = 0.03   # 5y bench 검증값
    take_profit_pct: float = 0.06  # R/R 1:2

    # ── Risk gates ─────────────────────────────────────────────────────────
    kst_entry_hours: frozenset[int] = field(
        default_factory=lambda: frozenset({8, 11, 16, 22})
    )
    cooldown_after_stop_sec: float = 900.0  # 15분
    daily_loss_limit_usd: float = -200.0   # 일 -200 USDT 도달 시 자동 정지
    fire_max_age_seconds: float = 300.0    # 5분 초과 fire 는 stale skip

    # ── State / dry-run ────────────────────────────────────────────────────
    state_path: Path = field(default_factory=lambda: Path("logs/airborne_trader/state.db"))
    dry_run: bool = True   # 기본 dry-run — production 진입 전 항상 명시
    poll_interval_seconds: float = 30.0  # daemon log polling 주기

    @classmethod
    def from_env(cls) -> "AirborneTraderConfig":
        """환경변수에서 로드. 미설정 = default 사용."""
        return cls(
            api_key=os.environ.get("BINANCE_API_KEY", ""),
            api_secret=os.environ.get("BINANCE_API_SECRET", ""),
            daemon_container=os.environ.get(
                "AIRBORNE_TRADER_DAEMON", "qta-airborne-daemon",
            ),
            position_usd=_env_float("AIRBORNE_TRADER_POSITION_USD", 200.0),
            leverage=_env_int("AIRBORNE_TRADER_LEVERAGE", 10),
            max_concurrent_positions=_env_int(
                "AIRBORNE_TRADER_MAX_POSITIONS", 10,
            ),
            daily_loss_limit_usd=_env_float(
                "AIRBORNE_TRADER_DAILY_LOSS_USD", -200.0,
            ),
            state_path=Path(os.environ.get(
                "AIRBORNE_TRADER_STATE_PATH",
                "logs/airborne_trader/state.db",
            )),
            dry_run=_env_bool("AIRBORNE_TRADER_DRY_RUN", True),
        )

    def __post_init__(self) -> None:
        if self.position_usd <= 0:
            raise ValueError(f"position_usd > 0 required, got {self.position_usd}")
        if self.leverage < 1:
            raise ValueError(f"leverage >= 1 required, got {self.leverage}")
        if self.max_concurrent_positions < 1:
            raise ValueError(
                f"max_concurrent_positions >= 1 required, "
                f"got {self.max_concurrent_positions}",
            )
        if not (0 < self.stop_loss_pct < 1):
            raise ValueError(
                f"stop_loss_pct in (0, 1) required, got {self.stop_loss_pct}",
            )
        if not (0 < self.take_profit_pct < 1):
            raise ValueError(
                f"take_profit_pct in (0, 1) required, got {self.take_profit_pct}",
            )
        if self.daily_loss_limit_usd >= 0:
            raise ValueError(
                f"daily_loss_limit_usd < 0 required (loss limit is negative), "
                f"got {self.daily_loss_limit_usd}",
            )
        invalid = [h for h in self.kst_entry_hours if not (0 <= int(h) <= 23)]
        if invalid:
            raise ValueError(
                f"kst_entry_hours must be in [0, 23], got invalid={invalid}",
            )
