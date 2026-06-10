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


def _wire_balance_provider(cfg, existing=None) -> None:
    """#238 Item 9 — inject real venue balances into the SnapshotBuilder path.

    Without this the #238-Item-8 fraction→qty conversion sees equity_usdt=0
    and safely DROPS every order (inert).

    #238 follow-up root cause: a freshly-constructed AccountInfoProvider does
    its own KIS auth + balance REST call. In the live KIS daemon that call
    contends with the feed/warmup REST traffic and transiently rate-limits
    (EGW00201) → ok:False → (pre-fix) equity_krw regressed to the placeholder
    → every KIS order dropped → "0 trades", while standalone (no contention)
    the same fetch succeeds. Two-part mitigation:

      1. SnapshotBuilder now holds last-known-good equity across transient
         failures (the structural fix — applies to every path).
      2. Reuse the dashboard's already-running AccountInfoProvider when one
         is available (`existing`): its 15s cache is kept warm by the
         dashboard's own /api/account/info polling, so the snapshot path
         rides a successful cached value instead of issuing a fresh,
         contended balance call per pipeline.
    """
    if cfg is None:
        return
    if existing is not None:
        cfg.balance_provider = existing
        return
    from src.dashboard.account_info import AccountInfoProvider  # noqa: PLC0415
    cfg.balance_provider = AccountInfoProvider()


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


def _active_strategy_ids(yaml_path: Path) -> set[str]:
    """production.yaml 의 active (uncommented) strategy id set (2026-06-05).

    cross-run replay 가 disabled 전략 (commented `- id: ...`) 의 옛 fill
    이벤트까지 복원하면 store 에 옛 잔량이 살아남아 LivePositionRiskManager
    가 부풀린 qty 로 청산 발주 → broker over-shoot 으로 LONG/SHORT 뒤집기
    사고 (2026-06-05 BEATUSDT incident). 본 함수가 active set 을 반환해
    replay 가 그 set 의 sid 만 store/aggregator 에 적용하도록 한다.

    파싱 실패 / 파일 없음 → 빈 set (caller 는 fallback 으로 None 전달 처리).
    """
    try:
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return set()
        out: set[str] = set()
        for s in (data.get("strategies") or []):
            sid = (s or {}).get("id")
            if isinstance(sid, str) and sid:
                out.add(sid)
        return out
    except Exception:
        return set()


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


def _attach_rotating_file_log() -> None:
    """모든 logging 출력을 회전 파일(``logs/live_run.log``)에도 기록 (2026-06-11).

    콘솔 stdout 이 휘발돼 매번 붙여넣어야 하던 불편 해소 — 사용자는 그냥
    ``python scripts/live_run.py`` 만 치면 되고 로그가 자동 누적·회전된다.
    20MB×5 (=최대 ~100MB) 회전. ``logs/`` 는 gitignore. 파일 로그 실패가
    거래를 막지 않도록 전부 graceful.
    """
    import logging  # noqa: PLC0415
    from logging.handlers import RotatingFileHandler  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    try:
        root = logging.getLogger()
        if any(getattr(h, "_qta_file_log", False) for h in root.handlers):
            return  # 중복 부착 방지 (두 모드/재진입)
        Path("logs").mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            "logs/live_run.log", maxBytes=20_000_000, backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        fh._qta_file_log = True  # type: ignore[attr-defined]  # 중복 가드 마커
        root.addHandler(fh)
        logging.getLogger("live_run").info(
            "rotating file log → logs/live_run.log (20MB×5, 자동 회전)"
        )
    except Exception:  # noqa: BLE001 — 파일 로그 실패가 거래를 막으면 안 됨
        pass


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
    # 2026-05-21: standalone 모드에서 pipeline 의 logger.info 가 root logger
    # 로 빠지고 핸들러가 없어 stdout 에 안 뜨던 문제 수정 — 거래 시작 후
    # 로그가 0 줄이라 "안 돌아가나" 오해 유발. basicConfig 으로 stdout 에
    # 강제 송출 (uvicorn 의 log_level=warning 은 별도 logger 라 영향 X).
    import logging  # noqa: PLC0415
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # 2026-06-05 — httpx INFO 폭주 차단 (universe-quote refresh 100+ REST).
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _attach_rotating_file_log()  # 2026-06-11 — 콘솔 로그 → logs/live_run.log 자동 누적
    # 거래 시작 시 실제로 어느 broker 로 갈지 미리 알려준다 (#$$$).
    _env_broker = os.environ.get("QTA_DEFAULT_BROKER", "").strip()
    if _env_broker:
        print(f"기본 broker (QTA_DEFAULT_BROKER): {_env_broker}")
    else:
        _smoke = os.environ.get("SMOKE_TEST_ENABLED", "").lower() in ("1","true","yes")
        _fallback = "binance-testnet-shadow" if _smoke else "kis-paper-shadow"
        print(
            f"기본 broker (QTA_DEFAULT_BROKER 미설정 → fallback): {_fallback}\n"
            f"  → .env 의 QTA_DEFAULT_BROKER 가 안 잡혔다면 .env 위치/구문 확인 필요."
        )
    print("거래 시작: 대시보드 'KIS 카드 → 거래 시작' 버튼 또는 별도 cmd:")
    print("  python scripts/live_run.py --symbols BTCUSDT --broker binance-testnet-shadow --feed binance")
    print("종료: 이 콘솔창에서 Ctrl+C")
    print()

    async def _serve() -> None:
        import uvicorn  # noqa: PLC0415
        from src.dashboard.app import DashboardState, create_app  # noqa: PLC0415
        from src.dashboard.timeline_broker import TimelineBroker  # noqa: PLC0415
        from src.dashboard.run_controller import RunController  # noqa: PLC0415
        from src.dashboard.account_info import AccountInfoProvider  # noqa: PLC0415
        from src.dashboard.ops_counters import OpsCounters  # noqa: PLC0415
        from src.live.pnl_aggregator import PnLAggregator  # noqa: PLC0415
        from src.live.strategy_position_store import StrategyPositionStore  # noqa: PLC0415

        # #182/#194 — pre-wire pnl_aggregator + position_store + ops_counters so
        # the dashboard's "거래 시작" button gives the same observability as the
        # CLI path (_run_pipeline). Without this, PnL gauges stay at 0, timeline
        # replay returns empty, and the ops 진단 card stays at "조회 중…" even
        # when trades are firing.
        position_store = StrategyPositionStore()
        pnl_aggregator = PnLAggregator()
        ops_counters = OpsCounters()
        state = DashboardState(timeline_broker=TimelineBroker(), wal_path=None)
        # #238 follow-up Issue 2 — standalone dashboard (no active pipeline)
        # must still show PERMANENT cross-run trade history. Seed log_dir to
        # the `--log-dir` default so prior runs surface immediately on boot
        # (a later pipeline overwrites with its own).
        # 2026-06-05 — default 변경 logs/live → logs/shadow-bitget 와 일치
        # (Bitget 이전 후 표준 broker). 옛 logs/live 도 있으면 동시 surface.
        state.log_dir = Path("logs/shadow-bitget")
        state.position_provider = position_store.get_positions
        state.pnl_aggregator = pnl_aggregator
        state.ops_counters = ops_counters
        state.run_controller = RunController(
            _build_pipeline_factory(
                state, logging.getLogger("qta-pipeline"),
                position_store=position_store, pnl_aggregator=pnl_aggregator,
                ops_counters=ops_counters,
            )
        )
        state.account_info_provider = AccountInfoProvider()

        # Standalone 대시보드용 in-process orchestrator pre-build (2026-05-20).
        # 인자 없이 띄운 standalone 모드는 trading loop 가 없어서 예전엔
        # state.orchestrator=None → 카탈로그 토글이 전부 "no-runtime" 으로
        # 읽기전용이 됐다. 카드 토글로 전략 ON/OFF 가 가능하도록 production.yaml
        # 로부터 미리 orch 를 빌드해 attach 한다. "거래 시작" 클릭 시
        # _run_pipeline_attached 가 자체 orch 로 덮어쓰므로 trading lifecycle
        # 에는 영향 없음. 빌드 실패 시 warn 하고 None 유지 (graceful, never 500).
        try:
            from portfolio.config_loader import load_orchestrator_from_yaml  # noqa: PLC0415
            from risk.dsl import Policy  # noqa: PLC0415
            state.orchestrator = load_orchestrator_from_yaml(
                yaml_path,
                Policy(policy_version=1, name="dashboard"),
                on_metalabeler_missing="skip",
            )
            # Dynamic Universe Architecture Phase 1 (2026-05-28) —
            # universe quote provider 는 state.orchestrator 를 lookup. _factory
            # 가 거래 시작 시 별도 Namespace 를 만들고 _on_orchestrator_ready 에서
            # `<ns>._orchestrator` 를 따로 set 하므로 standalone pre-build 경로
            # 에서는 별도 attach 불필요 (universe provider 도 거래 시작 후에만
            # 호출됨). 과거 한 줄짜리 attach 시도는 이 함수에 그 Namespace 가
            # 존재하지 않아 NameError → try/except 로 silent skipped 되던
            # 데드코드였음 (PR #336/#337 회귀). 제거.
            print(
                f"[qta] dashboard orch attached "
                f"({len(state.orchestrator.strategies)} strategies — toggles actionable)"
            )
        except Exception as err:
            print(f"[qta] dashboard orch pre-build skipped: {err!r}")

        # cs-tsmom-crypto-daily 신호 페이지 backend (2026-05-20).
        # production wiring 무관, dashboard 가 자체적으로 30종목 일봉 fetch +
        # 12-1m momentum + cross-sectional 랭킹. 첫 호출에서만 fetch(~10s),
        # 이후 1h TTL 캐시. parquet 디스크 캐시도 같이 잡혀 재시작 후 빠름.
        try:
            from src.dashboard.cs_tsmom_signals import CsTsmomComputer  # noqa: PLC0415
            state.cs_tsmom_computer = CsTsmomComputer()
            print("[qta] cs-tsmom computer attached — /cs-tsmom page ready")
        except Exception as err:
            print(f"[qta] cs-tsmom computer wiring skipped: {err!r}")

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
        choices=[
            "paper-only",
            "kis-paper",
            "kis-paper-shadow",
            "binance-testnet-shadow",  # #231 S1 — Binance shadow live-daemon
            "bitget-demo",             # P4 — Bitget USDT-M Futures Demo (paper trading)
            "bitget-mainnet",          # P4 — Bitget USDT-M Futures mainnet (real money)
            "smoke-dual",              # smoke test — KIS paper 005930 + Binance testnet BTCUSDT 동시
        ],
        default="bitget-demo",
        help="브로커 모드 (default: bitget-demo — 2026-06-05 부터 Bitget 이전. "
             "Binance 로 돌리려면 --broker binance-testnet-shadow). KIS 모의 가동은 "
             "--broker kis-paper-shadow. bitget-mainnet 은 실거래 (1주 demo 검증 후). "
             "smoke-dual 은 KIS + Binance 둘 다 병렬 (SMOKE_TEST_ENABLED=1 필요). "
             "기본값 변경 이력: 2026-05-21 kis-paper-shadow → binance-testnet-shadow, "
             "2026-06-05 → bitget-demo.",
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
        "--log-dir", type=str, default="logs/shadow-bitget",
        help="로그/WAL 기본 디렉토리. 2026-06-05 default 변경: "
             "logs/live → logs/shadow-bitget (--broker bitget-demo 와 일치). "
             "Binance 모드로 돌리려면 --log-dir logs/shadow-binance 명시.",
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
        "--feed", choices=["auto", "binance", "kis", "kis-ws", "mock"], default="auto",
        help="시세 feed 선택. auto=KRX→KIS REST polling, 그 외→Binance WS. "
             "kis-ws (#231 S3) = KIS 실시간 체결가 WS — 200종목 동시 subscribe "
             "(단일 connection 40종 제한 → 5 connection rotation 또는 batched).",
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


def _build_binance_adapter(broker_mode: str):
    """#231 S1 — broker_mode 가 binance-testnet-shadow 면 Binance Futures testnet
    adapter 생성. 별도 컨테이너 qta-live-daemon-binance 에서 호출.

    Env vars (fallback chain — src/dashboard/account_info.py 동일 표준):
      API key:    BINANCE_DEMO_API_KEY → BINANCE_TESTNET_API_KEY → BINANCE_API_KEY
      API secret: BINANCE_DEMO_API_SECRET → BINANCE_TESTNET_API_SECRET → BINANCE_SECRET_KEY
      Base URL:   BINANCE_BASE_URL (default: https://testnet.binancefuture.com)

    KIS / paper-only 모드면 None 반환.
    """
    if broker_mode != "binance-testnet-shadow":
        return None
    from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter

    def _strip(v):
        return (v or "").strip().strip('"').strip("'")
    api_key = _strip(
        os.environ.get("BINANCE_DEMO_API_KEY")
        or os.environ.get("BINANCE_TESTNET_API_KEY")
        or os.environ.get("BINANCE_API_KEY")
    )
    # #238 — `BINANCE_DEMO__SECRET_API_KEY` (이중 underscore) + `BINANCE_DEMO_SECRET_API_KEY`
    # 도 검출. account_info.py 가 이미 사용하는 fallback chain 과 일치시켜야 dashboard
    # 잔고 조회와 거래 adapter 가 같은 secret 을 사용 — mismatch 시 Binance -1022
    # "Signature invalid" 폭주 발생.
    secret = _strip(
        os.environ.get("BINANCE_DEMO__SECRET_API_KEY")
        or os.environ.get("BINANCE_DEMO_SECRET_API_KEY")
        or os.environ.get("BINANCE_DEMO_API_SECRET")
        or os.environ.get("BINANCE_TESTNET_API_SECRET")
        or os.environ.get("BINANCE_API_SECRET")
        or os.environ.get("BINANCE_SECRET_KEY")
    )
    base_url = os.environ.get(
        "BINANCE_BASE_URL", "https://testnet.binancefuture.com",
    )
    # NOTE: AsyncBinanceUserDataStream builds `{ws_base_url}/{listenKey}` — the
    # base MUST include the `/ws` user-data path or Binance returns HTTP 404
    # (testnet user-data WS = wss://stream.binancefuture.com/ws/<listenKey>).
    ws_base_url = os.environ.get(
        "BINANCE_WS_BASE_URL", "wss://stream.binancefuture.com/ws",
    )
    missing = [
        k for k, v in [
            ("BINANCE_DEMO_API_KEY (or BINANCE_TESTNET_API_KEY / BINANCE_API_KEY)", api_key),
            ("BINANCE_DEMO_API_SECRET (or BINANCE_TESTNET_API_SECRET / BINANCE_SECRET_KEY)", secret),
        ] if not v
    ]
    if missing:
        raise SystemExit(
            f"broker_mode='{broker_mode}' requires env vars: {', '.join(missing)}"
        )
    return AsyncBinanceFuturesAdapter(
        api_key=api_key,
        secret=secret,
        base_url=base_url,
        ws_base_url=ws_base_url,
        paper=True,
    )


def _build_bitget_adapter(broker_mode: str):
    """P4 — Bitget USDT-M Futures adapter (Demo or Mainnet).

    Env vars (single chain — no fallbacks; Bitget API key has 3 components):
      API key:    BITGET_DEMO_API_KEY (paper) / BITGET_API_KEY (live)
      API secret: BITGET_DEMO_SECRET  (paper) / BITGET_API_SECRET (live)
      Passphrase: BITGET_DEMO_PASSPHRASE (paper) / BITGET_API_PASSPHRASE (live)

    Demo trading routes via the ``paptrading: 1`` REST header and
    ``wspap.bitget.com`` WS subdomain (auto-selected by ``paper=True``).

    KIS / paper-only / Binance 모드면 None 반환.
    """
    if broker_mode not in ("bitget-demo", "bitget-mainnet"):
        return None
    # 2026-06-05 — venue 라우팅: 전략의 get_universe() 가 본 env 확인해 Bitget
    # 거래량 top-100 으로 분기. Binance 미상장 종목 호출 → 400 폭주 + REST
    # rate-limit 낭비 root cause fix. Binance 모드는 env 미설정 → 기존 byte-
    # identical.
    os.environ["QTA_BROKER_VENUE"] = "bitget"
    from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter

    paper = broker_mode == "bitget-demo"

    def _strip(v):
        return (v or "").strip().strip('"').strip("'")

    if paper:
        api_key = _strip(os.environ.get("BITGET_DEMO_API_KEY"))
        secret = _strip(os.environ.get("BITGET_DEMO_SECRET"))
        passphrase = _strip(os.environ.get("BITGET_DEMO_PASSPHRASE"))
        var_names = ["BITGET_DEMO_API_KEY", "BITGET_DEMO_SECRET", "BITGET_DEMO_PASSPHRASE"]
    else:
        api_key = _strip(os.environ.get("BITGET_API_KEY"))
        secret = _strip(os.environ.get("BITGET_API_SECRET"))
        passphrase = _strip(os.environ.get("BITGET_API_PASSPHRASE"))
        var_names = ["BITGET_API_KEY", "BITGET_API_SECRET", "BITGET_API_PASSPHRASE"]

    missing = [name for name, val in zip(var_names, (api_key, secret, passphrase)) if not val]
    if missing:
        raise SystemExit(
            f"broker_mode='{broker_mode}' requires env vars: {', '.join(missing)}"
        )

    return AsyncBitgetFuturesAdapter(
        api_key=api_key,
        secret=secret,
        passphrase=passphrase,
        paper=paper,
    )


def _build_universe_quote_provider(broker_mode: str, kis_client, args):
    """#231 S2 — broker_mode 기반 universe OHLCV provider 빌드.

    SnapshotBuilder 에 주입되어 cs_async_wrapper 등 universe-scan 전략이
    매 build_snapshot 마다 universe ohlcv_history 를 받게 함. TTL=300s
    cache 가 호출 빈도 제한.

    KIS 모드:    fetch_universe_snapshot(KIS REST, KOSPI200 + KOSDAQ150)
    Binance:    fetch_universe_klines(Binance public, top-30 USDT)
    paper-only / no-client: None — graceful hold path 유지.
    """
    if broker_mode in ("kis-paper", "kis-paper-shadow") and kis_client is not None:
        from src.brokers.kis.universe_quote import fetch_universe_snapshot
        from src.universe.krx_pool import get_pool_codes
        import datetime

        def _kis_provider():
            try:
                symbols = get_pool_codes(n=350)
                today = datetime.date.today()
                start = (today - datetime.timedelta(days=365)).strftime("%Y%m%d")
                end = today.strftime("%Y%m%d")
                return fetch_universe_snapshot(kis_client, symbols, start, end)
            except Exception:
                return {}
        return _kis_provider

    if broker_mode == "binance-testnet-shadow":
        from src.brokers.binance.universe_quote import fetch_universe_klines
        from src.portfolio.binance_universe import get_universe as _binance_top30
        # cs-tsmom-crypto-daily universe — dashboard / live / backtest 단일 소스
        # ``src/portfolio/binance_universe.py``. 갱신 시 그 파일 한 곳만 수정.
        #
        # 2026-05-28 Dynamic Universe Architecture Phase 1
        # (docs/specs/dynamic-universe-architecture.md):
        # - orchestrator 의 active 전략들에서 get_universe / get_interval 수집
        #   → per-interval union 으로 fetch.
        # - orchestrator 미주입 (legacy) 시 TOP30 / 1d — byte-identical 보존.
        #
        # orchestrator 는 caller (live_run main flow) 가 ``args._orchestrator``
        # 로 늦게 attach. provider 가 closure 안에서 매 호출 시 lookup —
        # provider 생성 시점에는 None 이어도 OK.
        def _get_orchestrator():
            return getattr(args, "_orchestrator", None)

        def _collect_strategy_universes() -> dict[str, set[str]]:
            """interval → symbol set. orchestrator 미주입 시 빈 dict."""
            orchestrator = _get_orchestrator()
            if orchestrator is None or not hasattr(orchestrator, "_strategies"):
                return {}
            out: dict[str, set[str]] = {}
            for strat in orchestrator._strategies.values():
                get_u = getattr(strat, "get_universe", None)
                get_i = getattr(strat, "get_interval", None)
                if not (callable(get_u) and callable(get_i)):
                    continue
                try:
                    syms = list(get_u())
                    interval = str(get_i())
                except Exception:
                    continue
                if not syms or not interval:
                    continue
                out.setdefault(interval, set()).update(syms)
            return out

        def _binance_provider():
            try:
                per_interval = _collect_strategy_universes()
                if not per_interval:
                    return fetch_universe_klines(_binance_top30(), interval="1d")
                # 같은 symbol 다른 interval 일 경우 first-wins (interval 알파벳).
                # cs-tsmom (1d) + airborne (1h) 가 겹치는 symbol — 1d 가 먼저
                # 들어가 cs-tsmom 회귀 X. Phase 3 에서 symbol-major 분리 검토.
                ohlcv: dict = {}
                for interval in sorted(per_interval.keys()):
                    syms = sorted(per_interval[interval])
                    try:
                        partial = fetch_universe_klines(syms, interval=interval)
                    except Exception:
                        continue
                    for sym, df in partial.items():
                        ohlcv.setdefault(sym, df)
                return ohlcv
            except Exception:
                return {}
        return _binance_provider

    if broker_mode in ("bitget-demo", "bitget-mainnet"):
        # P4 — Bitget universe quote provider. Same closure pattern as Binance
        # (per-interval union from orchestrator strategies). Fallback when no
        # orchestrator attached: top-30 USDT pairs ≈ Binance baseline.
        from src.brokers.bitget.universe_quote import fetch_universe_klines as _bg_fetch
        from src.portfolio.binance_universe import get_universe as _binance_top30  # symbol shared

        def _bg_get_orchestrator():
            return getattr(args, "_orchestrator", None)

        def _bg_collect_strategy_universes() -> dict[str, set[str]]:
            orchestrator = _bg_get_orchestrator()
            if orchestrator is None or not hasattr(orchestrator, "_strategies"):
                return {}
            out: dict[str, set[str]] = {}
            for strat in orchestrator._strategies.values():
                get_u = getattr(strat, "get_universe", None)
                get_i = getattr(strat, "get_interval", None)
                if not (callable(get_u) and callable(get_i)):
                    continue
                try:
                    syms = list(get_u())
                    interval = str(get_i())
                except Exception:
                    continue
                if not syms or not interval:
                    continue
                # 1000PEPEUSDT / 1000SHIBUSDT 매핑 (Bitget 은 1코인 단위라 multiplier 없음).
                # P0 사전조사 결과: 우리 universe 의 1000X 종목만 매핑 필요.
                normalized = [
                    s.removeprefix("1000") if s.startswith("1000") and s.endswith("USDT") else s
                    for s in syms
                ]
                out.setdefault(interval, set()).update(normalized)
            return out

        def _bitget_provider():
            try:
                per_interval = _bg_collect_strategy_universes()
                if not per_interval:
                    return _bg_fetch(_binance_top30(), interval="1d")
                ohlcv: dict = {}
                for interval in sorted(per_interval.keys()):
                    syms = sorted(per_interval[interval])
                    try:
                        partial = _bg_fetch(syms, interval=interval)
                    except Exception:
                        continue
                    for sym, df in partial.items():
                        ohlcv.setdefault(sym, df)
                return ohlcv
            except Exception:
                return {}
        return _bitget_provider

    return None


def _build_kis_client(feed_mode: str, symbols: list[str]):
    """Build a sync KISClient for REST polling/warmup when KRX feed is active.

    Returns None when no KIS REST surface is required (mock/binance feed, no
    KRX symbols). Failures to construct (e.g. missing env vars) are surfaced
    as SystemExit so EXE smoke runs fail loudly.
    """
    # #231 S3: kis-ws 도 KISClient 필요 (warmup REST + auth source).
    needs_kis = feed_mode in ("kis", "kis-ws") or (
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


def _register_exit_policies(orch, risk_mgr, logger) -> int:
    """Register a LivePositionRiskManager StopTpPolicy for every strategy
    that needs auto stop/TP — returns the number registered.

    Two sources of thresholds (#227 S3 + #238):
      1. live-scanner strategies (``is_live_scanner`` truthy) — read the
         LiveScannerMixin class attrs, default 0.03 / 0.06 if absent.
      2. single-ticker / universe-scan strategies that EXPLICITLY declare
         ``stop_loss_pct`` AND ``take_profit_pct`` instance/class attrs
         (e.g. momo-btc-v2 via production.yaml kwargs). Root incident:
         momo opened a naked -1 BTC short with ZERO auto-stop because the
         old loop skipped every non-live-scanner strategy outright.

    A strategy that is neither a live-scanner nor declares both stop AND
    take_profit is skipped — we never invent thresholds.
    """
    registered = 0
    for sid, strategy in orch.strategies.items():
        if getattr(strategy, "is_live_scanner", False):
            risk_mgr.register_strategy_policy(
                sid,
                stop_loss_pct=float(getattr(strategy, "stop_loss_pct", 0.03)),
                take_profit_pct=float(getattr(strategy, "take_profit_pct", 0.06)),
                trailing_stop_pct=getattr(strategy, "trailing_stop_pct", None),
            )
            registered += 1
            logger.info("live_scanner.policy_registered sid=%s", sid)
            continue
        # Non-live-scanner: only if it explicitly declares stop AND TP.
        sl = getattr(strategy, "stop_loss_pct", None)
        tp = getattr(strategy, "take_profit_pct", None)
        if sl is None or tp is None:
            continue
        risk_mgr.register_strategy_policy(
            sid,
            stop_loss_pct=float(sl),
            take_profit_pct=float(tp),
            trailing_stop_pct=getattr(strategy, "trailing_stop_pct", None),
        )
        registered += 1
        logger.info("single_ticker.policy_registered sid=%s", sid)
    logger.info(
        "exit_policies.total registered=%d total_strategies=%d",
        registered, len(orch.strategies),
    )
    return registered


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
    *, binance_adapter=None, bitget_adapter=None,
    position_store=None, pnl_aggregator=None,
    ops_counters=None, args=None,
) -> None:
    """이미 떠있는 dashboard 에 attach — 거래만 시작 (#182 단계 2).

    dashboard 재기동 없이 state.timeline_broker 를 WAL fan-out observer 로 와이어링한 뒤
    run_shadow_loop 호출. RunController 가 이 함수를 background task 로 돌린다.

    #231 S1: ``binance_adapter`` 는 broker_mode=binance-testnet-shadow 시 사용.
    keyword-only 라 기존 호출자 (positional kis_adapter only) 영향 zero.

    position_store / pnl_aggregator 가 주어지면 _run_pipeline 과 동일한 fan-out 패턴 —
    timeline_broker.publish + position_store.ingest_fill_event + pnl_aggregator.ingest_fill_event.
    이게 없으면 거래가 일어나도 대시보드 PnL/포지션이 갱신되지 않는다.
    """
    from dataclasses import asdict
    # WAL path 를 state 에 노출 — WS replay 가 이 세션의 과거 이벤트를 복원할 수 있게.
    state.wal_path = config.wal_path
    # 2026-06-05 — dashboard 가 거래시작 버튼으로 들어온 pipeline 의 log_dir
    # 을 알도록 동기화. 미설정 시 standalone 모드의 line 353 default 가
    # 영구 고정되어 dashboard 가 옛 logs/live 만 보임 (사용자 보고: bitget
    # 거래내역 / 전략별 포지션 깜깜).
    if config.wal_path is not None:
        state.log_dir = Path(config.wal_path).parent.parent
    # 2026-05-21 fix: cross-run replay 가드 완화. 이전엔 wal_path.exists() 가
    # False 면 cross-run 도 skip (= 첫 거래 직전 wal_path 미생성 케이스에서 store/
    # aggregator 가 빈 상태로 시작 → restore_live_entered 무효 → 매수 폭주).
    # 본 fix 는 wal_path 가 None 이어도 log_dir 추정해서 cross-run 시도. 메시지도
    # 항상 출력 (N=0 케이스 가시화 — 사용자 진단용).
    #
    # 2026-05-21 fix #2 (warm guard): dashboard 의 "거래 정지 → 시작" 토글마다
    # 이 함수가 다시 호출되는데, pnl_aggregator/position_store 는 _serve() 의
    # 싱글톤이라 이미 정확한 상태를 들고 있음. 그 위에 replay_from_wal_dir 가
    # 또 돌면 모든 fill 이 두 번 카운트됨 → realized PnL 누적(78→156→234) +
    # store 의 보유 qty 인플레(0.343→1.029=3x). risk manager 가 부풀린 qty
    # 로 stop 발사 → 실제론 0.343 만 있는데 1.029 sell → broker 가 short
    # 으로 뒤집힘 → 다음 tick 에 재진입 폭주. WAL 증거 (04:06:37) 확인.
    # 그래서 싱글톤이 이미 warm 이면 replay 를 skip — 프로세스 첫 부팅 한 번만 함.
    if pnl_aggregator is not None and position_store is not None:
        already_warm = (
            pnl_aggregator.realtime != 0.0
            or len(position_store.all_positions()) > 0
        )
        if already_warm:
            logger.info(
                "cross-run replay: SKIPPED (싱글톤 already warm — "
                "정지/시작 토글 재replay 방지). realized=%.4f positions=%d strategies",
                pnl_aggregator.realtime,
                len(position_store.all_positions()),
            )
        else:
            if config.wal_path:
                log_dir = Path(config.wal_path).parent.parent
            else:
                log_dir = Path("logs/live")  # fallback
            # 2026-06-05 active filter: disabled 전략 (production.yaml commented)
            # 의 옛 fill 은 store/pnl 에 누적 안 됨 — BEATUSDT/TRX 잔량 사고 방지.
            # 파싱 실패면 빈 set → None 전달 → 기존 동작 byte-identical.
            _allowed_sids = _active_strategy_ids(
                config.production_yaml if getattr(config, "production_yaml", None)
                else _bundle_root() / "configs/orchestrator/production.yaml"
            )
            _allowed_arg = _allowed_sids or None
            try:
                n_store = position_store.replay_from_wal_dir(
                    log_dir, allowed_strategy_ids=_allowed_arg,
                )
                n_pnl = pnl_aggregator.replay_from_wal_dir(
                    log_dir, allowed_strategy_ids=_allowed_arg,
                )
                logger.info(
                    "cross-run replay: %d WAL files (store=%d / pnl=%d) from %s "
                    "active_sids=%d",
                    max(n_store, n_pnl), n_store, n_pnl, log_dir,
                    len(_allowed_sids),
                )
            except Exception as err:
                logger.warning("cross-run replay failed: %s — fallback to single-path", err)
                if config.wal_path and Path(config.wal_path).exists():
                    position_store.replay_from_wal(
                        config.wal_path, allowed_strategy_ids=_allowed_arg,
                    )
                    pnl_aggregator.replay_from_wal(
                        config.wal_path, allowed_strategy_ids=_allowed_arg,
                    )

    def _wal_observer(ev) -> None:
        if state.timeline_broker is not None:
            state.timeline_broker.publish(asdict(ev))
        if position_store is not None:
            position_store.ingest_fill_event(ev.event_type, ev.payload or {})
        if pnl_aggregator is not None:
            pnl_aggregator.ingest_fill_event(ev.event_type, ev.payload or {})
        if ops_counters is not None:
            ops_counters.ingest(ev.event_type, ev.payload or {})

    config.wal_observer = _wal_observer
    # Binance live-fill gap — the executor only writes `order_acked` (Binance
    # MARKET ack = status=NEW, no price); without a fill-stream consumer NO
    # `order_filled` is ever emitted for the live Binance path → zero realized
    # P&L / positions / trades on the dashboard (제출-only, never 체결).
    # Wiring the StrategyPositionStore lets run_shadow_loop spawn that
    # consumer for binance-testnet-shadow and lets execute_intents register
    # the coid→(symbol, side, strategy_id) context it resolves. Harmless for
    # paper/kis (no fill-stream task is created without binance_adapter).
    config.position_store = position_store
    # #238 follow-up — multi-symbol mark-price cache + manual close executor.
    # Same instance the mark-price feed writes and the dashboard reads. The
    # manual-close callback parks the executor closure (built by
    # run_shadow_loop) on the dashboard state so the operator can submit
    # market orders via POST /api/strategies/{sid}/positions/{sym}/close.
    from src.live.price_cache import LivePriceCache
    price_cache = LivePriceCache()
    config.live_price_cache = price_cache
    setattr(state, "price_cache", price_cache)
    config.on_manual_close_executor_ready = lambda closure: setattr(
        state, "manual_close_executor", closure,
    )
    # #238 follow-up root cause — reuse the dashboard's already-warm
    # AccountInfoProvider (15s cache kept fresh by /api/account/info polling)
    # so the KIS snapshot path rides a successful cached balance instead of a
    # fresh contended REST call that the live KIS daemon transiently
    # rate-limits (→ equity_krw was regressing to placeholder → 0 trades).
    _wire_balance_provider(
        config, existing=getattr(state, "account_info_provider", None),
    )
    # Surface the live SnapshotBuilder so /api/venue_equity_status can show
    # which venue is INERT (real equity 미확보 → 주문 전량 보류).
    config.on_snapshot_builder_ready = lambda sb: setattr(
        state, "snapshot_builder", sb,
    )

    # #238 — dashboard 거래 시작 경로(_run_pipeline_attached)도 live-scanner
    # 자동 stop/TP 청산을 지원해야 한다. 기존엔 _run_pipeline(CLI)에만 있어
    # dashboard 버튼으로 live-scanner 를 켜면 매수만 되고 청산이 안 됐음.
    risk_mgr = None
    if os.environ.get("LIVE_SCANNER_ENABLED") == "1":
        if position_store is not None and pnl_aggregator is not None:
            from src.portfolio.live_position_risk import LivePositionRiskManager  # noqa: PLC0415
            risk_mgr = LivePositionRiskManager(
                position_store=position_store,
                pnl_aggregator=pnl_aggregator,
                wal_observer=_wal_observer,
            )
            config.position_risk_manager = risk_mgr
            logger.info(
                "LIVE_SCANNER_ENABLED=1 (attached) — LivePositionRiskManager constructed"
            )
        else:
            logger.warning(
                "LIVE_SCANNER_ENABLED=1 but position_store/pnl_aggregator 미주입 "
                "— 자동 청산 비활성. _serve() wiring 확인 필요."
            )
    else:
        logger.info(
            "LIVE_SCANNER_ENABLED!=1 (attached) — universe-scan / single-ticker only"
        )

    def _on_orchestrator_ready(orch):
        setattr(state, "orchestrator", orch)
        # Dynamic Universe Architecture Phase 1 (2026-05-28) —
        # _build_universe_quote_provider 가 args._orchestrator lookup.
        # args 는 _factory 가 명시 keyword 로 넘긴 Namespace — caller 가
        # 안 넘기면 (legacy CLI 직접 호출) skip. 과거엔 args 가 enclosing scope
        # 에 없어 매번 NameError silent skip → universe provider 가 항상 TOP30
        # × 1d 폴백 → airborne(1h, 100종목) 전략 무용지물. PR #336/#337 회귀.
        if args is not None:
            args._orchestrator = orch
        # 2026-05-20: re-entry bug fix — _live_entered 가 부팅 시 비어있어
        # 재시작 = 보유 종목 추가 매수 폭주. 이미 replay 된 store 의
        # positions 로 _live_entered 복원 → 부팅 후 첫 tick 에 보유 종목 진입 차단.
        try:
            if position_store is not None and hasattr(orch, "restore_live_entered"):
                positions = position_store.all_positions()
                n = orch.restore_live_entered(positions) or 0
                logger.info(
                    "orchestrator._live_entered restored %d entries from store "
                    "(positions=%d strategies)",
                    n, len(positions),
                )
        except Exception as err:  # noqa: BLE001 — never block startup
            logger.warning("restore_live_entered failed (attached): %s", err)
        if risk_mgr is None:
            return
        # #238 — 청산 시 orchestrator 진입 기록 해제 → live-scanner 재진입 허용.
        risk_mgr._on_exit = orch.release_live_position
        # 2026-05-21 — ATR 기반 동적 stop. Strategy 가 Signal 에 실어보낸 per-entry
        # stop/TP/trailing pct override 를 risk manager 의 (sid, sym) dynamic
        # policy 로 등록한다. 없으면 정적 policy fallback (기존 동작).
        orch._on_entry = risk_mgr.register_entry_override
        _register_exit_policies(orch, risk_mgr, logger)

    config.on_orchestrator_ready = _on_orchestrator_ready

    # 2026-05-21 — Binance broker ↔ position_store reconciler. 사용자가
    # Binance UI 에서 직접 close 했을 때 store 가 모르고 phantom long 보유 →
    # stop fire → broker SHORT 진입 사이클을 차단. WAL + dashboard timeline
    # 양쪽으로 알림. single-holder mismatch 는 auto-fix, 그 외는 알림만.
    # P4b — Bitget 도 동일 reconciler 사용 (broker.get_positions 시그니처 동일).
    reconciler_task: asyncio.Task | None = None
    reconciler_stop = asyncio.Event()
    _live_broker_adapter = binance_adapter or bitget_adapter
    if _live_broker_adapter is not None and position_store is not None:
        from src.live.position_reconciler import PositionReconciler  # noqa: PLC0415

        def _sync_orch_live_entered(sid: str, symbol: str, qty) -> None:
            # 2026-05-22 — reconciler auto-fix 가 store qty 를 바꾸면
            # orchestrator._live_entered 도 정합. 미정합 시 청산된 종목이
            # 영구 진입 차단된다 (재시작 후 11시간 매수 0 의 원인).
            orch = getattr(state, "orchestrator", None)
            if orch is not None and hasattr(orch, "sync_live_entered"):
                orch.sync_live_entered(sid, symbol, float(qty))

        reconciler = PositionReconciler(
            position_store=position_store,
            broker=_live_broker_adapter,
            wal_observer=_wal_observer,
            alert_publisher=(
                (lambda p: state.timeline_broker.publish(p))
                if state.timeline_broker is not None else None
            ),
            on_position_synced=_sync_orch_live_entered,
            tol=Decimal("0.001"),
            interval_sec=float(os.environ.get("QTA_RECONCILE_INTERVAL_SEC", "60")),
        )
        reconciler_task = asyncio.create_task(
            reconciler.run_loop(reconciler_stop), name="qta-position-reconciler",
        )
        logger.info("PositionReconciler started (attached)")

    try:
        await _run_with_duration(
            run_shadow_loop(
                config,
                kis_adapter=kis_adapter,
                binance_adapter=binance_adapter,
                bitget_adapter=bitget_adapter,
            ),
            duration_sec,
        )
    finally:
        if reconciler_task is not None:
            reconciler_stop.set()
            try:
                await asyncio.wait_for(reconciler_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                reconciler_task.cancel()


def _build_pipeline_factory(
    state, logger, *, position_store=None, pnl_aggregator=None, ops_counters=None,
):
    """RunController 의 pipeline_factory — 대시보드 시작 버튼 클릭 시 호출 (#182).

    params: {symbols?: list[str] | str, broker?: str, duration?: str}

    position_store / pnl_aggregator 가 _serve() 에서 주입된 경우 _run_pipeline_attached
    가 그것들에 fill 이벤트를 fan-out — 거래 시작 → 대시보드 PnL/포지션 즉시 가시화.
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
        smoke_on = os.environ.get("SMOKE_TEST_ENABLED", "").lower() in ("1", "true", "yes")
        broker = params.get("broker")
        if not broker:
            # 우선순위:
            #   1. params.broker (dashboard 가 명시 전달 시)
            #   2. QTA_DEFAULT_BROKER env (운영자가 dashboard 버튼 default 지정)
            #   3. SMOKE_TEST_ENABLED=1 → binance-testnet-shadow (smoke 통로검증)
            #   4. fallback → bitget-demo (2026-06-05 Bitget 이전 — 이전엔
            #      kis-paper-shadow 였음)
            env_broker = os.environ.get("QTA_DEFAULT_BROKER", "").strip()
            if env_broker:
                broker = env_broker
            elif smoke_on:
                broker = "binance-testnet-shadow"
            else:
                broker = "bitget-demo"
        duration = params.get("duration") or "0"

        if broker == "smoke-dual":
            await _run_smoke_dual(
                state, logger, duration,
                position_store=position_store, pnl_aggregator=pnl_aggregator,
                ops_counters=ops_counters,
            )
            return

        symbols = _resolve_symbols(params)
        # #238 — Binance broker 면 BTCUSDT 강제 (KRX 코드 fallback 회피). smoke 여부
        # 무관 — binance-testnet-shadow 인데 005930 fallback 되면 feed 가 비어 무의미.
        if broker == "binance-testnet-shadow" and not params.get("symbols"):
            symbols = ["BTCUSDT"]
        # 2026-06-05 — Bitget broker 면 *현재 열린 포지션* + 핵심 10 종목. mark-price
        # feed 가 그 종목들 구독해야 TP/SL 평가 가능 — 미구독이면 DOT 같은
        # 보유 포지션의 TP fire 자체 안 됨 (사용자 사고 root cause).
        if broker in ("bitget-demo", "bitget-mainnet") and not params.get("symbols"):
            _open: set[str] = set()
            try:
                import asyncio as _asyncio  # noqa: PLC0415
                _bg = _build_bitget_adapter(broker)
                if _bg is not None:
                    _ps = await _bg.get_positions()
                    for _p in _ps:
                        if getattr(_p, "qty", 0) and float(getattr(_p, "qty", 0)) != 0:
                            _open.add(_p.symbol)
                    await _bg.aclose()
            except Exception:  # noqa: BLE001
                pass
            symbols = sorted(_open | {
                "BTCUSDT", "ETHUSDT", "SOLUSDT", "DOGEUSDT", "XRPUSDT",
                "ADAUSDT", "BNBUSDT", "LINKUSDT", "DOTUSDT", "AVAXUSDT",
            })
        # 2026-05-21: 거래 시작 시 실제 어디로 가는지 stdout 에 명시 — bars=0
        # 인데 어디 막혔는지 진단 즉시 가능 (.env 미로드 / 한국장 마감 등).
        print(
            f"[qta] 거래 시작 — broker={broker} symbols={symbols} duration={duration}",
            flush=True,
        )
        # #238 — Binance broker 에서는 KIS REST 진입 자체를 막아야 함 (feed=binance).
        extra_argv: list[str] = []
        if broker == "binance-testnet-shadow":
            extra_argv = ["--feed", "binance"]
        argv = ["--symbols", ",".join(symbols), "--broker", broker, "--duration", duration, *extra_argv]
        args = parse_args(argv)
        config = _build_config(args)
        if args.feed == "mock":
            config.mock_ticks = _build_mock_ticks(args.symbols, args.mock_bars)
        else:
            config.kis_client = _build_kis_client(args.feed, args.symbols)
        kis_adapter = _build_kis_adapter(args.broker)
        binance_adapter = _build_binance_adapter(args.broker)  # #231 S1
        bitget_adapter = _build_bitget_adapter(args.broker)    # P4b
        # #231 S2 — universe-scan strategies 가 live dispatch 되도록 provider wire.
        # #238 — smoke 검증 중에는 350종목 universe fetch 가 KIS 초당 한도를 폭주
        # 시키므로 SMOKE_TEST_ENABLED=1 이면 provider 미주입 (cs-* 전략은 hold 폴백).
        if smoke_on:
            config.universe_quote_provider = None
        else:
            config.universe_quote_provider = _build_universe_quote_provider(
                args.broker, config.kis_client, args,
            )
        duration_sec = _parse_duration(args.duration)
        await _run_pipeline_attached(
            state, config, kis_adapter, logger, duration_sec,
            binance_adapter=binance_adapter,
            bitget_adapter=bitget_adapter,
            position_store=position_store, pnl_aggregator=pnl_aggregator,
            ops_counters=ops_counters,
            args=args,
        )

    return _factory


async def _run_smoke_dual(
    state, logger, duration: str,
    *, position_store=None, pnl_aggregator=None, ops_counters=None,
) -> None:
    """smoke-dual broker — KIS paper 005930 + Binance testnet BTCUSDT 병렬 실행.

    "통로만 뚫어두는" 검증용. 두 개의 독립 `run_shadow_loop` 가 분리된 WAL 파일에
    이벤트를 적재하지만, observability (timeline broker / pnl / position / ops)
    는 공유 → 대시보드 한 화면에서 양쪽 거래 즉시 확인.

    SMOKE_TEST_ENABLED env 미설정 시에도 entry 자체는 작동 (전략이 hold 만 반환).
    """
    if not os.environ.get("SMOKE_TEST_ENABLED"):
        logger.warning(
            "smoke-dual: SMOKE_TEST_ENABLED env 미설정 — 두 broker 가 연결되어도 "
            "smoke 전략이 hold 만 반환합니다. .env 에 SMOKE_TEST_ENABLED=1 추가 후 재시작."
        )

    run_id = _build_run_id()

    def _wal_observer(ev):
        from dataclasses import asdict as _asdict  # noqa: PLC0415
        if state.timeline_broker is not None:
            state.timeline_broker.publish(_asdict(ev))
        if position_store is not None:
            position_store.ingest_fill_event(ev.event_type, ev.payload or {})
        if pnl_aggregator is not None:
            pnl_aggregator.ingest_fill_event(ev.event_type, ev.payload or {})
        if ops_counters is not None:
            ops_counters.ingest(ev.event_type, ev.payload or {})

    # KIS branch — 005930 paper-shadow + KIS REST feed.
    kis_argv = [
        "--symbols", "005930", "--broker", "kis-paper-shadow",
        "--feed", "auto", "--duration", duration,
        "--run-id", f"{run_id}-kis", "--schedule", "always",
        "--dashboard-port", "0",
    ]
    kis_args = parse_args(kis_argv)
    kis_config = _build_config(kis_args)
    try:
        kis_config.kis_client = _build_kis_client(kis_args.feed, kis_args.symbols)
        kis_adapter = _build_kis_adapter(kis_args.broker)
    except SystemExit as err:
        logger.error("smoke-dual KIS branch skipped — %s", err)
        kis_config = None
        kis_adapter = None
    if kis_config is not None:
        kis_config.wal_observer = _wal_observer
        # #238 follow-up root cause — reuse the dashboard's warm provider.
        _wire_balance_provider(
            kis_config, existing=getattr(state, "account_info_provider", None),
        )
        kis_config.on_snapshot_builder_ready = lambda sb: setattr(
            state, "snapshot_builder", sb,
        )
        state.wal_path = kis_config.wal_path  # primary for WS replay
        # Issue 2 — permanent cross-run history for the smoke-dual path too.
        if kis_config.wal_path is not None:
            state.log_dir = Path(kis_config.wal_path).parent.parent

    # Binance branch — BTCUSDT testnet-shadow + Binance public WS feed.
    bnb_argv = [
        "--symbols", "BTCUSDT", "--broker", "binance-testnet-shadow",
        "--feed", "binance", "--duration", duration,
        "--run-id", f"{run_id}-binance", "--schedule", "always",
        "--dashboard-port", "0",
    ]
    bnb_args = parse_args(bnb_argv)
    bnb_config = _build_config(bnb_args)
    try:
        binance_adapter = _build_binance_adapter(bnb_args.broker)
    except SystemExit as err:
        logger.error("smoke-dual Binance branch skipped — %s", err)
        bnb_config = None
        binance_adapter = None
    if bnb_config is not None:
        bnb_config.wal_observer = _wal_observer
        # Binance live-fill gap — only the Binance branch gets the fill-stream
        # consumer (the KIS branch is PaperBroker, which emits its own
        # order_filled; setting position_store there would NOT create a
        # consumer anyway since no binance_adapter is passed, but we keep it
        # scoped to the Binance branch for clarity).
        bnb_config.position_store = position_store
        _wire_balance_provider(
            bnb_config, existing=getattr(state, "account_info_provider", None),
        )
        # Surface to /api/trades so the dashboard trade-history card sees both.
        if state.wal_path is None:
            state.wal_path = bnb_config.wal_path
        else:
            state.extra_wal_paths = [bnb_config.wal_path]

    coros = []
    if kis_config is not None:
        coros.append(run_shadow_loop(kis_config, kis_adapter=kis_adapter))
    if bnb_config is not None:
        coros.append(run_shadow_loop(bnb_config, binance_adapter=binance_adapter))
    if not coros:
        logger.error(
            "smoke-dual: 두 branch 모두 자격증명 누락. .env 의 HANTOO_FAKE_* / "
            "BINANCE_DEMO_API_KEY 를 확인하세요."
        )
        return

    logger.info(
        "smoke-dual starting: branches=%d run_id=%s wal_primary=%s wal_extra=%s",
        len(coros), run_id, state.wal_path,
        [str(p) for p in state.extra_wal_paths or []],
    )
    duration_sec = _parse_duration(duration)
    await _run_with_duration(asyncio.gather(*coros), duration_sec)


async def _run_pipeline(config, kis_adapter, dashboard_port: int, logger,
                       duration_sec: float, auto_open_browser: bool = True,
                       *, binance_adapter=None, bitget_adapter=None, args=None):
    from dataclasses import asdict
    from src.dashboard.app import DashboardState
    from src.dashboard.ops_counters import OpsCounters
    from src.dashboard.timeline_broker import TimelineBroker
    from src.live.pnl_aggregator import PnLAggregator
    from src.live.strategy_position_store import StrategyPositionStore

    timeline_broker = TimelineBroker()
    # #192: per-strategy position store, fed by every order_filled WAL event.
    # #194: realized PnL aggregator (cum / daily / monthly / by_strategy).
    # Replay any pre-existing WAL so that a daemon restart preserves both.
    position_store = StrategyPositionStore()
    pnl_aggregator = PnLAggregator()
    ops_counters = OpsCounters()
    # 2026-05-21 fix: cross-run replay 가드 완화 + 항상 log. wal_path 가 None /
    # 미존재면 log_dir 추정해 cross-run 시도 (이전엔 skip 되어 빈 store 로 시작).
    if config.wal_path:
        log_dir = Path(config.wal_path).parent.parent
    else:
        log_dir = Path("logs/live")
    # 2026-06-05 active filter — _run_pipeline_attached 와 동일 가드.
    _allowed_sids = _active_strategy_ids(
        config.production_yaml if getattr(config, "production_yaml", None)
        else _bundle_root() / "configs/orchestrator/production.yaml"
    )
    _allowed_arg = _allowed_sids or None
    try:
        n_store = position_store.replay_from_wal_dir(
            log_dir, allowed_strategy_ids=_allowed_arg,
        )
        n_pnl = pnl_aggregator.replay_from_wal_dir(
            log_dir, allowed_strategy_ids=_allowed_arg,
        )
        logger.info(
            "cross-run replay: %d WAL files (store=%d / pnl=%d) from %s "
            "active_sids=%d",
            max(n_store, n_pnl), n_store, n_pnl, log_dir, len(_allowed_sids),
        )
    except Exception as err:
        logger.warning("cross-run replay failed: %s — fallback to single-path", err)
        if config.wal_path and Path(config.wal_path).exists():
            position_store.replay_from_wal(
                config.wal_path, allowed_strategy_ids=_allowed_arg,
            )
            pnl_aggregator.replay_from_wal(
                config.wal_path, allowed_strategy_ids=_allowed_arg,
            )
    dashboard_state = DashboardState(
        timeline_broker=timeline_broker,
        wal_path=config.wal_path,
    )
    dashboard_state.position_provider = position_store.get_positions
    dashboard_state.pnl_aggregator = pnl_aggregator
    dashboard_state.ops_counters = ops_counters
    # 2026-06-05 — 계좌 카드 (Binance + Bitget + KIS) 데이터 소스. 누락 시
    # ``/api/account/info`` 가 available=False 반환 → dashboard 가 "계좌 연결
    # 실패" 로 표시. _run_dashboard_only_mode 만 set 하고 _run_pipeline 은
    # 안 set 해서 인자 있는 모드에서 dashboard 가 깜깜이던 회귀 fix.
    from src.dashboard.account_info import AccountInfoProvider as _AIP  # noqa: PLC0415
    dashboard_state.account_info_provider = _AIP()
    # 2026-06-05 — dashboard 의 거래 시작/정지 버튼 컨트롤러. _run_pipeline 은
    # 이미 trading loop 가 가동 중이므로 controller.start 는 재진입 noop 처럼
    # 동작하면 OK. 미설정 시 dashboard 가 ``컨트롤러 미주입 (cmd 모드)`` +
    # ``controller unavailable`` 503 반환 (사용자 보고 결함).
    from src.dashboard.run_controller import RunController as _RC  # noqa: PLC0415
    dashboard_state.run_controller = _RC(
        _build_pipeline_factory(
            dashboard_state, logger,
            position_store=position_store, pnl_aggregator=pnl_aggregator,
            ops_counters=ops_counters,
        )
    )
    # #238 follow-up Issue 2 — trade history / 전략별 포지션은 영구·누적이어야
    # 한다. config.wal_path = {log_dir}/{run_id}/wal.jsonl 이므로 log_dir 은
    # 항상 parent.parent. 이 한 줄이 없으면 _resolve_log_dir 이 None →
    # 거래 이력이 매 run 마다 사라졌다 (사용자 보고 결함).
    if config.wal_path is not None:
        dashboard_state.log_dir = Path(config.wal_path).parent.parent
    # #238 follow-up Issue 1 — live SnapshotBuilder 노출 → venue INERT 가시화.
    config.on_snapshot_builder_ready = lambda sb: setattr(
        dashboard_state, "snapshot_builder", sb,
    )

    def _wal_observer(ev) -> None:
        timeline_broker.publish(asdict(ev))
        position_store.ingest_fill_event(ev.event_type, ev.payload or {})
        pnl_aggregator.ingest_fill_event(ev.event_type, ev.payload or {})
        ops_counters.ingest(ev.event_type, ev.payload or {})

    config.wal_observer = _wal_observer
    # Binance live-fill gap — see _run_pipeline_attached. Wiring the store
    # lets run_shadow_loop spawn the fill-stream consumer for
    # binance-testnet-shadow so `order_filled` events actually reach the WAL
    # (otherwise the live Binance path shows the submitted intent forever).
    config.position_store = position_store
    _wire_balance_provider(config)  # #238 Item 9

    # #238 follow-up — multi-symbol mark-price cache + manual close executor.
    # One ``LivePriceCache`` shared between the mark-price feed (writer) and
    # the dashboard (reader). The manual-close callback receives the executor
    # closure built by ``run_shadow_loop`` and parks it on ``DashboardState``
    # so the dashboard's POST /api/strategies/{sid}/positions/{sym}/close can
    # submit market orders through the same path as the strategy loop.
    from src.live.price_cache import LivePriceCache
    price_cache = LivePriceCache()
    config.live_price_cache = price_cache
    dashboard_state.price_cache = price_cache
    config.on_manual_close_executor_ready = lambda closure: setattr(
        dashboard_state, "manual_close_executor", closure,
    )

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
        # Dynamic Universe Architecture Phase 1 (2026-05-28) —
        # _build_universe_quote_provider 가 args._orchestrator lookup.
        # main() 에서 keyword 로 명시 전달된 args 로 박는다. 과거엔 args 가
        # enclosing scope 에 없어 NameError 로 silent skip 됐었음 (PR #336/#337 회귀).
        if args is not None:
            args._orchestrator = orch
        # 2026-05-20: re-entry bug fix — _live_entered 가 부팅 시 비어있어
        # 재시작 = 보유 종목 추가 매수 폭주. 이미 replay 된 store 의
        # positions 로 _live_entered 복원 → 부팅 후 첫 tick 에 보유 종목 진입 차단.
        try:
            if position_store is not None and hasattr(orch, "restore_live_entered"):
                positions = position_store.all_positions()
                n = orch.restore_live_entered(positions) or 0
                logger.info(
                    "orchestrator._live_entered restored %d entries from store "
                    "(positions=%d strategies)",
                    n, len(positions),
                )
        except Exception as err:  # noqa: BLE001 — never block startup
            logger.warning("restore_live_entered failed: %s", err)
        if risk_mgr is None:
            return
        # #238 — 청산 시 orchestrator 진입 기록 해제 → live-scanner 재진입 허용.
        risk_mgr._on_exit = orch.release_live_position
        # 2026-05-21 — ATR 기반 동적 stop. Strategy 가 Signal 에 실어보낸 per-entry
        # stop/TP/trailing pct override 를 risk manager 의 (sid, sym) dynamic
        # policy 로 등록한다. 없으면 정적 policy fallback.
        orch._on_entry = risk_mgr.register_entry_override
        _register_exit_policies(orch, risk_mgr, logger)

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

    # 2026-05-21 — Binance broker ↔ position_store reconciler (CLI 경로).
    # _run_pipeline_attached 와 동일 와이어링. Binance/Bitget broker 모드일 때 가동.
    reconciler_task: asyncio.Task | None = None
    reconciler_stop = asyncio.Event()
    _live_broker_cli = binance_adapter or bitget_adapter
    if _live_broker_cli is not None:
        from src.live.position_reconciler import PositionReconciler  # noqa: PLC0415

        def _sync_orch_live_entered(sid: str, symbol: str, qty) -> None:
            # 2026-05-22 — reconciler auto-fix 가 store qty 를 바꾸면
            # orchestrator._live_entered 도 정합. 미정합 시 청산된 종목이
            # 영구 진입 차단된다 (재시작 후 11시간 매수 0 의 원인).
            orch = getattr(dashboard_state, "orchestrator", None)
            if orch is not None and hasattr(orch, "sync_live_entered"):
                orch.sync_live_entered(sid, symbol, float(qty))

        reconciler = PositionReconciler(
            position_store=position_store,
            broker=_live_broker_cli,
            wal_observer=_wal_observer,
            alert_publisher=lambda p: timeline_broker.publish(p),
            on_position_synced=_sync_orch_live_entered,
            tol=Decimal("0.001"),
            interval_sec=float(os.environ.get("QTA_RECONCILE_INTERVAL_SEC", "60")),
        )
        reconciler_task = asyncio.create_task(
            reconciler.run_loop(reconciler_stop), name="qta-position-reconciler",
        )
        logger.info("PositionReconciler started (CLI)")

    try:
        await _run_with_duration(
            run_shadow_loop(
                config, kis_adapter=kis_adapter,
                binance_adapter=binance_adapter,
                bitget_adapter=bitget_adapter,
            ),
            duration_sec,
        )
    finally:
        if reconciler_task is not None:
            reconciler_stop.set()
            try:
                await asyncio.wait_for(reconciler_task, timeout=3.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                reconciler_task.cancel()
        if shutdown_dashboard is not None:
            await shutdown_dashboard()


def main(argv: list[str] | None = None) -> int:
    av = sys.argv[1:] if argv is None else argv
    if "--check-bundle" in av:
        return _run_check_bundle()
    if _is_no_args(argv):
        if os.environ.get("QTA_FIRST_RUN_HELP_ONLY", "").lower() == "true":
            return _show_first_run_help()
        # 2026-06-05 — 인자 없이 띄우면 standalone (dashboard 만). 사용자가
        # '거래 시작' 버튼 누르면 그때 RunController.start → pipeline_factory
        # → _run_pipeline_attached 실행. 옛 시도 (auto-default-args 주입) 는
        # 사용자가 시작 안 눌렀는데도 RUNNING 으로 표시되어 회피로 분류됨 —
        # 정상 status machine (IDLE → STARTING → RUNNING → STOPPED) 으로 복원.
        return _run_dashboard_only_mode()
    args = parse_args(argv)
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # 2026-06-05 — httpx 라이브러리 INFO 로그가 universe-quote refresh (수십~수백
    # 종목 REST) 마다 폭주. 운영 운영 가시성 0 (실 distractor) + 디스크 IO 낭비.
    # 에러는 WARNING 이상이라 유지.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _attach_rotating_file_log()  # 2026-06-11 — 콘솔 로그 → logs/live_run.log 자동 누적
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
        binance_adapter = _build_binance_adapter(args.broker)  # #231 S1
        bitget_adapter = _build_bitget_adapter(args.broker)    # P4b
        # #231 S2 — universe-scan strategies wire (KIS / Binance / Bitget provider)
        config.universe_quote_provider = _build_universe_quote_provider(
            args.broker, config.kis_client, args,
        )
        asyncio.run(
            _run_pipeline(
                config, kis_adapter, args.dashboard_port, logger, duration_sec,
                auto_open_browser=not args.no_browser,
                binance_adapter=binance_adapter,
                bitget_adapter=bitget_adapter,
                args=args,
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
