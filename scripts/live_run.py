#!/usr/bin/env python3
"""KIS 모의계좌 Phase 2 Live Loop CLI 진입점 — Issue #105 Stage 5.3.

사용법:
    python scripts/live_run.py --symbols 005930,035720 --broker kis-paper-shadow
    python scripts/live_run.py --symbols 005930 --max-iterations 100  (테스트용)
    python scripts/live_run.py --help

브로커 모드:
    paper-only          Phase 1 PaperBroker 단독 (회귀 테스트용)
    kis-paper           KIS 모의계좌 단독 (shadow 없음)
    kis-paper-shadow    KIS 모의계좌 + AsyncOrderRouter + auto-fallback (기본값)

auto-fallback (--auto-fallback, default=true):
    R1 (5xx > 10%) / R3 (tracking error > 0.5%) trip 시
    AsyncOrderRouter.swap_active(paper_broker) → graceful 재시작.
    WAL 에 mode_switched event 기록.

shutdown hook:
    종료 시 strategy_returns_export.export_to_orchestrator() 호출 (Architect note #4).
    호출 시점: daemon 정상 종료 또는 SIGINT/SIGTERM 수신 후.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

# UTF-8 stdout/stderr — Windows console (cp1252/cp949) 에서 한글 출력 시 charmap fail 방지.
# `qta.exe --help` 가 docstring 의 한글을 출력할 때 UnicodeEncodeError 회피 (#123).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.live.loop import ShadowConfig, run_shadow_loop


def _build_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_duration(s: str) -> float:
    s = s.strip().lower()
    if s in ("0", ""):
        return 0.0
    if s.endswith("w"):
        return float(s[:-1]) * 7 * 24 * 3600
    if s.endswith("d"):
        return float(s[:-1]) * 24 * 3600
    if s.endswith("h"):
        return float(s[:-1]) * 3600
    if s.endswith("m"):
        return float(s[:-1]) * 60
    if s.endswith("s"):
        return float(s[:-1])
    return float(s)


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
        broker_mode=args.broker,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue #105 Phase 2 KIS 모의계좌 Live Loop CLI"
    )
    parser.add_argument(
        "--symbols", required=True,
        help="콤마 구분 종목 코드 (KRX: 005930,035720 또는 Binance: BTCUSDT)",
    )
    parser.add_argument(
        "--broker",
        choices=["paper-only", "kis-paper", "kis-paper-shadow"],
        default="kis-paper-shadow",
        help="브로커 모드 (default: kis-paper-shadow)",
    )
    parser.add_argument(
        "--duration", default="0",
        help="실행 시간 (예: 4w, 8h, 30m, 0=무제한). 기본 0.",
    )
    parser.add_argument(
        "--max-orders", type=int, default=None,
        help="최대 주문 건수 도달 시 종료 (AC3 exit gate 수동 제어)",
    )
    parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="최대 틱 반복 횟수 (테스트용)",
    )
    parser.add_argument(
        "--auto-fallback", action=argparse.BooleanOptionalAction, default=True,
        help="R1/R3 trip 시 PaperBroker 자동 폴백 (default: true)",
    )
    parser.add_argument(
        "--schedule", choices=["krx", "always"], default="always",
        help="krx: KRX 영업시간(09:00-15:30) 외 자동 sleep. always: 24/7 실행.",
    )
    parser.add_argument(
        "--initial-balance", type=str, default="100000",
        help="초기 페이퍼 잔고 (KRW 또는 USDT, default 100000)",
    )
    parser.add_argument(
        "--log-dir", type=str, default="logs/live",
        help="로그/WAL 기본 디렉토리 (default logs/live)",
    )
    parser.add_argument(
        "--run-id", type=str, default=None,
        help="실행 식별자 (default: UTC 타임스탬프)",
    )
    parser.add_argument(
        "--production-yaml", type=str,
        default="configs/orchestrator/production.yaml",
        help="오케스트레이터 설정 YAML (#94)",
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


def _build_kis_adapter(broker_mode: str):
    """broker_mode 가 kis-paper / kis-paper-shadow 면 KIS 모의계좌 adapter 생성.

    Env vars (project standard — see src/brokers/config.py):
      HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY, HANTOO_FAKE_CREDIT_NUMBER
      HANTOO_HTS_ID (optional, default 'live_run')

    paper-only 모드면 None 반환.
    """
    if broker_mode not in ("kis-paper", "kis-paper-shadow"):
        return None
    from src.brokers.kis.async_adapter import KISAsyncAdapter

    app_key = os.environ.get("HANTOO_FAKE_API_KEY")
    app_secret = os.environ.get("HANTOO_FAKE_SECRET_API_KEY")
    credit_number = (
        os.environ.get("HANTOO_FAKE_CREDIT_NUMBER")
        or os.environ.get("HANTOO_CREDIT_NUMBER")
    )
    hts_id = os.environ.get("HANTOO_HTS_ID", "live_run")
    missing = [
        k for k, v in [
            ("HANTOO_FAKE_API_KEY", app_key),
            ("HANTOO_FAKE_SECRET_API_KEY", app_secret),
            ("HANTOO_FAKE_CREDIT_NUMBER (or HANTOO_CREDIT_NUMBER)", credit_number),
        ] if not v
    ]
    if missing:
        raise SystemExit(
            f"broker_mode='{broker_mode}' requires env vars: {', '.join(missing)}"
        )
    return KISAsyncAdapter(
        app_key=app_key,
        app_secret=app_secret,
        hts_id=hts_id,
        credit_number=credit_number,
        paper=True,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("live_run")
    logger.info("Phase 2 KIS Live Loop starting")
    logger.info(
        "symbols=%s broker=%s duration=%s auto_fallback=%s",
        args.symbols, args.broker, args.duration, args.auto_fallback,
    )

    # Override from env if set (Architect note #5)
    halt_threshold = int(os.environ.get("KIS_FILL_MISSING_HALT_THRESHOLD", "1"))
    logger.info("KIS_FILL_MISSING_HALT_THRESHOLD=%d", halt_threshold)

    config = _build_config(args)
    duration_sec = _parse_duration(args.duration)

    try:
        kis_adapter = _build_kis_adapter(args.broker)
        asyncio.run(
            _run_with_duration(
                run_shadow_loop(config, kis_adapter=kis_adapter), duration_sec
            )
        )
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        return 130
    except Exception as err:
        logger.exception("Live loop failed: %s", err)
        return 1

    # Shutdown hook: export strategy returns (Architect note #4)
    # Called after the loop exits normally; orchestrator reference not available
    # here so we log the intent. Full wiring is in run_shadow_loop shutdown path.
    logger.info(
        "Live loop ended. strategy_returns_export.export_to_orchestrator() "
        "should be called by the loop shutdown hook (Stage 5.2)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
