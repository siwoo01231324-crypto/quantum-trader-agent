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

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

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
        feed_mode=args.feed,
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
    # ── #177 EXE wiring ────────────────────────────────────────────────
    parser.add_argument(
        "--feed", choices=["auto", "binance", "kis", "mock"], default="auto",
        help="시세 feed 선택 (auto=KRX 종목이면 KIS REST, 그 외 Binance WS).",
    )
    parser.add_argument(
        "--mock-bars", type=int, default=30,
        help="--feed mock 사용 시 합성 bar 개수 (default 30).",
    )
    parser.add_argument(
        "--dashboard-port", type=int, default=8000,
        help="로컬 대시보드(/, /metrics, /ws/timeline) 포트. 0=비활성. default 8000.",
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


def _build_kis_client(feed_mode: str, symbols: list[str]):
    """Build a sync KISClient for REST polling/warmup when KRX feed is active.

    Returns None when no KIS REST surface is required (mock/binance feed, no
    KRX symbols). Failures to construct (e.g. missing env vars) are surfaced
    as SystemExit so EXE smoke runs fail loudly.
    """
    needs_kis = feed_mode == "kis" or (
        feed_mode == "auto" and any(s.isdigit() and len(s) == 6 for s in symbols)
    )
    if not needs_kis:
        return None
    from src.brokers.kis.auth import KISAuth
    from src.brokers.kis.rest import KISClient
    app_key = os.environ.get("HANTOO_FAKE_API_KEY") or os.environ.get("KIS_APP_KEY")
    app_secret = os.environ.get("HANTOO_FAKE_SECRET_API_KEY") or os.environ.get("KIS_APP_SECRET")
    credit_number = (
        os.environ.get("HANTOO_FAKE_CREDIT_NUMBER")
        or os.environ.get("HANTOO_CREDIT_NUMBER")
        or "00000000"
    )
    missing = [
        k for k, v in [
            ("HANTOO_FAKE_API_KEY", app_key),
            ("HANTOO_FAKE_SECRET_API_KEY", app_secret),
        ] if not v
    ]
    if missing:
        raise SystemExit(
            f"feed=kis (or auto KRX) requires env vars: {', '.join(missing)}"
        )
    cano = credit_number[:8] if len(credit_number) >= 8 else credit_number.ljust(8, "0")
    acnt_prdt_cd = (
        os.environ.get("HANTOO_ACNT_PRDT_CD")
        or (credit_number[8:10] if len(credit_number) >= 10 else "01")
    )
    auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)
    return KISClient(
        auth=auth,
        app_key=app_key,
        app_secret=app_secret,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        paper=True,
    )


def _build_mock_ticks(symbols: list[str], n_bars: int):
    """Synthesize *n_bars* deterministic 1m ticks per symbol (smoke test).

    Pattern: trend-down for first half, trend-up for second half — engineered
    so RSI divergence (lower price low with higher RSI low) appears in the
    tail window. Mirrors `tests/test_production_yaml_smoke.py::_build_history`.

    Time alignment: starts at 2026-05-04T01:00 UTC = 2026-05-04 10:00 KST,
    so KST minute boundaries 0/15/30/45 land on ticks 0/15/30/45 — matching
    MomoKisV1's 15-min KRX session gate.
    """
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal as _Dec
    import random
    from src.live.types import Tick

    ticks: list = []
    base_ts = datetime(2026, 5, 4, 1, 0, tzinfo=timezone.utc)
    # Closed-form path engineered for `signals.rsi.detect_divergence`
    # (lookback=14) so a bullish divergence is the LATEST entry in `div` at
    # MomoKisV1's KST 15-min boundary (iter 75). Phases:
    #   bars  0..29  — warmup ramp 80_000 → 80_700 (RSI primes near 65)
    #   bars 30..44  — sharp drop 80_700 → 71_000 (RSI hits ~18)
    #   bars 45..59  — recovery   71_000 → 78_000 (RSI back to ~55)
    #   bars 60..74  — milder drop 78_000 → 69_000 — NEW lower low,
    #                  RSI ≈ 30 (HIGHER than first leg) → bullish at iter 75
    #   bars 75..    — bounce (post-signal continuation; sample sizing window)
    def _phase_price(i: int) -> float:
        if i < 30:
            return 80_000.0 + 24.0 * i                         # 80000 → 80700
        if i < 45:
            return 80_700.0 - (9_700.0 / 14.0) * (i - 29)      # 80700 → 71000
        if i < 60:
            return 71_000.0 + (7_000.0 / 14.0) * (i - 44)      # 71000 → 78000
        if i < 75:
            return 78_000.0 - (9_000.0 / 14.0) * (i - 59)      # 78000 → 69000
        return 69_000.0 + 250.0 * (i - 74)                     # bounce
    for sym in symbols:
        for i in range(n_bars):
            ts = (base_ts + timedelta(minutes=i)).isoformat()
            ticks.append(Tick(
                symbol=sym,
                price=_Dec(f"{_phase_price(i):.0f}"),
                qty=_Dec("1000"),
                ts=ts,
                server_ts=ts,
            ))
    return ticks


def _start_dashboard(state, port: int, logger):
    """Start uvicorn FastAPI dashboard in the current event loop as a Task.

    Returns (task, shutdown_callable). Caller awaits the task on shutdown.
    """
    import uvicorn
    from src.dashboard.app import create_app

    app = create_app(state)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port,
        log_level="warning", access_log=False,
        # use the loop the parent run_shadow_loop is already using
        loop="none",
    )
    server = uvicorn.Server(config)
    server.config.setup_event_loop = lambda: None  # avoid policy override on Windows
    logger.info("Dashboard listening at http://127.0.0.1:%d (/ws/timeline)", port)
    task = asyncio.create_task(server.serve(), name="qta-dashboard")

    async def _shutdown():
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()

    return task, _shutdown


async def _run_pipeline(config, kis_adapter, dashboard_port: int, logger,
                       duration_sec: float):
    from dataclasses import asdict
    from src.dashboard.app import DashboardState
    from src.dashboard.timeline_broker import TimelineBroker

    timeline_broker = TimelineBroker()
    dashboard_state = DashboardState(
        timeline_broker=timeline_broker,
        wal_path=config.wal_path,
    )
    config.wal_observer = lambda ev: timeline_broker.publish(asdict(ev))
    # #180: surface the live orchestrator into DashboardState so the
    # /api/strategies/{id}/toggle endpoint can call enable/disable.
    config.on_orchestrator_ready = lambda orch: setattr(
        dashboard_state, "orchestrator", orch,
    )

    shutdown_dashboard = None
    if dashboard_port > 0:
        try:
            _task, shutdown_dashboard = _start_dashboard(
                dashboard_state, dashboard_port, logger,
            )
        except OSError as err:
            logger.warning(
                "dashboard.start_failed port=%d error=%s — continuing without dashboard",
                dashboard_port, err,
            )

    try:
        await _run_with_duration(
            run_shadow_loop(config, kis_adapter=kis_adapter),
            duration_sec,
        )
    finally:
        if shutdown_dashboard is not None:
            await shutdown_dashboard()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("live_run")
    logger.info("Phase 2 KIS Live Loop starting")
    logger.info(
        "symbols=%s broker=%s duration=%s auto_fallback=%s feed=%s dashboard_port=%d",
        args.symbols, args.broker, args.duration, args.auto_fallback,
        args.feed, args.dashboard_port,
    )

    # Override from env if set (Architect note #5)
    halt_threshold = int(os.environ.get("KIS_FILL_MISSING_HALT_THRESHOLD", "1"))
    logger.info("KIS_FILL_MISSING_HALT_THRESHOLD=%d", halt_threshold)

    config = _build_config(args)
    duration_sec = _parse_duration(args.duration)

    # Mock-feed payload + KIS REST client wiring (#177)
    if args.feed == "mock":
        config.mock_ticks = _build_mock_ticks(args.symbols, args.mock_bars)
    else:
        try:
            config.kis_client = _build_kis_client(args.feed, args.symbols)
        except SystemExit:
            raise

    try:
        kis_adapter = _build_kis_adapter(args.broker)
        asyncio.run(
            _run_pipeline(
                config, kis_adapter, args.dashboard_port, logger, duration_sec,
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
