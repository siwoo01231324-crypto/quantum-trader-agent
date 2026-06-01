"""Whitelist YAML loader 검증 — schema validation + active 필터."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.live.airborne_short_whitelist.whitelist_loader import (
    WhitelistValidationError,
    active_symbols,
    candidate_symbols,
    load_whitelist,
)


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "whitelist.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_minimal_valid(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: live-airborne-short-whitelist-v1
as_of: 2026-06-01
state:
  ARBUSDT:
    status: active
    consecutive_pass: 5
    consecutive_fail: 0
  BNBUSDT:
    status: candidate
    consecutive_pass: 1
    consecutive_fail: 0
""")
    cfg = load_whitelist(p)
    assert cfg.version == 1
    assert cfg.strategy_id == "live-airborne-short-whitelist-v1"
    assert cfg.as_of == "2026-06-01"
    assert set(cfg.entries) == {"ARBUSDT", "BNBUSDT"}
    arb = cfg.entries["ARBUSDT"]
    assert arb.status == "active"
    assert arb.consecutive_pass == 5
    assert arb.consecutive_fail == 0


def test_active_symbols_excludes_non_active(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: live-airborne-short-whitelist-v1
as_of: 2026-06-01
state:
  ARBUSDT: {status: active, consecutive_pass: 5, consecutive_fail: 0}
  BNBUSDT: {status: candidate, consecutive_pass: 1, consecutive_fail: 0}
  ETHUSDT: {status: warning, consecutive_pass: 0, consecutive_fail: 1}
  AAVEUSDT: {status: removed, consecutive_pass: 0, consecutive_fail: 5}
  XRPUSDT: {status: active, consecutive_pass: 12, consecutive_fail: 0}
""")
    cfg = load_whitelist(p)
    assert active_symbols(cfg) == frozenset({"ARBUSDT", "XRPUSDT"})
    assert candidate_symbols(cfg) == frozenset({"BNBUSDT"})


def test_symbol_uppercased(tmp_path: Path) -> None:
    """소문자 symbol 도 받아서 대문자로 정규화."""
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
state:
  arbusdt: {status: active, consecutive_pass: 0, consecutive_fail: 0}
""")
    cfg = load_whitelist(p)
    assert "ARBUSDT" in cfg.entries
    assert cfg.entries["ARBUSDT"].symbol == "ARBUSDT"


def test_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_whitelist(tmp_path / "nonexistent.yaml")


def test_invalid_yaml_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "not: : valid: yaml: [\n")
    with pytest.raises(WhitelistValidationError):
        load_whitelist(p)


def test_bad_version_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 99
strategy_id: x
as_of: 2026-06-01
state: {}
""")
    with pytest.raises(WhitelistValidationError, match="unsupported.*version"):
        load_whitelist(p)


def test_missing_strategy_id_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
as_of: 2026-06-01
state: {}
""")
    with pytest.raises(WhitelistValidationError, match="strategy_id"):
        load_whitelist(p)


def test_invalid_status_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
state:
  ARBUSDT: {status: maybe, consecutive_pass: 0, consecutive_fail: 0}
""")
    with pytest.raises(WhitelistValidationError, match="status"):
        load_whitelist(p)


def test_negative_counter_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
state:
  ARBUSDT: {status: active, consecutive_pass: -1, consecutive_fail: 0}
""")
    with pytest.raises(WhitelistValidationError, match="counters"):
        load_whitelist(p)


def test_real_config_loads() -> None:
    """레포 안의 실제 운영 yaml 파일이 valid 한지 검증."""
    p = Path(__file__).resolve().parents[3] / "config" / "airborne_short_whitelist.yaml"
    cfg = load_whitelist(p)
    assert cfg.version == 1
    assert cfg.strategy_id == "live-airborne-short-whitelist-v1"
    # 21종 (초기 spec)
    assert len(cfg.entries) == 21
    # 코어 active 종목 sanity
    actives = active_symbols(cfg)
    assert "FETUSDT" in actives
    assert "APTUSDT" in actives
    assert "ARBUSDT" in actives
    # drift 의심 종목은 candidate
    cands = candidate_symbols(cfg)
    assert "BNBUSDT" in cands
    assert "ETHUSDT" in cands
    # kst_entry_hours override — Hard OOS hour sweep 결과 19시간
    assert cfg.kst_entry_hours is not None
    assert len(cfg.kst_entry_hours) == 19
    # 알파 음수 시간 제외 확인
    assert 4 not in cfg.kst_entry_hours
    assert 6 not in cfg.kst_entry_hours
    assert 7 not in cfg.kst_entry_hours
    assert 8 not in cfg.kst_entry_hours  # legacy default 포함했었음
    assert 13 not in cfg.kst_entry_hours
    # 알파 양수 시간 포함 확인 (대표)
    assert 0 in cfg.kst_entry_hours
    assert 12 in cfg.kst_entry_hours
    assert 18 in cfg.kst_entry_hours


def test_kst_entry_hours_absent_returns_none(tmp_path: Path) -> None:
    """``kst_entry_hours`` 미지정 시 None — daemon 이 legacy default 사용."""
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
state:
  ARBUSDT: {status: active, consecutive_pass: 0, consecutive_fail: 0}
""")
    cfg = load_whitelist(p)
    assert cfg.kst_entry_hours is None


def test_kst_entry_hours_parsed(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
kst_entry_hours: [0, 1, 12, 23]
state:
  ARBUSDT: {status: active, consecutive_pass: 0, consecutive_fail: 0}
""")
    cfg = load_whitelist(p)
    assert cfg.kst_entry_hours == frozenset({0, 1, 12, 23})


def test_kst_entry_hours_out_of_range_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
kst_entry_hours: [0, 24]
state: {ARBUSDT: {status: active, consecutive_pass: 0, consecutive_fail: 0}}
""")
    with pytest.raises(WhitelistValidationError, match="not in"):
        load_whitelist(p)


def test_kst_entry_hours_empty_list_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
kst_entry_hours: []
state: {ARBUSDT: {status: active, consecutive_pass: 0, consecutive_fail: 0}}
""")
    with pytest.raises(WhitelistValidationError, match="empty"):
        load_whitelist(p)


def test_kst_entry_hours_non_list_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, """
version: 1
strategy_id: x
as_of: 2026-06-01
kst_entry_hours: "8,11,16"
state: {ARBUSDT: {status: active, consecutive_pass: 0, consecutive_fail: 0}}
""")
    with pytest.raises(WhitelistValidationError, match="list"):
        load_whitelist(p)
