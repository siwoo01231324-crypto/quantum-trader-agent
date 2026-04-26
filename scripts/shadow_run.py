#!/usr/bin/env python3
"""Shadow Live Loop CLI 진입점 — 이슈 #80 Phase E.

사용법:
    python scripts/shadow_run.py --symbols BTCUSDT,ETHUSDT --duration 8h
    python scripts/shadow_run.py --symbols BTCUSDT --max-iterations 100  (테스트용)
    python scripts/shadow_run.py --config configs/shadow.yaml  (향후)

기본 동작:
- BinancePublicFeed (aggTrade WS) 자동 연결
- production.yaml 부재 시 fallback (Phase 1 stub, #94 머지 후 활성화)
- WAL: logs/shadow/{run_id}/wal.jsonl
- Lock: logs/shadow/{run_id}/.live_loop.lock
- 메트릭: 8종 paper-* + 기존 활용
- Graceful shutdown: SIGINT/SIGTERM
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.live.loop import ShadowConfig, run_shadow_loop


def _parse_duration(s: str) -> float:
    """'8h', '30m', '15s' → seconds (float). '0' = unlimited."""
    s = s.strip().lower()
    if s in ("0", ""):
        return 0.0
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("s"):
        return float(s[:-1])
    return float(s)


def _build_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _build_config(args: argparse.Namespace) -> ShadowConfig:
    run_id = args.run_id or _build_run_id()
    base = Path(args.log_dir) / run_id
    return ShadowConfig(
        symbols=args.symbols,
        wal_path=base / "wal.jsonl",
        lock_path=base / ".live_loop.lock",
        initial_balance=Decimal(str(args.initial_balance)),
        production_yaml=Path(args.production_yaml),
        max_iterations=args.max_iterations,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Issue #80 Phase 1 Shadow Live Loop CLI")
    parser.add_argument(
        "--symbols", required=True,
        help="Comma-separated symbol list, e.g., BTCUSDT,ETHUSDT",
    )
    parser.add_argument(
        "--duration", default="0",
        help="Run duration (e.g., 8h, 30m, 15s, 0=unlimited). Default 0.",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Stop after N tick iterations (test mode).",
    )
    parser.add_argument(
        "--initial-balance", type=str, default="100000",
        help="Initial paper balance in USDT (default 100000).",
    )
    parser.add_argument(
        "--log-dir", type=str, default="logs/shadow",
        help="Base log directory (default logs/shadow).",
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="Run identifier (default: UTC timestamp).",
    )
    parser.add_argument(
        "--production-yaml", type=str,
        default="configs/orchestrator/production.yaml",
        help="Path to orchestrator config YAML (#94 dependency).",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)
    args.symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    return args


async def _run_with_duration(coro, duration_sec: float) -> None:
    if duration_sec <= 0:
        await coro
        return
    try:
        await asyncio.wait_for(coro, timeout=duration_sec)
    except asyncio.TimeoutError:
        logging.info("Duration %ss reached; shutting down.", duration_sec)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("shadow_run")
    logger.info("Phase 1 Shadow Live Loop starting")
    logger.info(
        "symbols=%s duration=%s log_dir=%s run_id=%s",
        args.symbols, args.duration, args.log_dir, args.run_id,
    )

    config = _build_config(args)
    duration_sec = _parse_duration(args.duration)
    try:
        asyncio.run(_run_with_duration(run_shadow_loop(config), duration_sec))
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as err:
        logger.exception("Shadow loop failed: %s", err)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
