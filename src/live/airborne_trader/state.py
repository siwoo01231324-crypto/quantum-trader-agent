"""AirborneTraderState — SQLite WAL for positions + fire decisions.

크래시 시에도 보유 포지션 / 이미 처리한 fire 를 복원 가능. 단일 process 라
file lock 불필요, 단일 connection 으로 충분.

Schema:
  positions(
      id INTEGER PRIMARY KEY,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,             -- 'long' | 'short'
      entry_ts TEXT NOT NULL,         -- ISO UTC
      entry_px REAL NOT NULL,
      qty REAL NOT NULL,
      stop_px REAL NOT NULL,
      tp_px REAL NOT NULL,
      status TEXT NOT NULL,           -- 'open' | 'closed_tp' | 'closed_sl' | 'closed_manual'
      exit_ts TEXT,
      exit_px REAL,
      realized_pnl_usd REAL,
      fire_key TEXT NOT NULL UNIQUE   -- 1 fire = 1 position max
  );
  fires_processed(
      fire_key TEXT PRIMARY KEY,      -- (ts_iso, symbol, side)
      ts TEXT NOT NULL,
      symbol TEXT NOT NULL,
      side TEXT NOT NULL,
      decision TEXT NOT NULL,         -- 'placed' | 'skipped'
      reason TEXT NOT NULL,
      created_at TEXT NOT NULL        -- ISO UTC of decision
  );
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Iterator


class FireDecision(str, Enum):
    PLACED = "placed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class PositionRecord:
    """SQLite row 의 typed view. ``status='open'`` 인 row 만 보유 중."""
    id: int
    symbol: str
    side: str  # 'long' | 'short'
    entry_ts: str  # UTC ISO
    entry_px: float
    qty: float
    stop_px: float
    tp_px: float
    status: str
    fire_key: str
    exit_ts: str | None = None
    exit_px: float | None = None
    realized_pnl_usd: float | None = None


_SCHEMA = """
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_ts TEXT NOT NULL,
    entry_px REAL NOT NULL,
    qty REAL NOT NULL,
    stop_px REAL NOT NULL,
    tp_px REAL NOT NULL,
    status TEXT NOT NULL,
    exit_ts TEXT,
    exit_px REAL,
    realized_pnl_usd REAL,
    fire_key TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status);
CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol);

CREATE TABLE IF NOT EXISTS fires_processed (
    fire_key TEXT PRIMARY KEY,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- Daily loss kill switch (manual unlock 필요)
-- 활성 row: unlocked_at IS NULL. 최신 row 만 active 판정.
-- 자동 reset 안 됨 — KST 자정 후에도 manual unlock 까진 차단 유지.
CREATE TABLE IF NOT EXISTS kill_switch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    triggered_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    unlocked_at TEXT,
    unlocked_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_kill_switch_active ON kill_switch(unlocked_at);
"""


class AirborneTraderState:
    """SQLite-backed state store. Thread-safe within single process via ``check_same_thread=False`` not used — caller responsible."""

    def __init__(self, path: Path | str = "logs/airborne_trader/state.db") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self.path),
            isolation_level=None,  # autocommit
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.execute("PRAGMA journal_mode = WAL")

    def close(self) -> None:
        self._conn.close()

    # ── Fire deduplication ─────────────────────────────────────────────────
    def is_fire_processed(self, fire_key: str) -> bool:
        cur = self._conn.execute(
            "SELECT 1 FROM fires_processed WHERE fire_key = ?",
            (fire_key,),
        )
        return cur.fetchone() is not None

    def record_fire_decision(
        self,
        *,
        fire_key: str,
        ts_iso: str,
        symbol: str,
        side: str,
        decision: FireDecision,
        reason: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO fires_processed "
            "(fire_key, ts, symbol, side, decision, reason, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fire_key, ts_iso, symbol, side, decision.value, reason, now),
        )

    # ── Positions ──────────────────────────────────────────────────────────
    def open_position(
        self,
        *,
        symbol: str,
        side: str,
        entry_ts_iso: str,
        entry_px: float,
        qty: float,
        stop_px: float,
        tp_px: float,
        fire_key: str,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO positions "
            "(symbol, side, entry_ts, entry_px, qty, stop_px, tp_px, status, fire_key) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)",
            (symbol, side, entry_ts_iso, entry_px, qty, stop_px, tp_px, fire_key),
        )
        return int(cur.lastrowid)

    def close_position(
        self,
        *,
        position_id: int,
        exit_ts_iso: str,
        exit_px: float,
        status: str,
        realized_pnl_usd: float,
    ) -> None:
        if status not in {"closed_tp", "closed_sl", "closed_manual", "closed_timeout"}:
            raise ValueError(f"unknown close status: {status}")
        self._conn.execute(
            "UPDATE positions SET status = ?, exit_ts = ?, exit_px = ?, "
            "realized_pnl_usd = ? WHERE id = ?",
            (status, exit_ts_iso, exit_px, realized_pnl_usd, position_id),
        )

    def list_open_positions(self) -> list[PositionRecord]:
        cur = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'open' ORDER BY entry_ts ASC"
        )
        return [self._row_to_position(r) for r in cur.fetchall()]

    def find_open_by_symbol(self, symbol: str) -> PositionRecord | None:
        cur = self._conn.execute(
            "SELECT * FROM positions WHERE status = 'open' AND symbol = ? "
            "ORDER BY entry_ts DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        return self._row_to_position(row) if row else None

    def count_open(self) -> int:
        cur = self._conn.execute(
            "SELECT COUNT(*) FROM positions WHERE status = 'open'"
        )
        return int(cur.fetchone()[0])

    # ── Daily PnL / stop history ───────────────────────────────────────────
    def realized_pnl_since(self, since_utc_iso: str) -> float:
        cur = self._conn.execute(
            "SELECT COALESCE(SUM(realized_pnl_usd), 0) FROM positions "
            "WHERE status LIKE 'closed_%' AND exit_ts >= ?",
            (since_utc_iso,),
        )
        return float(cur.fetchone()[0])

    def last_stop_close_ts(self, symbol: str) -> str | None:
        """가장 최근에 stop_loss 로 청산된 시각 — cooldown 게이트 용."""
        cur = self._conn.execute(
            "SELECT exit_ts FROM positions WHERE symbol = ? "
            "AND status = 'closed_sl' ORDER BY exit_ts DESC LIMIT 1",
            (symbol,),
        )
        row = cur.fetchone()
        return row[0] if row else None

    # ── Kill switch ────────────────────────────────────────────────────────
    def is_kill_switch_active(self) -> bool:
        """가장 최신 kill_switch row 가 unlocked 안 됐으면 active."""
        cur = self._conn.execute(
            "SELECT unlocked_at FROM kill_switch ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        return row is not None and row["unlocked_at"] is None

    def trigger_kill_switch(self, reason: str) -> int:
        """차단 발동. 이미 active 면 no-op (return 기존 id).

        Returns: kill_switch row id.
        """
        if self.is_kill_switch_active():
            cur = self._conn.execute(
                "SELECT id FROM kill_switch ORDER BY id DESC LIMIT 1"
            )
            row = cur.fetchone()
            return int(row["id"])
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "INSERT INTO kill_switch (triggered_at, reason) VALUES (?, ?)",
            (now, reason),
        )
        return int(cur.lastrowid)

    def unlock_kill_switch(self, *, unlocked_by: str = "manual") -> bool:
        """active kill switch 해제. 해제 성공 시 True, 활성 row 없으면 False."""
        cur = self._conn.execute(
            "SELECT id FROM kill_switch WHERE unlocked_at IS NULL "
            "ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return False
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE kill_switch SET unlocked_at = ?, unlocked_by = ? WHERE id = ?",
            (now, unlocked_by, int(row["id"])),
        )
        return True

    def last_kill_switch_event(self) -> dict | None:
        """diagnostic — 최신 row 의 모든 필드."""
        cur = self._conn.execute(
            "SELECT id, triggered_at, reason, unlocked_at, unlocked_by "
            "FROM kill_switch ORDER BY id DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": int(row["id"]),
            "triggered_at": str(row["triggered_at"]),
            "reason": str(row["reason"]),
            "unlocked_at": row["unlocked_at"],
            "unlocked_by": row["unlocked_by"],
        }

    # ── Helpers ────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_position(row: sqlite3.Row) -> PositionRecord:
        return PositionRecord(
            id=int(row["id"]),
            symbol=str(row["symbol"]),
            side=str(row["side"]),
            entry_ts=str(row["entry_ts"]),
            entry_px=float(row["entry_px"]),
            qty=float(row["qty"]),
            stop_px=float(row["stop_px"]),
            tp_px=float(row["tp_px"]),
            status=str(row["status"]),
            fire_key=str(row["fire_key"]),
            exit_ts=str(row["exit_ts"]) if row["exit_ts"] is not None else None,
            exit_px=float(row["exit_px"]) if row["exit_px"] is not None else None,
            realized_pnl_usd=(
                float(row["realized_pnl_usd"])
                if row["realized_pnl_usd"] is not None else None
            ),
        )
