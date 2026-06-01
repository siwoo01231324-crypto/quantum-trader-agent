"""Whitelist YAML loader + validation.

``config/airborne_short_whitelist.yaml`` 파일 schema:

    version: 1
    strategy_id: live-airborne-short-whitelist-v1
    as_of: 2026-06-01
    state:
      SYMBOL:
        status: active | candidate | warning | removed
        consecutive_pass: <int>
        consecutive_fail: <int>
        note: <str>

본 모듈:
  - ``load_whitelist(path)``         → ``WhitelistConfig``
  - ``active_symbols(cfg)``          → ``set[str]`` (status == "active" 만)
  - validation: 알 수 없는 status, 음수 카운터, 누락 필드 거부

본 모듈은 *읽기 전용* — yaml 파일을 수정하지 않음. 갱신은
``scripts/refresh_airborne_short_whitelist.py`` 의 책임.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VALID_STATUSES: frozenset[str] = frozenset(
    {"active", "candidate", "warning", "removed"}
)


@dataclass(frozen=True)
class WhitelistEntry:
    """단일 종목의 whitelist 상태."""
    symbol: str
    status: str               # active | candidate | warning | removed
    consecutive_pass: int
    consecutive_fail: int
    note: str = ""


@dataclass(frozen=True)
class WhitelistConfig:
    """전체 whitelist 설정.

    ``kst_entry_hours`` — optional list[int] (0~23). 미지정 시 None →
    daemon 이 ``AirborneTraderConfig`` 의 default ({8,11,16,22}) 사용.
    지정 시 daemon 이 config 를 ``dataclasses.replace`` 로 override.
    """
    version: int
    strategy_id: str
    as_of: str                # ISO date string
    entries: dict[str, WhitelistEntry] = field(default_factory=dict)
    kst_entry_hours: frozenset[int] | None = None


class WhitelistValidationError(ValueError):
    """yaml 파싱 또는 schema 검증 실패."""


def _coerce_entry(symbol: str, raw: Any) -> WhitelistEntry:
    if not isinstance(raw, dict):
        raise WhitelistValidationError(
            f"entry {symbol!r}: dict 필요, got {type(raw).__name__}"
        )
    status = str(raw.get("status", "")).strip()
    if status not in VALID_STATUSES:
        raise WhitelistValidationError(
            f"entry {symbol!r}: status={status!r} not in {sorted(VALID_STATUSES)}"
        )
    cp_raw = raw.get("consecutive_pass", 0)
    cf_raw = raw.get("consecutive_fail", 0)
    try:
        cp = int(cp_raw)
        cf = int(cf_raw)
    except (TypeError, ValueError) as err:
        raise WhitelistValidationError(
            f"entry {symbol!r}: consecutive_pass/fail must be int "
            f"(got {cp_raw!r} / {cf_raw!r})"
        ) from err
    if cp < 0 or cf < 0:
        raise WhitelistValidationError(
            f"entry {symbol!r}: counters must be >= 0 (got {cp}, {cf})"
        )
    note = str(raw.get("note", "") or "")
    return WhitelistEntry(
        symbol=symbol.upper(),
        status=status,
        consecutive_pass=cp,
        consecutive_fail=cf,
        note=note,
    )


def load_whitelist(path: str | Path) -> WhitelistConfig:
    """Parse + validate ``config/airborne_short_whitelist.yaml``."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"whitelist yaml not found: {p}")
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as err:
        raise WhitelistValidationError(f"yaml parse error: {err}") from err
    if not isinstance(raw, dict):
        raise WhitelistValidationError(
            f"top-level must be a mapping, got {type(raw).__name__}"
        )

    try:
        version = int(raw.get("version", 0))
    except (TypeError, ValueError) as err:
        raise WhitelistValidationError(f"invalid version: {err}") from err
    if version != 1:
        raise WhitelistValidationError(
            f"unsupported whitelist version: {version} (expected 1)"
        )

    strategy_id = str(raw.get("strategy_id", "")).strip()
    if not strategy_id:
        raise WhitelistValidationError("strategy_id field required")

    as_of = str(raw.get("as_of", "")).strip()
    if not as_of:
        raise WhitelistValidationError("as_of field required")

    state_raw = raw.get("state") or {}
    if not isinstance(state_raw, dict):
        raise WhitelistValidationError(
            f"state must be a mapping, got {type(state_raw).__name__}"
        )

    entries: dict[str, WhitelistEntry] = {}
    for sym, body in state_raw.items():
        if not isinstance(sym, str) or not sym.strip():
            raise WhitelistValidationError(f"invalid symbol key: {sym!r}")
        entry = _coerce_entry(sym, body)
        entries[entry.symbol] = entry

    # Optional KST hour gate override
    kst_hours: frozenset[int] | None = None
    raw_hours = raw.get("kst_entry_hours")
    if raw_hours is not None:
        if not isinstance(raw_hours, (list, tuple)):
            raise WhitelistValidationError(
                f"kst_entry_hours must be a list, got {type(raw_hours).__name__}"
            )
        parsed: set[int] = set()
        for h in raw_hours:
            try:
                hi = int(h)
            except (TypeError, ValueError) as err:
                raise WhitelistValidationError(
                    f"kst_entry_hours element must be int, got {h!r}"
                ) from err
            if not (0 <= hi <= 23):
                raise WhitelistValidationError(
                    f"kst_entry_hours element {hi} not in [0, 23]"
                )
            parsed.add(hi)
        if not parsed:
            raise WhitelistValidationError(
                "kst_entry_hours present but empty — remove field or list >=1 hour"
            )
        kst_hours = frozenset(parsed)

    return WhitelistConfig(
        version=version,
        strategy_id=strategy_id,
        as_of=as_of,
        entries=entries,
        kst_entry_hours=kst_hours,
    )


def active_symbols(cfg: WhitelistConfig) -> frozenset[str]:
    """Return symbols with ``status == "active"`` only.

    daemon 가 실제 발주 universe 로 사용. candidate/warning/removed 은 제외.
    """
    return frozenset(
        e.symbol for e in cfg.entries.values() if e.status == "active"
    )


def candidate_symbols(cfg: WhitelistConfig) -> frozenset[str]:
    """``status == "candidate"`` — shadow mode (testnet only) 권장 종목."""
    return frozenset(
        e.symbol for e in cfg.entries.values() if e.status == "candidate"
    )
