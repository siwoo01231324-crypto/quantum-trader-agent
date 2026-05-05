"""Shadow Runs viewer — read-only summaries of shadow daemon WAL files (#198).

Discovers all `logs/shadow/*` directories created by `scripts/shadow_run_swing.py`
(or other shadow daemons) and reports per-run metadata, daemon liveness, trade
counts, and broker state. Read-only: never writes to WAL or mutates state.

Used by:
- `GET /api/shadow_runs` — list summaries
- `GET /api/shadow_runs/{run_id}` — single-run detail
- `GET /shadow_runs` — HTML dashboard page

The viewer handles three states gracefully:
- Empty parent dir → []
- Run directory exists but wal.jsonl missing → status="idle", events=0
- WAL exists but daemon hasn't fired in too long → status="dead"
"""
from __future__ import annotations

import logging
import re
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Literal

from src.live.wal import replay as wal_replay

logger = logging.getLogger(__name__)


Exchange = Literal["binance", "kis", "unknown"]
Timeframe = Literal["1m", "15m", "1h", "4h", "EOD", "unknown"]
LiveStatus = Literal["alive", "idle", "dead"]


# Liveness thresholds: if last WAL event is older than (timeframe × multiplier),
# daemon is considered dead. Different timeframes need different thresholds:
# 4h cron may legitimately have no events for hours; 1m daemon should have
# events every minute.
_TIMEFRAME_SECONDS: dict[Timeframe, int] = {
    "1m": 60,
    "15m": 900,
    "1h": 3600,
    "4h": 14400,
    "EOD": 86400,
    "unknown": 14400,
}
_ALIVE_MULT = 1.5    # last event within (tf × 1.5) → alive
_DEAD_MULT = 3.0     # last event older than (tf × 3.0) → dead
                     # in between → idle


def classify_exchange(symbol: str) -> Exchange:
    """Infer exchange from symbol naming convention.

    BTCUSDT / ETHUSDT / etc → binance.
    6-digit numeric (e.g., 005930) → kis.
    Else → unknown.
    """
    if not symbol:
        return "unknown"
    upper = symbol.upper()
    if upper.endswith("USDT") or upper.endswith("USD") or upper.endswith("BUSD"):
        return "binance"
    if re.fullmatch(r"\d{6}", symbol):
        return "kis"
    return "unknown"


def classify_timeframe(run_id: str) -> Timeframe:
    """Infer bar timeframe from run_id naming.

    `phase1-r4-switch-BTCUSDT` → 4h
    `phase1-r6-switch-BTCUSDT` → 1h
    `*-momo-kis-v1-005930`     → EOD (KIS strategies default)
    Else fall back to 4h (current Binance shadow default).
    """
    rid = run_id.lower()
    if "r4-switch" in rid or "r4_switch" in rid:
        return "4h"
    if "r6-switch" in rid or "r6_switch" in rid:
        return "1h"
    if "s2c-voltarget" in rid or "s4-funding" in rid:
        return "4h"
    if "kis" in rid or "krx" in rid:
        return "EOD"
    return "unknown"


def classify_alive(last_ts: datetime | None, timeframe: Timeframe) -> LiveStatus:
    """Liveness based on (now - last_event_ts) vs timeframe threshold."""
    if last_ts is None:
        return "idle"
    seconds = _TIMEFRAME_SECONDS.get(timeframe, _TIMEFRAME_SECONDS["unknown"])
    age = (datetime.now(timezone.utc) - last_ts).total_seconds()
    if age <= seconds * _ALIVE_MULT:
        return "alive"
    if age >= seconds * _DEAD_MULT:
        return "dead"
    return "idle"


def _extract_symbol(run_id: str) -> str:
    """Best-effort symbol extraction from run_id.

    `phase1-r4-switch-BTCUSDT` → BTCUSDT
    `phase1-momo-kis-v1-005930` → 005930
    Everything between the last hyphen and end-of-string.
    """
    last = run_id.rsplit("-", 1)
    return last[-1] if last else run_id


def _summarize_wal_events(events: list) -> dict:
    """Count events by type + identify last activity timestamp."""
    n_submitted = sum(1 for e in events if e.event_type == "order_submitted")
    n_filled = sum(1 for e in events if e.event_type == "order_filled")
    n_entry = sum(
        1 for e in events
        if e.event_type == "order_filled" and (e.payload or {}).get("side") == "BUY"
    )
    n_exit = sum(
        1 for e in events
        if e.event_type == "order_filled" and (e.payload or {}).get("side") == "SELL"
    )

    last_ts: datetime | None = None
    if events:
        try:
            last_ts = datetime.fromisoformat(events[-1].ts.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            last_ts = None
    return {
        "n_events": len(events),
        "n_submitted": n_submitted,
        "n_filled": n_filled,
        "n_entry": n_entry,
        "n_exit": n_exit,
        "last_event_ts": last_ts.isoformat() if last_ts else None,
        "_last_ts_dt": last_ts,
    }


def _summarize_run(log_dir: Path, run_dir: Path) -> dict:
    """Build a summary dict for one run directory.

    Never raises: corruption / missing wal / parse errors → status="idle"
    with diagnostic in `error` field.
    """
    run_id = run_dir.name
    symbol = _extract_symbol(run_id)
    timeframe = classify_timeframe(run_id)
    exchange = classify_exchange(symbol)

    summary = {
        "run_id": run_id,
        "symbol": symbol,
        "exchange": exchange,
        "timeframe": timeframe,
        "n_events": 0,
        "n_submitted": 0,
        "n_filled": 0,
        "n_entry": 0,
        "n_exit": 0,
        "n_corruptions": 0,
        "last_event_ts": None,
        "status": "idle",
        "error": None,
    }

    wal_path = run_dir / "wal.jsonl"
    if not wal_path.exists():
        # Directory exists but daemon hasn't written anything yet — normal for
        # cron-based runs that haven't fired their first signal.
        summary["status"] = classify_alive(None, timeframe)
        return summary

    try:
        events, corruptions = wal_replay(wal_path)
    except Exception as err:
        logger.warning("shadow_runs: WAL replay failed for %s: %s", run_id, err)
        summary["error"] = str(err)
        return summary

    summary["n_corruptions"] = len(corruptions)
    counts = _summarize_wal_events(events)
    summary["n_events"] = counts["n_events"]
    summary["n_submitted"] = counts["n_submitted"]
    summary["n_filled"] = counts["n_filled"]
    summary["n_entry"] = counts["n_entry"]
    summary["n_exit"] = counts["n_exit"]
    summary["last_event_ts"] = counts["last_event_ts"]
    summary["status"] = classify_alive(counts["_last_ts_dt"], timeframe)
    return summary


def discover_shadow_runs(log_dir: Path | str) -> list[dict]:
    """Scan `log_dir` for shadow run directories and return summaries.

    Sorted by last_event_ts descending (most active first), then run_id.
    """
    log_dir = Path(log_dir)
    if not log_dir.exists() or not log_dir.is_dir():
        return []

    runs: list[dict] = []
    for run_dir in sorted(log_dir.iterdir()):
        if not run_dir.is_dir():
            continue
        try:
            runs.append(_summarize_run(log_dir, run_dir))
        except Exception as err:
            logger.warning("shadow_runs: skip %s due to %s", run_dir.name, err)
            continue

    # Most recently active first; runs without events sort last by run_id.
    # Sort key: (0=has_event, 1=no_event), then negate ts ordering (descending).
    runs_with_ts = [r for r in runs if r.get("last_event_ts")]
    runs_no_ts = [r for r in runs if not r.get("last_event_ts")]
    runs_with_ts.sort(key=lambda r: r["last_event_ts"], reverse=True)
    runs_no_ts.sort(key=lambda r: r["run_id"])
    return runs_with_ts + runs_no_ts


def load_run_detail(log_dir: Path | str, run_id: str) -> dict | None:
    """Return rich detail for one run: summary + broker state from WAL replay.

    None if run directory does not exist.
    """
    log_dir = Path(log_dir)
    run_dir = log_dir / run_id
    if not run_dir.exists() or not run_dir.is_dir():
        return None

    summary = _summarize_run(log_dir, run_dir)
    detail: dict = {**summary}

    wal_path = run_dir / "wal.jsonl"
    if not wal_path.exists():
        detail["positions"] = []
        detail["balance_usdt"] = None
        return detail

    # Reconstruct broker state via WAL replay. PaperBroker.from_wal builds a
    # fresh broker and applies all order_filled events to derive positions and
    # balance — exactly what the daemon does on cron startup.
    try:
        from src.execution.mock_matching import MockMatchingEngine
        from src.execution.paper_broker import PaperBroker
        from src.ops.kill_switch import KillSwitch

        broker = PaperBroker.from_wal(
            path=wal_path,
            kill_switch=KillSwitch(),
            matching_engine=MockMatchingEngine(),
            initial_balance=Decimal("100000"),
        )
    except Exception as err:
        logger.warning("shadow_runs: broker replay failed for %s: %s", run_id, err)
        detail["positions"] = []
        detail["balance_usdt"] = None
        detail["error"] = (detail.get("error") or "") + f" broker_replay: {err}"
        return detail

    positions = list(broker._positions.values()) if hasattr(broker, "_positions") else []
    detail["positions"] = [
        {
            "symbol": p.symbol,
            "side": p.side.value if hasattr(p.side, "value") else str(p.side),
            "qty": str(p.qty),
            "entry_price": str(p.entry_price),
        }
        for p in positions
    ]
    balance = broker._balances.get("USDT") if hasattr(broker, "_balances") else None
    if balance is not None:
        detail["balance_usdt"] = str(balance.free)
    else:
        detail["balance_usdt"] = None

    return detail
