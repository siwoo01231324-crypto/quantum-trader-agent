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
import webbrowser
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

QTA_VERSION = "0.1.0"

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


def _autoload_dotenv() -> None:
    """EXE/dev 환경에서 .env 자동 탐색 (#182, walk-up #215).

    탐색 순서 (먼저 발견되는 것이 적용):
      1. frozen: sys.executable 부모 → 그 부모 → 드라이브 루트까지 walk-up
         dev:    _REPO_ROOT → cwd → 그 부모 → 드라이브 루트까지 walk-up
      2. 위에서 못 찾으면 dotenv 의 find_dotenv 기본 동작에 위임

    frozen 시 `__file__` 이 `_MEIPASS` 안이라 dotenv 의 find_dotenv 가
    호출자 프레임 기준으로 못 찾는 문제(사용자 .env 가 EXE 보다 상위
    디렉토리에 있을 때) 를 walk-up 으로 명시 회피.
    """
    try:
        from dotenv import load_dotenv  # noqa: PLC0415
    except ImportError:
        return

    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        cur = Path(sys.executable).resolve().parent
    else:
        candidates.append(_REPO_ROOT / ".env")
        cur = Path.cwd().resolve()
    candidates.append(cur / ".env")
    candidates.extend(parent / ".env" for parent in cur.parents)

    seen: set[Path] = set()
    for env_path in candidates:
        if env_path in seen:
            continue
        seen.add(env_path)
        if env_path.exists():
            load_dotenv(env_path, override=False)
            return
    load_dotenv(override=False)  # last-resort: dotenv 의 find_dotenv


_autoload_dotenv()


def _bundle_root() -> Path:
    """PyInstaller frozen 시 sys._MEIPASS, 그 외 _REPO_ROOT (#182)."""
    meipass = getattr(sys, "_MEIPASS", None)
    return Path(meipass) if meipass else _REPO_ROOT


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
        schedule=args.schedule,  # #216 fix: was unused (argparse-only) before
    )


def _is_no_args(argv: list[str] | None = None) -> bool:
    """더블클릭 패턴 감지 — sys.argv[0] 빼고 인자 0개 (#182).

    --help 는 argparse 가 자체 처리하도록 위임 (no-args 아님).
    """
    args = argv if argv is not None else sys.argv[1:]
    return len(args) == 0


def _count_strategies(yaml_path: Path) -> int:
    """production.yaml 의 strategies 개수. 파싱 실패/파일 없음 → 0 (#182)."""
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return 0
        strats = data.get("strategies") or []
        return len(strats) if isinstance(strats, list) else 0
    except Exception:
        return 0


def _print_startup_banner(strategies_count: int, dashboard_port: int) -> None:
    """ASCII 시작 배너 — qta.exe 더블클릭 시 콘솔창에 표시 (#182)."""
    url = f"http://localhost:{dashboard_port}" if dashboard_port > 0 else "(dashboard off)"
    banner = (
        "\n"
        "   ___    _____      _\n"
        "  / _ \\  |_   _|    / \\     QTA · quantum-trader-agent v" + QTA_VERSION + "\n"
        " | | | |   | |     / _ \\    Local trading desk · " + url + "\n"
        " | |_| |   | |    / ___ \\   Strategies registered: " + str(strategies_count) + "\n"
        "  \\__\\_\\   |_|   /_/   \\_\\\n"
    )
    print(banner)


def _run_check_bundle() -> int:
    """CI/release smoke — verify bundled assets are present (#215).

    Run as `qta.exe --check-bundle` (no other flags). Returns 0 only if all
    bundled assets that dashboard needs are reachable from `_bundle_root()`:

      - configs/orchestrator/production.yaml
      - docs/specs/strategies/*.md  (strategy_catalog parses ≥ 5 specs)

    Past regression: #178 inlined the strategy catalog on `/` but qta.spec
    only bundled `configs/`, so the EXE rendered an empty grid. This check
    fails the CI build instead of shipping a silently-broken EXE.
    """
    root = _bundle_root()
    errors: list[str] = []

    yaml_path = root / "configs/orchestrator/production.yaml"
    if not yaml_path.exists():
        errors.append(f"missing: {yaml_path}")

    specs_dir = root / "docs/specs/strategies"
    if not specs_dir.exists():
        errors.append(f"missing: {specs_dir}")
    else:
        from src.dashboard.strategy_catalog import load_strategy_catalog  # noqa: PLC0415
        items = load_strategy_catalog(specs_dir)
        if len(items) < 5:
            errors.append(
                f"strategy_catalog count={len(items)} (<5) — specs may not be bundled"
            )
        else:
            print(f"[check-bundle] strategy_catalog count={len(items)} (OK)")
            for it in items:
                print(f"  - {it.get('id')}: {it.get('name')}")

    if errors:
        for e in errors:
            print(f"[check-bundle] FAIL: {e}", file=sys.stderr)
        return 1
    print(f"[check-bundle] PASS — bundle_root={root}")
    return 0


def _show_first_run_help() -> int:
    """텍스트 도움말 + Press Enter 모드 (CI/테스트용, #182).

    QTA_FIRST_RUN_HELP_ONLY=true 환경변수 시 _run_dashboard_only_mode 대신 호출됨.
    """
    yaml_path = _bundle_root() / "configs/orchestrator/production.yaml"
    n_strats = _count_strategies(yaml_path)
    _print_startup_banner(n_strats, dashboard_port=8000)
    _build_parser().print_help()
    print()
    print("To start: qta.exe --symbols 005930 --broker kis-paper-shadow")
    print("Then open the dashboard:  http://localhost:8000")
    print()
    try:
        input("Press Enter to exit...")
    except EOFError:
        pass
    return 0


def _run_dashboard_only_mode(port: int = 8000) -> int:
    """qta.exe 더블클릭(인자 없음) → 대시보드만 자동 기동 + 자동 브라우저 (#182 B 안).

    거래는 시작하지 않음. 사용자는 대시보드에서 5 전략 카드를 확인하고
    별도 명령줄에서 `qta.exe --symbols 005930 ...` 로 거래를 시작한다.

    Ctrl+C 까지 uvicorn 이 listen. 종료 시 graceful shutdown.
    """
    yaml_path = _bundle_root() / "configs/orchestrator/production.yaml"
    n_strats = _count_strategies(yaml_path)
    _print_startup_banner(n_strats, dashboard_port=port)
    print(f"Dashboard ready: http://localhost:{port}")
    print("거래는 아직 시작 안 됨 — 별도 cmd 에서 다음 명령으로 시작:")
    print("  qta.exe --symbols 005930 --broker kis-paper-shadow")
    print("종료: 이 콘솔창에서 Ctrl+C")
    print()

    async def _serve() -> None:
        import uvicorn  # noqa: PLC0415
        from src.dashboard.app import DashboardState, create_app  # noqa: PLC0415
        from src.dashboard.timeline_broker import TimelineBroker  # noqa: PLC0415
        from src.dashboard.run_controller import RunController  # noqa: PLC0415
        from src.dashboard.account_info import AccountInfoProvider  # noqa: PLC0415

        state = DashboardState(timeline_broker=TimelineBroker(), wal_path=None)
        state.run_controller = RunController(
            _build_pipeline_factory(state, logging.getLogger("qta-pipeline"))
        )
        state.account_info_provider = AccountInfoProvider()
        app = create_app(state)
        config = uvicorn.Config(
            app, host="127.0.0.1", port=port,
            log_level="warning", access_log=False, loop="none",
        )
        server = uvicorn.Server(config)
        server.config.setup_event_loop = lambda: None

        async def _open_browser() -> None:
            await asyncio.sleep(1.0)
            try:
                webbrowser.open(f"http://localhost:{port}")
            except Exception:
                pass

        asyncio.create_task(_open_browser())
        await server.serve()

    try:
        asyncio.run(_serve())
    except KeyboardInterrupt:
        print("\n[qta] dashboard stopped (Ctrl+C)")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """argparse Parser 생성 (parse_args 와 _show_first_run_help 양쪽에서 재사용)."""
    parser = argparse.ArgumentParser(
        prog="qta",
        description="quantum-trader-agent — Phase 2 KIS 모의계좌 Live Loop CLI",
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
    parser.add_argument(
        "--no-browser", action="store_true", default=False,
        help="대시보드 자동 브라우저 열기 비활성화 (default: 자동 열림, #182).",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
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


async def _run_pipeline_attached(
    state, config, kis_adapter, logger, duration_sec: float,
) -> None:
    """이미 떠있는 dashboard 에 attach — 거래만 시작 (#182 단계 2).

    dashboard 재기동 없이 state.timeline_broker 를 WAL fan-out observer 로 와이어링한 뒤
    run_shadow_loop 호출. RunController 가 이 함수를 background task 로 돌린다.
    """
    from dataclasses import asdict
    if state.timeline_broker is not None:
        config.wal_observer = lambda ev: state.timeline_broker.publish(asdict(ev))
    await _run_with_duration(
        run_shadow_loop(config, kis_adapter=kis_adapter),
        duration_sec,
    )


def _build_pipeline_factory(state, logger):
    """RunController 의 pipeline_factory — 대시보드 시작 버튼 클릭 시 호출 (#182).

    params: {symbols?: list[str] | str, broker?: str, duration?: str}
    """
    def _resolve_symbols(params):
        s = params.get("symbols")
        if isinstance(s, list):
            return [x for x in s if x]
        if isinstance(s, str) and s.strip():
            return [x.strip() for x in s.split(",") if x.strip()]
        # production.yaml 의 첫 KRX 전략 symbol fallback
        yaml_path = _bundle_root() / "configs/orchestrator/production.yaml"
        try:
            import yaml as _yaml  # noqa: PLC0415
            data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            for s in data.get("strategies", []):
                sym = (s.get("kwargs") or {}).get("symbol")
                if sym and isinstance(sym, str) and sym.isdigit():
                    return [sym]
        except Exception:
            pass
        return ["005930"]

    async def _factory(params: dict):
        symbols = _resolve_symbols(params)
        broker = params.get("broker") or "kis-paper-shadow"
        duration = params.get("duration") or "0"
        argv = ["--symbols", ",".join(symbols), "--broker", broker, "--duration", duration]
        args = parse_args(argv)
        config = _build_config(args)
        if args.feed == "mock":
            config.mock_ticks = _build_mock_ticks(args.symbols, args.mock_bars)
        else:
            config.kis_client = _build_kis_client(args.feed, args.symbols)
        kis_adapter = _build_kis_adapter(args.broker)
        duration_sec = _parse_duration(args.duration)
        await _run_pipeline_attached(state, config, kis_adapter, logger, duration_sec)

    return _factory


async def _run_pipeline(config, kis_adapter, dashboard_port: int, logger,
                       duration_sec: float, auto_open_browser: bool = True):
    from dataclasses import asdict
    from src.dashboard.app import DashboardState
    from src.dashboard.timeline_broker import TimelineBroker
    from src.live.pnl_aggregator import PnLAggregator
    from src.live.strategy_position_store import StrategyPositionStore

    timeline_broker = TimelineBroker()
    # #192: per-strategy position store, fed by every order_filled WAL event.
    # #194: realized PnL aggregator (cum / daily / monthly / by_strategy).
    # Replay any pre-existing WAL so that a daemon restart preserves both.
    position_store = StrategyPositionStore()
    pnl_aggregator = PnLAggregator()
    if config.wal_path and Path(config.wal_path).exists():
        position_store.replay_from_wal(config.wal_path)
        pnl_aggregator.replay_from_wal(config.wal_path)
    dashboard_state = DashboardState(
        timeline_broker=timeline_broker,
        wal_path=config.wal_path,
    )
    dashboard_state.position_provider = position_store.get_positions
    dashboard_state.pnl_aggregator = pnl_aggregator

    def _wal_observer(ev) -> None:
        timeline_broker.publish(asdict(ev))
        position_store.ingest_fill_event(ev.event_type, ev.payload or {})
        pnl_aggregator.ingest_fill_event(ev.event_type, ev.payload or {})

    config.wal_observer = _wal_observer

    # #227 S3: env-gated Live Universe Scanner — when LIVE_SCANNER_ENABLED=1,
    # construct LivePositionRiskManager + register exit policies for every
    # registered LiveScannerMixin strategy. Default OFF preserves legacy
    # universe-scan / single-ticker behaviour with zero impact.
    live_scanner_enabled = os.environ.get("LIVE_SCANNER_ENABLED") == "1"
    if live_scanner_enabled:
        from src.portfolio.live_position_risk import LivePositionRiskManager
        risk_mgr = LivePositionRiskManager(
            position_store=position_store,
            pnl_aggregator=pnl_aggregator,
            wal_observer=_wal_observer,
        )
        config.position_risk_manager = risk_mgr
        logger.info("LIVE_SCANNER_ENABLED=1 — LivePositionRiskManager constructed")
    else:
        risk_mgr = None
        logger.info(
            "LIVE_SCANNER_ENABLED!=1 — universe-scan / single-ticker only"
        )

    # #180: surface the live orchestrator into DashboardState so the
    # /api/strategies/{id}/toggle endpoint can call enable/disable.
    # #227 S3: also register live-scanner stop/TP policies once strategies load.
    def _on_orchestrator_ready(orch):
        setattr(dashboard_state, "orchestrator", orch)
        if risk_mgr is None:
            return
        registered = 0
        for sid, strategy in orch.strategies.items():
            if not getattr(strategy, "is_live_scanner", False):
                continue
            risk_mgr.register_strategy_policy(
                sid,
                stop_loss_pct=float(getattr(strategy, "stop_loss_pct", 0.03)),
                take_profit_pct=float(getattr(strategy, "take_profit_pct", 0.06)),
                trailing_stop_pct=getattr(strategy, "trailing_stop_pct", None),
            )
            registered += 1
            logger.info("live_scanner.policy_registered sid=%s", sid)
        logger.info(
            "live_scanner.policies_total registered=%d total_strategies=%d",
            registered, len(orch.strategies),
        )

    config.on_orchestrator_ready = _on_orchestrator_ready

    shutdown_dashboard = None
    if dashboard_port > 0:
        try:
            _task, shutdown_dashboard = _start_dashboard(
                dashboard_state, dashboard_port, logger,
            )
            if auto_open_browser:
                async def _open_after_listen():
                    await asyncio.sleep(0.8)
                    try:
                        webbrowser.open(f"http://localhost:{dashboard_port}")
                    except Exception as err:
                        logger.warning("auto-open browser failed: %s", err)
                asyncio.create_task(_open_after_listen(), name="qta-browser-open")
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
    av = sys.argv[1:] if argv is None else argv
    if "--check-bundle" in av:
        return _run_check_bundle()
    if _is_no_args(argv):
        if os.environ.get("QTA_FIRST_RUN_HELP_ONLY", "").lower() == "true":
            return _show_first_run_help()
        return _run_dashboard_only_mode()
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logger = logging.getLogger("live_run")
    yaml_path = Path(args.production_yaml)
    if not yaml_path.is_absolute():
        yaml_path = _bundle_root() / yaml_path
    _print_startup_banner(_count_strategies(yaml_path), args.dashboard_port)
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
                auto_open_browser=not args.no_browser,
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
