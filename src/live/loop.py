from __future__ import annotations
import sys
import os
import asyncio
# 2026-06-03 — Selector 강제 제거. Python 3.14 + Windows 의 default 는
# Proactor loop 인데, 코드가 강제로 Selector 로 바꾸면 websockets 라이브러리가
# 처음 3 tick 처리 후 ws.recv() 에서 stuck (producer 가 무한 idle). 외부
# ad-hoc 테스트 (default Proactor) 는 ticks 정상 push. 본 fix 는 Windows
# default (Proactor) 그대로 사용. KIS REST + httpx 는 둘 다 정상 작동.
# Selector 가 필요한 경우 (subprocess 등) 별도 라이브러리에서 명시 설정.

import logging
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from src.brokers.async_router import AsyncOrderRouter
from src.execution.base import MarketState, Tick as ExecTick
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.executor import execute_intents
from src.live.feed import MarketDataFeed, BinancePublicFeed, BinanceMarkPriceFeed
from src.live.post_only_fallback import cancel_pending_fallbacks
from src.live.fill_consumer import run_binance_fill_consumer
from src.live.process_lock import ProcessLock
from src.live.reconnect import backoff_delay
from src.live.schedule import wait_until_session_open
from src.live.snapshot_builder import SnapshotBuilder, SnapshotBuilderConfig, is_krx_symbol
from src.live.types import Tick, WALEvent
from src.live.wal import WAL
from src.observability.metrics import Metrics
from src.ops.kill_switch import KillSwitch
from src.portfolio._async_orchestrator import AsyncStrategyOrchestrator

logger = logging.getLogger(__name__)


@dataclass
class ShadowConfig:
    symbols: list[str]
    wal_path: Path = field(default_factory=lambda: Path("logs/shadow/wal.jsonl"))
    lock_path: Path = field(default_factory=lambda: Path("logs/shadow/.live_loop.lock"))
    initial_balance: Decimal = Decimal("100000")
    production_yaml: Path = field(default_factory=lambda: Path("configs/orchestrator/production.yaml"))
    policy: object = None
    max_iterations: int | None = None  # None=infinite (실 운영), 정수=테스트용 종료 조건
    # Phase 2 broker mode (#105). Default "paper-only" preserves Phase 1 behaviour.
    # #231 S1: binance-testnet-shadow added for Binance shadow live-daemon.
    broker_mode: Literal[
        "paper-only", "kis-paper-shadow", "kis-paper", "binance-testnet-shadow",
        "bitget-demo", "bitget-mainnet",  # P4b — Bitget USDT-M Futures
    ] = "paper-only"
    # Phase 2 feed mode (#177).
    #   "auto"   — KIS REST polling for any 6-digit KRX symbol; Binance WS otherwise
    #   "binance" / "kis" / "kis-ws" / "mock" — explicit override
    #   "kis-ws" (#231 S3): KIS WS realtime trade stream (≥40 종목/conn 제한)
    feed_mode: Literal["auto", "binance", "kis", "kis-ws", "mock"] = "auto"
    # Optional KIS REST client for snapshot warmup + KISMarketFeed; supplied by
    # caller (live_run.py builds via KISClient(...)). None disables warmup.
    kis_client: Any | None = None
    # Optional WAL observer (#181 timeline broker / metrics tap).
    wal_observer: Callable[[WALEvent], None] | None = None
    # Mock-mode feed payload (deterministic smoke tests, --feed mock).
    mock_ticks: list[Tick] | None = None
    snapshot_builder_config: SnapshotBuilderConfig | None = None
    # #231 S2 — universe-scan strategies (cs_*) 를 위해 SnapshotBuilder 에
    # 주입되는 universe OHLCV provider. live_run.py 의 _build_config 에서
    # broker_mode 기반 closure 빌드 (KIS: fetch_universe_snapshot,
    # Binance: fetch_universe_klines). None 이면 graceful hold path 유지.
    universe_quote_provider: Callable[[], dict] | None = None
    # #238 Item 9 — real venue balance provider (AccountInfoProvider) injected
    # into SnapshotBuilder so build_snapshot carries equity_usdt/equity_krw.
    # None → SnapshotBuilder uses the config placeholder → Item-8 conversion
    # safely drops Binance orders (inert, not flooding). live_run.py sets this
    # to the already-constructed AccountInfoProvider.
    balance_provider: object | None = None
    # Callback invoked once the orchestrator instance is constructed (#180).
    # Used by live_run.py to wire `DashboardState.orchestrator` so that
    # `POST /api/strategies/{id}/toggle` reaches the live orchestrator.
    on_orchestrator_ready: Callable[[AsyncStrategyOrchestrator], None] | None = None
    # #238 follow-up — invoked once the SnapshotBuilder is constructed. Used
    # by live_run.py to wire `DashboardState.snapshot_builder` so that
    # `GET /api/venue_equity_status` can surface which venue is INERT (real
    # equity unavailable → orders dropped). None → no-op (legacy/tests).
    on_snapshot_builder_ready: Callable[[Any], None] | None = None
    # #216 trading-session schedule gate. "krx" blocks startup until next KRX
    # open (09:00 KST) when current time is outside session, preventing
    # warmup-time EGW00201 floods that previously stalled the WS connect step.
    # "always" preserves legacy 24/7 behaviour for non-KRX symbols / smoke tests.
    schedule: Literal["krx", "always"] = "always"
    # #227 S3: optional LivePositionRiskManager. When set, the consumer calls
    # `evaluate(symbol, last_price, ts)` after the strategy dispatch each tick,
    # routing any returned SELL intents through the same broker/WAL pipeline.
    # None (default) leaves the legacy single-paradigm path untouched.
    position_risk_manager: Any | None = None
    # Binance fill-stream wiring. The live `binance-testnet-shadow` path had
    # NO production consumer of binance/async_ws.stream_fills() — the
    # executor only wrote `order_acked` (intent; Binance MARKET ack is
    # status=NEW, no price). When this StrategyPositionStore is supplied AND
    # broker_mode == "binance-testnet-shadow" with a binance_adapter, a
    # background task consumes the fill stream and emits `order_filled` WAL
    # events through the existing wal_observer fan-out. The store also lets
    # execute_intents register the coid→(symbol, side, strategy_id) context
    # the fill consumer needs. None (default) → no fill-stream task; the
    # paper / kis paths are byte-identical.
    position_store: Any | None = None
    # Multi-symbol mark-price feed (#238 follow-up). When ``True`` AND
    # ``position_risk_manager`` is set AND ``broker_mode == "binance-testnet-shadow"``,
    # a parallel ``BinanceMarkPriceFeed`` subscribes to ``!markPrice@arr@1s``
    # (all USDT-perp symbols, 1Hz mark price) and pipes every update through
    # ``position_risk_manager.evaluate(...)``. Without this, the single-symbol
    # aggTrade feed only triggers stop/TP for the one symbol in
    # ``config.symbols`` — universe-scanner positions on other symbols are
    # never evaluated and never auto-closed.
    enable_mark_price_feed: bool = True
    # Optional ``LivePriceCache`` written by ``_run_mark_price_consumer`` so
    # the dashboard can render live mark price + unrealized PnL% on every
    # open position without polling Binance REST. Same instance is wired to
    # ``DashboardState.price_cache`` by ``scripts/live_run.py``. ``None``
    # (default) → cache writes skipped, byte-identical to the previous path.
    live_price_cache: Any | None = None
    # Callback invoked once the broker/router + WAL + kill-switch are all
    # constructed. Receives a closure ``async (intents) -> dict`` that fires
    # the supplied intents through ``execute_intents`` with the same router /
    # WAL / metrics / position_store the trading loop uses. ``scripts/live_run.py``
    # uses this to wire ``DashboardState.manual_close_executor`` so the
    # dashboard's manual-close endpoint can submit market orders without
    # importing any broker internals. ``None`` (default) → no wiring, manual
    # close endpoint returns 503.
    on_manual_close_executor_ready: Callable[[Callable[..., Any]], None] | None = None


def _load_orchestrator(config: ShadowConfig, broker: PaperBroker) -> AsyncStrategyOrchestrator:
    """#94 production.yaml 부트 — 미존재 시 fallback (Phase 1 stub).

    #94 머지 후 본 함수가 load_orchestrator_from_yaml 사용으로 전환됨.
    fallback: 빈 orchestrator 생성, 명시적 warning 로그.
    """
    # `risk.dsl.evaluate` 가 attribute 접근을 하므로 policy=None 으로 들어오면
    # AttributeError 가 발생한다. None 인 경우 모든 옵션 None 인 permissive
    # 기본 Policy 를 주입 (#177 EXE smoke + 단위 테스트 호환).
    if config.policy is None:
        from risk.dsl import Policy
        config.policy = Policy(policy_version=1, name="default")

    if config.production_yaml.exists():
        try:
            from src.portfolio.config_loader import load_orchestrator_from_yaml
            # on_metalabeler_missing="skip": 모델 아티팩트 부재 시 해당 strategy entry 만
            # skip + warning, 나머지 5전략은 정상 로드 (#177).
            orch = load_orchestrator_from_yaml(
                config.production_yaml,
                policy=config.policy,
                on_metalabeler_missing="skip",
            )
            logger.info("Loaded orchestrator from %s", config.production_yaml)
            return orch
        except ImportError as err:
            logger.warning(
                "production.yaml exists but config_loader missing (#94 not merged): %s. "
                "Falling back to empty orchestrator.", err,
            )
        except (FileNotFoundError, OSError) as err:
            # YAML 자체 읽기 실패 — 알 수 없는 파일 시스템 오류
            logger.warning(
                "production.yaml read failed: %s. Falling back to empty orchestrator.", err,
            )
    else:
        logger.warning(
            "production.yaml not found at %s; running with empty orchestrator "
            "(Phase 1 stub, #94 merge required for full strategy roster).",
            config.production_yaml,
        )
    # Fallback: 빈 orchestrator
    return AsyncStrategyOrchestrator(policy=config.policy, broker=broker)


def _build_router(
    broker_mode: str,
    kill_switch: KillSwitch,
    metrics: Metrics,
    paper_broker: PaperBroker,
    kis_adapter=None,
    binance_adapter=None,
    bitget_adapter=None,
):
    """Return the active broker/router for the given broker_mode.

    broker_mode == "paper-only"              → PaperBroker directly (Phase 1 regression 0)
    broker_mode == "kis-paper"               → AsyncOrderRouter(active=KIS adapter)
    broker_mode == "kis-paper-shadow"        → AsyncOrderRouter(active=KIS, fallback swap to PaperBroker)
    broker_mode == "binance-testnet-shadow"  → AsyncOrderRouter(active=Binance testnet, fallback swap to PaperBroker) (#231 S1)
    broker_mode in {"bitget-demo","bitget-mainnet"} → AsyncOrderRouter(active=Bitget adapter) (P4b)
    """
    if broker_mode == "paper-only":
        return paper_broker
    if broker_mode in ("kis-paper", "kis-paper-shadow"):
        if kis_adapter is None:
            raise ValueError(
                f"broker_mode='{broker_mode}' requires kis_adapter to be provided"
            )
        return AsyncOrderRouter(
            active=kis_adapter,
            kill_switch=kill_switch,
            metrics=metrics,
        )
    if broker_mode == "binance-testnet-shadow":
        if binance_adapter is None:
            raise ValueError(
                f"broker_mode='{broker_mode}' requires binance_adapter to be provided"
            )
        return AsyncOrderRouter(
            active=binance_adapter,
            kill_switch=kill_switch,
            metrics=metrics,
        )
    if broker_mode in ("bitget-demo", "bitget-mainnet"):
        if bitget_adapter is None:
            raise ValueError(
                f"broker_mode='{broker_mode}' requires bitget_adapter to be provided"
            )
        return AsyncOrderRouter(
            active=bitget_adapter,
            kill_switch=kill_switch,
            metrics=metrics,
        )
    raise ValueError(f"Unknown broker_mode: '{broker_mode}'")


def emit_startup_events(
    wal: WAL,
    config: ShadowConfig,
    gate_resumed_at: datetime | None,
) -> None:
    """Write the WAL startup heartbeat events (#216 US-004).

    Always writes ``run_started`` so external monitors can see the daemon
    booted (lack of this record was the symptom that #216 surfaced — empty
    ``logs/shadow/{run_id}/`` directories).

    Additionally writes ``session_open`` when ``config.schedule='krx'`` and a
    gate-resume timestamp is available — i.e. either we slept through the
    gate or the gate evaluated us as already in-session. ``schedule='always'``
    skips ``session_open`` because no session-boundary semantics apply.
    """
    now_utc_iso = datetime.now(tz=timezone.utc).isoformat()
    wal.write(WALEvent(
        ts=now_utc_iso,
        event_type="run_started",
        payload={
            "run_id": config.wal_path.parent.name,
            "broker": config.broker_mode,
            "feed": config.feed_mode,
            "symbols": list(config.symbols),
            "schedule": config.schedule,
            "wal_path": str(config.wal_path),
        },
    ))
    if config.schedule == "krx" and gate_resumed_at is not None:
        wal.write(WALEvent(
            ts=datetime.now(tz=timezone.utc).isoformat(),
            event_type="session_open",
            payload={
                "kst_open": gate_resumed_at.isoformat(),
                "date": gate_resumed_at.date().isoformat(),
            },
        ))


def _select_feed(config: ShadowConfig) -> MarketDataFeed:
    """Select the live feed based on `config.feed_mode` and symbol shape.

    Auto policy: any 6-digit KRX symbol present → KISMarketFeed for the KRX
    subset (Binance feed is not invoked for KRX). Mixed-symbol setups should
    pass `feed=...` explicitly to `run_shadow_loop` instead.
    """
    mode = config.feed_mode
    if mode == "mock":
        from src.live.feed_kis import MockReplayFeed
        # gap_sec 작은 값 — drop-oldest 큐(maxsize=1) 가 producer 폭주를 흡수
        # 못 해서 consumer 가 max_iterations 도달 전에 producer 완료 → FIRST_COMPLETED
        # 로 루프가 조기 종료되는 문제 방지 (#177 smoke 결정성).
        return MockReplayFeed(config.mock_ticks or [], gap_sec=0.02)
    if mode == "kis-ws":
        # #231 S3 — KIS realtime WS feed. Auto-select single vs multi connection
        # based on symbol count (KIS WS single conn 40 종목 한도).
        from src.live.feed_kis_ws import (
            KISWebSocketMarketFeed, MultiConnectionKISWebSocketFeed,
        )
        if config.kis_client is None:
            raise ValueError(
                "feed_mode=kis-ws requires ShadowConfig.kis_client (auth source)"
            )
        # KISClient._auth / _app_key 추출 — private 이지만 같은 프로젝트 내 사용 안전.
        # KIS WS single connection 한도 (40 종목/conn) — 초과 시 multi-conn.
        if len(config.symbols) > MultiConnectionKISWebSocketFeed.BATCH_SIZE:
            # >40 종목 → KOSPI200 200종 등 production 운영: multi-connection 분산.
            return MultiConnectionKISWebSocketFeed(
                config.symbols,
                auth=config.kis_client._auth,
                app_key=config.kis_client._app_key,
            )
        return KISWebSocketMarketFeed(
            config.symbols,
            auth=config.kis_client._auth,
            app_key=config.kis_client._app_key,
        )
    if mode == "kis" or (mode == "auto" and any(is_krx_symbol(s) for s in config.symbols)):
        from src.live.feed_kis import KISMarketFeed
        if config.kis_client is None:
            raise ValueError(
                "feed_mode=kis (or auto with KRX symbols) requires ShadowConfig.kis_client"
            )
        return KISMarketFeed(config.symbols, config.kis_client)
    # P4b — Bitget broker_mode 면 BitgetPublicFeed (trade channel). Demo 는
    # wspap subdomain 자동 라우팅. mainnet 도 region-restricted issue 없음.
    if config.broker_mode in ("bitget-demo", "bitget-mainnet"):
        from src.live.feed import BitgetPublicFeed  # local import — avoid binance.* eager load
        paper = config.broker_mode == "bitget-demo"
        return BitgetPublicFeed(config.symbols, paper=paper)
    # #238 hotfix — testnet broker 면 testnet WS endpoint 명시. 한국 IP 에서
    # mainnet (fstream.binance.com) 은 connect 되지만 aggTrade 0건 push (지역 차단).
    base_url = (
        BinancePublicFeed.DEFAULT_TESTNET
        if config.broker_mode == "binance-testnet-shadow"
        else None
    )
    return BinancePublicFeed(config.symbols, base_url=base_url)


def _tick_to_market_state(tick: Tick) -> MarketState:
    """live.types.Tick → execution.base.MarketState.
    Phase 1 단순화: bid/ask 를 last 기준 ±0.01% 로 가정 (실제 호가창 미사용).
    """
    last = float(tick.price)
    return MarketState(
        tick=ExecTick(
            symbol=tick.symbol,
            bid=last * 0.9999,
            ask=last * 1.0001,
            last=last,
            volume=int(float(tick.qty) * 1000),  # placeholder
            ts=datetime.fromisoformat(tick.ts),
        ),
        adv=1_000_000.0,  # placeholder Phase 1
    )


def _tick_to_market_snapshot(tick: Tick) -> dict:
    """run_bar 의 market_snapshot 인자 형식: {symbol, price, equity_krw}.

    Phase 1 단순화: equity_krw=100000 (USDT-equivalent placeholder; Phase 2 에서 실 잔고 조회).
    """
    return {
        "symbol": tick.symbol,
        "price": float(tick.price),
        "equity_krw": 100000.0,
    }


async def run_shadow_loop(
    config: ShadowConfig,
    *,
    feed: MarketDataFeed | None = None,
    metrics: Metrics | None = None,
    kill_switch: KillSwitch | None = None,
    kis_adapter=None,
    binance_adapter=None,  # #231 S1 — broker_mode=binance-testnet-shadow 용
    bitget_adapter=None,   # P4b — broker_mode in {bitget-demo, bitget-mainnet} 용
    wait_for_session_fn: Callable[..., Any] = wait_until_session_open,
) -> None:
    """Phase 1 Shadow Live Loop.

    0. **Schedule gate (#216)** — block here until KRX session opens when
       ``config.schedule='krx'`` and current time is outside session. Prevents
       warmup-time KIS REST flood that previously stalled the WS connect step.
       ``schedule='always'`` is a no-op (legacy / non-KRX behaviour).
    1. ProcessLock 획득 (단일 인스턴스, FMEA F9)
    2. WAL 초기화
    3. PaperBroker + MockMatchingEngine 생성
    4. orchestrator 부트 (#94 production.yaml fallback)
    5. feed 연결 (None 이면 BinancePublicFeed)
    6. tick 수신 루프 (asyncio.Queue maxsize=1, latest-only):
       - feed 에서 tick 받아 큐에 put_nowait (꽉 차있으면 get_nowait 후 put — drop)
       - run_bar(ts, market_snapshot) → list[OrderIntent]
       - PaperBroker.update_market(market_state)
       - execute_intents(intents, broker, kill_switch, wal, metrics)
    7. SIGINT/SIGTERM → asyncio.Event → graceful shutdown → WAL flush → lock 해제

    config.max_iterations 가 정수면 N tick 처리 후 종료 (테스트용).
    """
    # Step 0: Schedule gate (#216) — must precede ProcessLock so startup outside
    # KRX hours does not hold the lock or spam the KIS REST API.
    gate_resumed_at = await wait_for_session_fn(config.schedule)

    metrics = metrics or Metrics()
    kill_switch = kill_switch or KillSwitch()
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.wal_path.parent.mkdir(parents=True, exist_ok=True)

    lock = ProcessLock(config.lock_path)
    lock.acquire()
    try:
        wal = WAL(config.wal_path, observer=config.wal_observer)
        # #216 US-004: emit startup heartbeat before any side-effecting work so
        # external monitors (telegram-notifier WAL tail, report-cron WAL find)
        # can see the daemon is alive even before the first market tick.
        emit_startup_events(wal, config, gate_resumed_at)
        matching_engine = MockMatchingEngine()
        paper_broker = PaperBroker(
            wal=wal, kill_switch=kill_switch,
            matching_engine=matching_engine, initial_balance=config.initial_balance,
        )
        router = _build_router(
            config.broker_mode, kill_switch, metrics, paper_broker,
            kis_adapter=kis_adapter, binance_adapter=binance_adapter,
            bitget_adapter=bitget_adapter,
        )
        # Build the manual-close executor closure as soon as the broker
        # pipeline is wired. The dashboard's manual-close endpoint calls this
        # to submit market orders through the *same* executor path the
        # strategy loop uses — identical risk gate, WAL trail, and store
        # registration. ``market_state=None`` is acceptable here: the
        # executor's risk gate degrades gracefully when no order book is
        # supplied, and manual close is operator-initiated rather than
        # signal-driven, so there's no risk of accidental cascading orders.
        if config.on_manual_close_executor_ready is not None:
            async def _manual_close_executor(intents):
                await execute_intents(
                    intents, broker=router, kill_switch=kill_switch,
                    wal=wal, metrics=metrics, market_state=None,
                    position_store=config.position_store,
                )
                return {"submitted": len(intents)}
            try:
                config.on_manual_close_executor_ready(_manual_close_executor)
            except Exception as err:
                logger.warning(
                    "live.loop.on_manual_close_executor_ready_failed error=%s",
                    err,
                )

        orchestrator = _load_orchestrator(config, paper_broker)
        if config.on_orchestrator_ready is not None:
            try:
                config.on_orchestrator_ready(orchestrator)
            except Exception as err:
                logger.warning(
                    "live.loop.on_orchestrator_ready_failed error=%s", err,
                )

        # post-only Maker 진입(post-only-maker-entry.draft.md 4단계) — 완전
        # 미체결 + 시장가 재발주도 REJECTED 일 때 `_live_entered` 박제를 푸는
        # 콜백. executor 의 post-only fallback 경로에서만 호출되며
        # `sync_live_entered(.., 0)` 으로 진입 기록을 discard 한다. legacy
        # market 진입 경로는 이 콜백을 절대 타지 않는다 (동작 무영향).
        def _release_live_entered(strategy_id: str, symbol: str) -> None:
            orchestrator.sync_live_entered(strategy_id, symbol, 0)

        snapshot_builder = SnapshotBuilder(
            config.symbols,
            kis_client=config.kis_client,
            config=config.snapshot_builder_config,
            universe_quote_provider=config.universe_quote_provider,  # #231 S2
            balance_provider=config.balance_provider,  # #238 Item 9
            # 2026-06-08 — bitget 거래는 bitget 잔고로 사이징 (binance 잔고로
            # 사이징돼 명목 11.6배 부푼 사고 fix). 그 외 USDT venue 는 binance.
            usdt_equity_venue=("bitget" if "bitget" in config.broker_mode else "binance"),
        )
        if config.on_snapshot_builder_ready is not None:
            try:
                config.on_snapshot_builder_ready(snapshot_builder)
            except Exception as err:
                logger.warning(
                    "live.loop.on_snapshot_builder_ready_failed error=%s", err,
                )
        await snapshot_builder.warmup()

        if feed is None:
            feed = _select_feed(config)
            await feed.connect()
            await feed.subscribe(config.symbols)

        stop_event = asyncio.Event()
        _setup_signal_handlers(stop_event)

        tick_queue: asyncio.Queue[Tick] = asyncio.Queue(maxsize=1)

        # 2026-06-11 — airborne fire-driven consumer (봉루프 decouple) 가 읽는
        # 최신 스냅샷 캐시. consumer() 가 매 tick build_snapshot 결과의
        # equity_usdt 와 ohlcv_history(BTC) 를 여기에 박아두면, fire consumer 의
        # equity_provider / btc_ohlcv_provider 가 봉루프와 무관하게 읽는다.
        # 미설정(부팅 직후 / AIRBORNE_FIRE_CONSUMER off) 이면 fire consumer 는
        # equity 0 → 사이징 drop (안전), BTC None → trend filter 생략.
        _live_snapshot_cache: dict[str, Any] = {
            "equity_usdt": 0.0, "ohlcv_history": None,
        }

        async def producer():
            # WS reconnect loop (#133 hotfix): keepalive ping timeout / network
            # errors restart the feed with exponential backoff instead of
            # killing the daemon. The original implementation had no reconnect
            # so a single ConnectionClosedError after ~24h ended the run.
            attempt = 0
            max_attempts = 100  # ~ days of retries; daemon should outlive any
                                # transient outage but eventually give up if
                                # the exchange WS is permanently down.
            # 2026-06-03 debug — producer 가 ticks 받는지 진단용
            _producer_tick_count = 0
            while not stop_event.is_set() and attempt < max_attempts:
                disconnect_err: BaseException | None = None
                try:
                    logger.info("producer.iter_start attempt=%d", attempt)
                    async for tick in feed:
                        if _producer_tick_count == 0:
                            logger.info(
                                "producer.FIRST_TICK %s @ %s (qty=%s)",
                                tick.symbol, tick.price, tick.qty,
                            )
                        _producer_tick_count += 1
                        if stop_event.is_set():
                            break
                        if tick_queue.full():
                            try:
                                tick_queue.get_nowait()
                                metrics.broker_fill_queue_overflow_total.labels(
                                    broker="live_feed", policy="latest_only",
                                ).inc()
                            except asyncio.QueueEmpty:
                                pass
                        await tick_queue.put(tick)
                        attempt = 0  # reset on any successful tick
                    # async for exited WITHOUT raising — finite feed (tests) or a
                    # graceful server-side WS close (live). A live daemon must
                    # reconnect, not stop: the previous `break` here silently ended
                    # the producer → asyncio.wait(FIRST_COMPLETED) tore the whole
                    # trading loop down even though stop_event was never set (the
                    # "거래 시작 눌렀는데 좀 지나니까 stopped" 회귀). Fall through to
                    # the shared reconnect path below; stop_event still ends it.
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except BaseException as err:
                    disconnect_err = err
                # Reached on BOTH a clean feed close and a disconnect exception.
                if stop_event.is_set():
                    break
                attempt += 1
                delay = backoff_delay(attempt - 1, base=1.0, cap=60.0)
                if disconnect_err is not None:
                    logger.warning(
                        "feed disconnect (attempt=%d/%d, sleep=%.1fs): %s: %s",
                        attempt, max_attempts, delay,
                        type(disconnect_err).__name__, disconnect_err,
                    )
                else:
                    logger.warning(
                        "feed closed cleanly; reconnecting (attempt=%d/%d, sleep=%.1fs)",
                        attempt, max_attempts, delay,
                    )
                try:
                    await feed.aclose()
                except BaseException:
                    pass
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=delay)
                    break  # stop_event triggered during sleep
                except asyncio.TimeoutError:
                    pass
                try:
                    await feed.connect()
                    await feed.subscribe(config.symbols)
                    logger.info("feed reconnected after attempt=%d", attempt)
                except BaseException as reconnect_err:
                    logger.warning(
                        "feed reconnect failed (attempt=%d): %s: %s",
                        attempt, type(reconnect_err).__name__, reconnect_err,
                    )
                    # loop will retry with longer backoff
                    continue
            if attempt >= max_attempts:
                logger.error(
                    "feed reconnect exhausted %d attempts; producer exiting",
                    max_attempts,
                )

        async def consumer():
            iter_count = 0
            # cs-tsmom-crypto-daily / KRX universe-scan basket dispatcher
            # (#218 follow-up — 2026-05-21 fix). orchestrator.run_bar 는
            # universe-scan strategy 의 Signal(symbol="CRYPTO_TOP30_BASKET")
            # 을 받으면 size_to_qty 가 basket 가격을 못 찾아 OrderIntent 가
            # silent drop 됨. 별도 polling 으로 strategy.latest_weights →
            # 종목별 dispatch_rebalance() → broker.place_order.
            #
            # 2026-05-21 — env gate 제거, 항상 활성. broker_mode 가 binance 가
            # 아니거나 universe-scan strategy 가 등록 안 된 경우 dispatcher 가
            # graceful no-op (latest_weights empty → skip). 즉 모든 모드에서
            # 안전, 자동발주 필요한 모드 (binance-testnet-shadow + cs-tsmom
            # active) 에서만 실제 동작.
            from src.live.cs_basket_dispatcher import BasketDispatcher
            basket_dispatcher = BasketDispatcher()
            logger.info("cs_basket_dispatcher.enabled — universe-scan auto-orders")
            # 2026-06-03 debug — consumer 가 tick 받는지 진단용
            _consumer_tick_count = 0
            _consumer_timeout_count = 0
            while not stop_event.is_set():
                try:
                    tick = await asyncio.wait_for(tick_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    _consumer_timeout_count += 1
                    if _consumer_timeout_count in (5, 30, 300):
                        logger.warning(
                            "consumer.NO_TICK %ds — producer may be stuck",
                            _consumer_timeout_count,
                        )
                    continue
                if _consumer_tick_count == 0:
                    logger.info(
                        "consumer.FIRST_TICK %s @ %s (after %d timeouts)",
                        tick.symbol, tick.price, _consumer_timeout_count,
                    )
                _consumer_tick_count += 1
                try:
                    ms = _tick_to_market_state(tick)
                    paper_broker.update_market(ms)
                    ts = datetime.fromisoformat(tick.ts)
                    # #3 (prior-review MEDIUM) — refresh the (15s-cached) balance
                    # provider OFF the event-loop thread BEFORE the sync
                    # build_snapshot. The provider's cache-miss does KIS+Binance
                    # REST; running it inline in _inject_real_equity blocked the
                    # tick loop (the #18 KIS contention area). asyncio.to_thread
                    # moves only that cache-miss REST off-loop; build_snapshot's
                    # _inject_real_equity then does a pure non-blocking peek.
                    # Guarded on a provider being wired so the default
                    # (balance_provider=None) path is byte-identical — no thread
                    # hop, no extra await point that would perturb the
                    # producer/consumer scheduling (legacy/tests).
                    if config.balance_provider is not None:
                        await asyncio.to_thread(snapshot_builder.refresh_balance)
                    snapshot = snapshot_builder.build_snapshot(tick)
                    # airborne fire-driven consumer 가 읽을 최신 equity / BTC
                    # ohlcv 캐시 갱신 (봉루프와 무관하게 발화 진입 사이징/필터에
                    # 사용). dict 갱신만 — fire consumer off 면 무비용 (읽는 곳 없음).
                    if isinstance(snapshot, dict):
                        _live_snapshot_cache["equity_usdt"] = snapshot.get(
                            "equity_usdt", 0.0,
                        )
                        _live_snapshot_cache["ohlcv_history"] = snapshot.get(
                            "ohlcv_history",
                        )
                    if _consumer_tick_count == 1:
                        logger.info("consumer.run_bar_start tick=%d ts=%s", _consumer_tick_count, ts.isoformat())
                    _t0 = time.monotonic() if _consumer_tick_count <= 3 else None
                    intents = await orchestrator.run_bar(ts, snapshot)
                    if _t0 is not None:
                        logger.info(
                            "consumer.run_bar_done tick=%d elapsed=%.2fs intents=%d",
                            _consumer_tick_count, time.monotonic() - _t0, len(intents),
                        )
                    if intents:
                        # Emit timeline `signal_emitted` events ahead of order
                        # placement so the dashboard / WAL audit trail captures
                        # strategy-level decisions even when risk gating later
                        # blocks the order (#177 + #181).
                        for intent in intents:
                            try:
                                wal.write(WALEvent(
                                    ts=datetime.now(timezone.utc).isoformat(),
                                    event_type="signal_emitted",
                                    payload={
                                        "strategy_id": intent.strategy_id,
                                        "symbol": intent.symbol,
                                        "side": intent.side,
                                        "qty": str(intent.qty),
                                        "reason": intent.reason,
                                    },
                                ))
                            except Exception as wal_err:
                                logger.warning(
                                    "wal.signal_emitted_write_failed strategy_id=%s error=%s",
                                    intent.strategy_id, wal_err,
                                )
                        await execute_intents(
                            intents, broker=router, kill_switch=kill_switch,
                            wal=wal, metrics=metrics, market_state=ms,
                            position_store=config.position_store,
                            # post-only Maker fallback 이 완전 미체결 시 호출.
                            on_entry_unfilled=_release_live_entered,
                        )
                    # cs-tsmom-crypto-daily universe-scan basket dispatch — see
                    # `src/live/cs_basket_dispatcher.py` for the why (orchestrator
                    # drops basket-symbol intents silently). Default OFF; opt-in
                    # via CS_BASKET_DISPATCH=1.
                    if basket_dispatcher is not None:
                        try:
                            # #324 — `snapshot` 자체가 market_snapshot dict
                            # (`SnapshotBuilder.build_snapshot` 반환). 이전 코드는
                            # `getattr(snapshot, "market_snapshot", ...)` 로 dict 에
                            # 없는 속성을 찾아 항상 None → 매 tick `no_prices` skip
                            # 으로 cs-tsmom-crypto-daily 발주가 silent drop 됐다.
                            if isinstance(snapshot, dict):
                                ohlcv = snapshot.get("ohlcv_history")
                            else:
                                ohlcv = getattr(snapshot, "ohlcv_history", None)
                            await basket_dispatcher.dispatch(
                                orchestrator=orchestrator,
                                snapshot=snapshot,
                                broker=router,
                                position_store=config.position_store,
                                ohlcv_history=ohlcv,
                                wal=wal,
                            )
                        except Exception as err:  # noqa: BLE001 — never abort live loop
                            logger.warning(
                                "basket_dispatcher.consumer_failed err=%s", err,
                            )
                    # #227 S3: position-level stop/TP evaluation. Runs even when
                    # the strategy dispatch produced no intents (the price tick is
                    # sufficient to cross a stop). Restricted to tick.symbol — the
                    # other symbols update on their own ticks.
                    if config.position_risk_manager is not None:
                        risk_intents = config.position_risk_manager.evaluate(
                            tick.symbol, Decimal(str(tick.price)), ts,
                        )
                        if risk_intents:
                            for ri in risk_intents:
                                try:
                                    wal.write(WALEvent(
                                        ts=datetime.now(timezone.utc).isoformat(),
                                        event_type="signal_emitted",
                                        payload={
                                            "strategy_id": ri.strategy_id,
                                            "symbol": ri.symbol,
                                            "side": ri.side,
                                            "qty": str(ri.qty),
                                            "reason": ri.reason,
                                        },
                                    ))
                                except Exception as wal_err:
                                    logger.warning(
                                        "wal.signal_emitted_write_failed (live_scanner_exit) "
                                        "strategy_id=%s error=%s",
                                        ri.strategy_id, wal_err,
                                    )
                            await execute_intents(
                                risk_intents, broker=router, kill_switch=kill_switch,
                                wal=wal, metrics=metrics, market_state=ms,
                                position_store=config.position_store,
                            )
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except Exception as tick_err:  # noqa: BLE001 — 한 틱 예외가 전체 거래 루프를 죽이면 안 됨
                    logger.exception(
                        "consumer.tick_failed symbol=%s tick=%d — 이 틱만 건너뛰고 루프 계속: %s",
                        getattr(tick, "symbol", "?"), _consumer_tick_count, tick_err,
                    )
                    continue
                iter_count += 1
                if config.max_iterations is not None and iter_count >= config.max_iterations:
                    stop_event.set()
                    break

        producer_task = asyncio.create_task(producer())
        consumer_task = asyncio.create_task(consumer())

        # ── Airborne fire-driven consumer (봉루프 decouple, 2026-06-11) ──────
        # AIRBORNE_FIRE_CONSUMER=1 일 때만 활성. history.jsonl 발화를 트레이더
        # OHLCV 봉루프와 무관하게 직접 구동 → universe refresh 랙으로 발화가
        # 떨어지던 사고("7시 롱 미매수") fix. 등록된 airborne live-scanner 전략
        # (id prefix `live-airborne`) 만 대상. dedup 은 on_bar consume 과 동일
        # logs/airborne_reentry/{ClassName}.json 영속 파일 공유 → 두 경로 동시
        # 가동해도 중복진입 0 (+ orchestrator._live_entered 가 (sid,sym) 1포지션).
        # 상세: docs/specs/airborne-fire-driven-consume.md.
        fire_consumer_task: asyncio.Task | None = None
        if os.environ.get("AIRBORNE_FIRE_CONSUMER", "0") == "1":
            try:
                fire_consumer_task = _start_airborne_fire_consumer(
                    orchestrator=orchestrator,
                    snapshot_cache=_live_snapshot_cache,
                    router=router,
                    kill_switch=kill_switch,
                    wal=wal,
                    metrics=metrics,
                    position_store=config.position_store,
                    release_live_entered=_release_live_entered,
                    stop_event=stop_event,
                )
            except Exception as err:  # noqa: BLE001 — 배선 실패가 거래 막으면 안 됨
                logger.warning(
                    "airborne fire consumer wiring failed (계속 진행): %s", err,
                )

        # Binance fill-stream consumer (#231 S5 / live-fill gap). ONLY when
        # broker_mode == "binance-testnet-shadow" with a real binance_adapter
        # AND a StrategyPositionStore is wired. The executor only writes
        # `order_acked` (Binance MARKET ack = status=NEW, no price); without
        # this background task NO `order_filled` is ever emitted for the live
        # Binance path → zero realized P&L / positions / trades. Spawned
        # OUTSIDE the FIRST_COMPLETED set (its natural completion must not end
        # the trading loop) and cancelled cleanly in the finally below. The
        # consumer itself is reconnect-bounded and never crashes the loop.
        fill_task: asyncio.Task | None = None
        if (
            config.broker_mode == "binance-testnet-shadow"
            and binance_adapter is not None
            and config.position_store is not None
        ):
            def _stream_factory():
                return binance_adapter.stream_fills()

            fill_task = asyncio.create_task(
                run_binance_fill_consumer(
                    _stream_factory,
                    wal=wal,
                    position_store=config.position_store,
                    stop_event=stop_event,
                ),
                name="binance-fill-consumer",
            )
            logger.info(
                "binance fill consumer started (broker_mode=%s symbols=%s)",
                config.broker_mode, config.symbols,
            )
        elif (
            config.broker_mode in ("bitget-demo", "bitget-mainnet")
            and bitget_adapter is not None
            and config.position_store is not None
        ):
            # 2026-06-08 — Bitget 계좌를 단방향(one-way) 포지션 모드로 강제.
            # 어댑터는 단방향 주문(position_side=BOTH, tradeSide 미전송)을 보내므로
            # 계좌가 헤지(양방향) 모드면 place-order 가 [40774] "order type for
            # unilateral position must also be the unilateral position type" 로
            # 전량 거부된다 (2026-06-08 마인넷 전환 시 신호 8건 전량 거부 사고 —
            # 데모 계좌는 단방향이라 안 드러났음). ensure_position_mode 가 정의만
            # 돼있고 호출되지 않던 것을 startup 에 연결. 포지션 보유 중(0포지션
            # 아님)이면 Bitget 이 모드변경 거부 → graceful skip (기존 모드 유지).
            try:
                await bitget_adapter.ensure_position_mode(hedge=False)
                logger.info(
                    "bitget position mode ensured: one-way (broker_mode=%s)",
                    config.broker_mode,
                )
            except Exception as exc:  # noqa: BLE001 — defensive, 거래 계속
                logger.warning(
                    "bitget ensure_position_mode(one-way) skipped: %s "
                    "(포지션 보유 중이거나 이미 설정됨 — 계좌 모드 수동 확인 권장)",
                    exc,
                )
            # 2026-06-10 P2 — synthetic SL/TP stand-down. 거래소 네이티브 preset
            # TP/SL(BITGET_NATIVE_TPSL=1)이 활성인 종목은 LivePositionRiskManager
            # 가 손 뗀다(거래소가 라인 청산) → 노이즈성 mark-price 틱에 라인 도달
            # 전 조기청산하던 사고 차단. OFF 면 미연결(기존 동작 그대로).
            if (
                os.environ.get("BITGET_NATIVE_TPSL", "0") == "1"
                and config.position_risk_manager is not None
            ):
                config.position_risk_manager.set_native_tpsl_check(
                    bitget_adapter.has_native_tpsl
                )
                logger.info(
                    "synthetic SL/TP stand-down wired — preset-active 종목은 "
                    "거래소 TP/SL 담당, synthetic 은 naked/청산분 백업"
                )
            # P4b — Bitget private WS user-data stream → order_filled WAL.
            from src.live.fill_consumer import run_bitget_fill_consumer
            def _bg_stream_factory():
                return bitget_adapter.stream_fills()

            # 거래소 네이티브 TP/SL 코디네이터 (2026-06-08) — 진입 체결 시 거래소에
            # 보호주문(plan order) 등록 / 청산 시 취소. Bitget 매칭엔진 서버측 즉시
            # 청산 → synthetic(mark-price watch)보다 빠르고 robust. synthetic
            # LivePositionRiskManager 는 백업으로 유지 (reduce-only → 무해).
            # ⚠️ 안전 게이트: BITGET_NATIVE_TPSL=1 일 때만 활성. 데모에서 실제
            # TPSL 주문 검증 후 마인넷에서 켤 것 (기본 OFF → 머지해도 동작변화 0).
            # 2026-06-08 — post-fill place-tpsl-order coordinator 는 one-way
            # holdSide(43011) 미해결. preset-on-entry(BITGET_NATIVE_TPSL, place-order
            # 에 presetStop* 첨부)로 대체. coordinator 는 별도 플래그로 분리해 OFF 유지.
            _prot_on_fill = None
            if (
                os.environ.get("BITGET_NATIVE_TPSL_POSTFILL", "0") == "1"
                and config.position_risk_manager is not None
                and config.position_store is not None
            ):
                from src.live.protective_coordinator import ProtectiveOrderCoordinator

                def _symbol_volatility(symbol: str) -> "float | None":
                    # 최근 N봉 (high-low)/close 평균 = 변동성 대리지표(슬리피지 ∝).
                    # snapshot ohlcv_history[symbol] 에서. 없으면 None(보정 안 함).
                    oh = _live_snapshot_cache.get("ohlcv_history")
                    if not isinstance(oh, dict):
                        return None
                    df = oh.get(symbol)
                    if df is None or len(df) == 0:
                        return None
                    try:
                        tail = df.tail(12)
                        rng = ((tail["high"] - tail["low"]) / tail["close"]).mean()
                        return float(rng)
                    except Exception:  # noqa: BLE001
                        return None

                _sl_factor = float(os.environ.get("AIRBORNE_SL_SLIP_FACTOR", "0") or 0)
                _sl_cap = float(os.environ.get("AIRBORNE_SL_SLIP_CAP_PCT", "0.003") or 0.003)
                _prot_coord = ProtectiveOrderCoordinator(
                    adapter=bitget_adapter,
                    position_store=config.position_store,
                    policy_lookup=config.position_risk_manager.effective_policy,
                    volatility_provider=_symbol_volatility,
                    sl_slip_factor=_sl_factor,
                    sl_slip_cap_pct=_sl_cap,
                )
                _prot_on_fill = _prot_coord.on_fill
                if _sl_factor > 0:
                    logger.info(
                        "변동성 보정 손절 ON (factor=%.3f cap=%.4f)", _sl_factor, _sl_cap,
                    )
                logger.info(
                    "bitget native TP/SL coordinator ATTACHED (BITGET_NATIVE_TPSL=1)"
                )

            fill_task = asyncio.create_task(
                run_bitget_fill_consumer(
                    _bg_stream_factory,
                    wal=wal,
                    position_store=config.position_store,
                    stop_event=stop_event,
                    on_fill=_prot_on_fill,
                ),
                name="bitget-fill-consumer",
            )
            logger.info(
                "bitget fill consumer started (broker_mode=%s symbols=%s)",
                config.broker_mode, config.symbols,
            )

            # OrphanGuard (2026-06-08) — WS 가 체결을 흘려 store↔broker 가 어긋날
            # 때 REST 로 복구. phantom(store 엔 있는데 broker 0) 청소 → synthetic
            # 22002 무한루프 멈춤. orphan(broker 엔 있는데 store 모름, 무보호) →
            # broker entry/mark 로 ROE 평가해 TP/SL 넘으면 청산. *봇 주문분만*
            # (수동 ORDI 등 미청산). WS 독립. BITGET_ORPHAN_GUARD=0 으로 끔.
            if (
                os.environ.get("BITGET_ORPHAN_GUARD", "1") == "1"
                and config.position_store is not None
            ):
                from src.live.orphan_guard import OrphanGuard
                _orphan_guard = OrphanGuard(
                    adapter=bitget_adapter,
                    position_store=config.position_store,
                    take_profit_roi=0.10, stop_loss_roi=0.05,
                    default_leverage=10.0, interval_sec=20.0,
                )
                asyncio.create_task(
                    _orphan_guard.run_loop(stop_event), name="orphan-guard",
                )
                logger.info(
                    "OrphanGuard ATTACHED (broker-truth orphan/phantom recovery, "
                    "20s, TP+10%%/SL-5%% ROE, 봇 주문분만)"
                )

        # Multi-symbol mark-price consumer (#238 follow-up). Subscribes to
        # `!markPrice@arr@1s` so every USDT-perp symbol's mark price reaches
        # ``position_risk_manager.evaluate`` once per second. Without this the
        # single-symbol aggTrade feed only evaluates one symbol per tick →
        # universe-scanner positions on the other symbols never trigger
        # stop/TP. Reconnect with exponential backoff is delegated to the
        # standalone helper below so its lifecycle matches the producer task
        # (cancelled cleanly on stop_event).
        mark_price_task: asyncio.Task | None = None
        timeout_sweep_task: asyncio.Task | None = None
        if (
            config.enable_mark_price_feed
            and config.broker_mode == "binance-testnet-shadow"
            and config.position_risk_manager is not None
        ):
            mark_price_task = asyncio.create_task(
                _run_mark_price_consumer(
                    position_risk_manager=config.position_risk_manager,
                    router=router,
                    kill_switch=kill_switch,
                    wal=wal,
                    metrics=metrics,
                    position_store=config.position_store,
                    stop_event=stop_event,
                    live_price_cache=config.live_price_cache,
                    tick_queue=tick_queue,  # #328 — universe-scan wakeup
                ),
                name="binance-mark-price-consumer",
            )
            logger.info(
                "binance mark-price consumer started (broker_mode=%s stream=%s)",
                config.broker_mode, BinanceMarkPriceFeed.STREAM_PATH,
            )
        elif (
            config.enable_mark_price_feed
            and config.broker_mode in ("bitget-demo", "bitget-mainnet")
            and config.position_risk_manager is not None
        ):
            # P4b — Bitget ticker channel per universe symbol. universe 가 큰 경우
            # 50개씩 chunk subscribe (BitgetMarkPriceFeed.connect 내부 처리).
            from src.live.feed import BitgetMarkPriceFeed
            _bg_paper = config.broker_mode == "bitget-demo"
            # 2026-06-08 — mark-price 피드 = config.symbols ∪ **현재 보유 종목**.
            # 손절은 *보유 중인* 종목만 평가하면 되므로 top-100 전체 구독은 불필요.
            # (앞선 시도: top-100 전체 구독 → public feed 와 같은 엔드포인트에
            # 111 구독 → Bitget WS 가 연결을 반복 차단 → consumer 가 틱을 못 받아
            # run_bar 정지 → 거래 0. 2026-06-08 BEAT/BTW/SNDK fire 미체결 사고.)
            # 보유 종목만 구독하면 부하 ↓ + 모든 포지션 stop/TP 커버. 피드는 재접속
            # /universe refresh 마다 factory 재호출돼 신규 보유분을 반영.
            def _bitget_mp_factory():
                syms = set(config.symbols)
                try:
                    if config.position_store is not None:
                        for _sid, _pos in config.position_store.all_positions().items():
                            for _sym, _qty in _pos:
                                if _qty:
                                    syms.add(_sym)
                except Exception as exc:  # noqa: BLE001 — 보유 조회 실패해도 피드는 떠야
                    logger.warning("bitget mark-price held-symbols fetch failed: %s", exc)
                return BitgetMarkPriceFeed(sorted(syms), paper=_bg_paper)
            mark_price_task = asyncio.create_task(
                _run_mark_price_consumer(
                    position_risk_manager=config.position_risk_manager,
                    router=router,
                    kill_switch=kill_switch,
                    wal=wal,
                    metrics=metrics,
                    position_store=config.position_store,
                    stop_event=stop_event,
                    feed_factory=_bitget_mp_factory,
                    live_price_cache=config.live_price_cache,
                    tick_queue=tick_queue,
                ),
                name="bitget-mark-price-consumer",
            )
            logger.info(
                "bitget mark-price consumer started (broker_mode=%s symbols=%d)",
                config.broker_mode, len(config.symbols),
            )

        # Feed-독립 timeout sweep (2026-06-16) — 틱 멈춘 종목(토큰화 주식
        # NVDA/SPYUSDT)도 max_hold 청산 보장. 틱 구동 evaluate 가 안 닿는 사각을
        # 주기 sweep 으로 커버. AIRBORNE_TIMEOUT_SWEEP_SEC=0 으로 끔(기존 동작).
        if (
            config.enable_mark_price_feed
            and config.position_risk_manager is not None
        ):
            _sweep_sec = float(
                os.environ.get("AIRBORNE_TIMEOUT_SWEEP_SEC", "30") or 30
            )
            if _sweep_sec > 0:
                timeout_sweep_task = asyncio.create_task(
                    _run_timeout_sweep(
                        position_risk_manager=config.position_risk_manager,
                        router=router,
                        kill_switch=kill_switch,
                        wal=wal,
                        metrics=metrics,
                        position_store=config.position_store,
                        stop_event=stop_event,
                        live_price_cache=config.live_price_cache,
                        interval_sec=_sweep_sec,
                    ),
                    name="timeout-sweep",
                )
                logger.info(
                    "timeout sweep started (interval=%.0fs, feed-독립 max_hold 청산)",
                    _sweep_sec,
                )

        try:
            await asyncio.wait(
                {producer_task, consumer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_event.set()
            # Surface WHY the loop ended. asyncio.wait(FIRST_COMPLETED) returns
            # as soon as producer or consumer finishes; the shutdown loop below
            # only awaits tasks that are NOT yet done, so the *terminating*
            # task's exception used to be silently discarded — the daemon would
            # "stop" with zero diagnostics. Log it here before cleanup.
            for _name, _t in (("producer", producer_task), ("consumer", consumer_task)):
                if _t.done() and not _t.cancelled():
                    _exc = _t.exception()
                    if _exc is not None:
                        logger.error(
                            "live loop ending — %s task raised %s: %s",
                            _name, type(_exc).__name__, _exc, exc_info=_exc,
                        )
                    else:
                        logger.info("live loop ending — %s task finished", _name)
            shutdown_tasks = [producer_task, consumer_task]
            if fill_task is not None:
                shutdown_tasks.append(fill_task)
            if mark_price_task is not None:
                shutdown_tasks.append(mark_price_task)
            if timeout_sweep_task is not None:
                shutdown_tasks.append(timeout_sweep_task)
            if fire_consumer_task is not None:
                shutdown_tasks.append(fire_consumer_task)
            for t in shutdown_tasks:
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except BaseException:
                        # Shutdown cleanup: swallow everything (incl.
                        # CancelledError) for the cancelled producer/
                        # consumer/fill tasks. BaseException already
                        # covers CancelledError.
                        pass
            # post-only Maker fallback task 취소 (post-only-maker-entry 3단계).
            # 미취소 시 데몬 종료 후에도 sleep 중이던 task 가 깨어나 시장가를
            # 발사할 수 있다.
            await cancel_pending_fallbacks()
            await feed.aclose()

    finally:
        lock.release()


async def _run_timeout_sweep(
    *,
    position_risk_manager,
    router,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    position_store,
    stop_event: asyncio.Event,
    live_price_cache=None,
    interval_sec: float = 30.0,
) -> None:
    """Feed-독립 주기 timeout sweep — 틱이 멈춘 종목도 max_hold 청산 보장.

    틱 구동 ``position_risk_manager.evaluate(tick.symbol, ...)`` 는 mark-price
    push 가 오는 종목만 평가한다. 토큰화 주식(NVDA/SPYUSDT)처럼 ticker 가 멈추면
    evaluate 가 안 불려 max_hold 가 지나도 timeout 청산이 안 됐다 (2026-06-16
    NVDA/SPY 무한보유 사고). 본 task 는 ``interval_sec`` 마다 보유 포지션 전체를
    순회(``sweep_timeouts``)해 경과분을 시장가 reduce_only 로 청산한다. price 는
    live_price_cache 의 last-known (없으면 avg_cost) — 시장가라 참조가면 충분.

    SELL/cover 라우팅은 ``_run_mark_price_consumer`` 와 동일 (signal_emitted WAL
    → execute_intents). 청산이 불가한 시각(예: 토큰화 주식 미장 폐장)엔 broker 가
    reject → 다음 sweep 에서 _pending_exit self-heal 후 재시도.
    """
    def _price_lookup(symbol: str):
        if live_price_cache is None:
            return None
        snap = live_price_cache.get_price(symbol)
        return snap.price if snap is not None else None

    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_sec)
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        try:
            now = datetime.now(timezone.utc)
            intents = position_risk_manager.sweep_timeouts(now, _price_lookup)
        except Exception as err:  # noqa: BLE001 — sweep 에러로 루프 죽이지 않음
            logger.warning("timeout_sweep.evaluate_failed err=%s", err)
            continue
        for ri in intents:
            price = _price_lookup(ri.symbol)
            last = float(price) if price else 0.0
            ms = MarketState(
                tick=ExecTick(
                    symbol=ri.symbol,
                    bid=last * 0.9999 if last else 0.0,
                    ask=last * 1.0001 if last else 0.0,
                    last=last,
                    volume=0,
                    ts=now,
                ),
                adv=1_000_000.0,
            )
            try:
                wal.write(WALEvent(
                    ts=datetime.now(timezone.utc).isoformat(),
                    event_type="signal_emitted",
                    payload={
                        "strategy_id": ri.strategy_id,
                        "symbol": ri.symbol,
                        "side": ri.side,
                        "qty": str(ri.qty),
                        "reason": ri.reason,
                    },
                ))
            except Exception as wal_err:  # noqa: BLE001
                logger.warning(
                    "wal.signal_emitted_write_failed (timeout_sweep) "
                    "strategy_id=%s error=%s", ri.strategy_id, wal_err,
                )
            try:
                await execute_intents(
                    [ri], broker=router, kill_switch=kill_switch,
                    wal=wal, metrics=metrics, market_state=ms,
                    position_store=position_store,
                )
            except Exception as exec_err:  # noqa: BLE001 — 한 종목 실패가 sweep 중단 안 함
                logger.warning(
                    "timeout_sweep.execute_failed sym=%s err=%s",
                    ri.symbol, exec_err,
                )


async def _run_mark_price_consumer(
    *,
    position_risk_manager,
    router,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    position_store,
    stop_event: asyncio.Event,
    feed_factory: Callable[[], "BinanceMarkPriceFeed"] | None = None,
    live_price_cache=None,
    tick_queue: "asyncio.Queue[Tick] | None" = None,
) -> None:
    """Run the Binance ``!markPrice@arr@1s`` consumer until ``stop_event`` fires.

    For each 1-second batch of ``(symbol, mark_price, ts)`` tuples, calls
    ``position_risk_manager.evaluate(symbol, mark_price, ts)`` and routes any
    returned SELL intents through the existing executor pipeline. Reconnect
    with exponential backoff matches the producer-task pattern in
    :func:`run_shadow_loop`.

    The function never raises out to the caller — any ``BaseException`` other
    than ``CancelledError`` is logged and the WS connection is reopened on
    the next backoff tick. This keeps the trading loop alive even if the
    mark-price endpoint hiccups.

    ``feed_factory`` is injectable for tests; production callers pass
    ``None`` and the function picks the testnet endpoint to match the
    aggTrade feed's regional-restriction workaround.
    """
    if feed_factory is None:
        def feed_factory():
            return BinanceMarkPriceFeed(base_url=BinanceMarkPriceFeed.DEFAULT_TESTNET)

    attempt = 0
    max_attempts = 100
    feed = feed_factory()
    try:
        await feed.connect()
    except BaseException as err:
        logger.warning("mark-price feed initial connect failed: %s", err)

    while not stop_event.is_set() and attempt < max_attempts:
        try:
            async for batch in feed:
                if stop_event.is_set():
                    break
                # Cheap broker-state guard: a market_state for the executor
                # is required when an exit fires. Build a minimal MarketState
                # per symbol from the mark price itself (no order book).
                for symbol, mark_price, ts in batch:
                    # Dashboard live-price overlay: write *every* mark-price
                    # update — including symbols with no open position. Cheap
                    # (dict assignment under a single lock), and lets the
                    # dashboard render a useful price the moment a future
                    # position opens. Errors must never break the consumer.
                    if live_price_cache is not None:
                        try:
                            live_price_cache.set_price(symbol, mark_price, ts)
                        except Exception as cache_err:
                            logger.warning(
                                "live_price_cache.set_price failed sym=%s err=%s",
                                symbol, cache_err,
                            )
                    try:
                        risk_intents = position_risk_manager.evaluate(
                            symbol, mark_price, ts,
                        )
                    except Exception as eval_err:
                        logger.warning(
                            "position_risk_manager.evaluate failed sym=%s err=%s",
                            symbol, eval_err,
                        )
                        continue
                    if not risk_intents:
                        continue
                    ms = MarketState(
                        tick=ExecTick(
                            symbol=symbol,
                            bid=float(mark_price) * 0.9999,
                            ask=float(mark_price) * 1.0001,
                            last=float(mark_price),
                            volume=0,
                            ts=ts,
                        ),
                        adv=1_000_000.0,
                    )
                    for ri in risk_intents:
                        try:
                            wal.write(WALEvent(
                                ts=datetime.now(timezone.utc).isoformat(),
                                event_type="signal_emitted",
                                payload={
                                    "strategy_id": ri.strategy_id,
                                    "symbol": ri.symbol,
                                    "side": ri.side,
                                    "qty": str(ri.qty),
                                    "reason": ri.reason,
                                },
                            ))
                        except Exception as wal_err:
                            logger.warning(
                                "wal.signal_emitted_write_failed (mark_price_exit) "
                                "strategy_id=%s error=%s",
                                ri.strategy_id, wal_err,
                            )
                    await execute_intents(
                        risk_intents, broker=router, kill_switch=kill_switch,
                        wal=wal, metrics=metrics, market_state=ms,
                        position_store=position_store,
                    )
                # #328 — testnet BTCUSDT aggTrade tick 부재로 cs-tsmom
                # (universe-scan) 발주 미트리거되던 사고 fix. mark-price batch
                # 마다 representative tick (BTC 우선, 없으면 첫 종목) 을 메인
                # consumer 의 tick_queue 에 drop-oldest put → consumer() 가 그
                # tick 으로 snapshot 빌드 + orchestrator.run_bar 호출 →
                # cs-tsmom.on_bar 가 매초 깨움 → 252s warmup 후 5s 마다 rebal
                # cycle. tick_queue=None (테스트/레거시) 면 byte-identical.
                if tick_queue is not None and batch:
                    rep = next(
                        (e for e in batch if e[0].upper() == "BTCUSDT"),
                        batch[0],
                    )
                    rep_sym, rep_price, rep_ts = rep
                    synthetic_tick = Tick(
                        symbol=rep_sym,
                        price=rep_price,
                        qty=Decimal("0"),
                        ts=rep_ts.isoformat() if hasattr(rep_ts, "isoformat") else str(rep_ts),
                        server_ts=None,
                    )
                    # producer() 와 동일한 drop-oldest 패턴
                    if tick_queue.full():
                        try:
                            tick_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                    try:
                        tick_queue.put_nowait(synthetic_tick)
                    except asyncio.QueueFull:
                        pass  # 다음 batch 에서 재시도
                attempt = 0
            break  # feed closed cleanly
        except (asyncio.CancelledError, KeyboardInterrupt):
            raise
        except BaseException as err:
            if stop_event.is_set():
                break
            attempt += 1
            delay = backoff_delay(attempt - 1, base=1.0, cap=60.0)
            logger.warning(
                "mark-price feed disconnect (attempt=%d/%d, sleep=%.1fs): %s: %s",
                attempt, max_attempts, delay,
                type(err).__name__, err,
            )
            try:
                await feed.aclose()
            except BaseException:
                pass
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                pass
            feed = feed_factory()
            try:
                await feed.connect()
                logger.info("mark-price feed reconnected after attempt=%d", attempt)
            except BaseException as reconnect_err:
                logger.warning(
                    "mark-price feed reconnect failed (attempt=%d): %s: %s",
                    attempt, type(reconnect_err).__name__, reconnect_err,
                )
                continue
    if attempt >= max_attempts:
        logger.error(
            "mark-price feed reconnect exhausted %d attempts; consumer exiting",
            max_attempts,
        )
    try:
        await feed.aclose()
    except BaseException:
        pass


def _build_airborne_fire_specs(orchestrator: AsyncStrategyOrchestrator) -> list:
    """등록된 airborne live-scanner 전략 → AirborneStrategySpec 리스트.

    id prefix ``live-airborne`` 인 전략만 대상. 각 전략 인스턴스에서 게이트
    명세를 introspect:
      - kst_entry_hours: 진입 허용 KST 도착시각 set (instance/class attr).
      - allowed_sides: ``shorts_allowed`` + short-only 여부로 결정.
          * short-whitelist 류 (id 에 ``short`` 포함) → {"short"}.
          * 그 외 bidir airborne → {"long","short"}.
      - universe: ``get_universe()`` set (실패/미선언 시 None = 무제한).
      - btc_filter: ``btc_trend_filter_enabled`` (default False).
      - instance: dedup 공유용 전략 인스턴스.
    """
    from src.live.airborne_fire_consumer import AirborneStrategySpec

    specs: list = []
    for sid, strat in orchestrator.strategies.items():
        if not sid.startswith("live-airborne"):
            continue
        hours = getattr(strat, "kst_entry_hours", None)
        if hours is None:
            continue
        kst_hours = frozenset(int(h) for h in hours)
        # allowed sides — short-only 전략은 id 에 "short" 포함 (short-whitelist).
        if "short" in sid:
            allowed_sides = frozenset({"short"})
        elif getattr(strat, "shorts_allowed", False):
            allowed_sides = frozenset({"long", "short"})
        else:
            allowed_sides = frozenset({"long"})
        # universe — get_universe() (classmethod). 실패/미선언 → None.
        universe: frozenset[str] | None = None
        get_u = getattr(type(strat), "get_universe", None)
        if callable(get_u):
            try:
                universe = frozenset(get_u())
            except Exception:  # noqa: BLE001 — 조회 실패 시 무제한 (보수적)
                universe = None
        btc_filter = bool(getattr(strat, "btc_trend_filter_enabled", False))
        specs.append(AirborneStrategySpec(
            id=sid,
            kst_entry_hours=kst_hours,
            allowed_sides=allowed_sides,
            universe=universe,
            btc_filter=btc_filter,
            instance=strat,
        ))
    return specs


def _start_airborne_fire_consumer(
    *,
    orchestrator: AsyncStrategyOrchestrator,
    snapshot_cache: dict,
    router,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
    position_store,
    release_live_entered: Callable[[str, str], None],
    stop_event: asyncio.Event,
) -> "asyncio.Task | None":
    """AirborneFireConsumer 구성 + run_loop 백그라운드 task 시작.

    route_intents 는 consumer() 가 run_bar OrderIntent 에 쓰는 것과 동일한
    경로(signal_emitted WAL → execute_intents)를 재사용한다. equity_provider /
    btc_ohlcv_provider 는 봉루프가 매 tick 갱신하는 snapshot_cache 에서 읽는다.
    """
    from src.live.airborne_fire_consumer import AirborneFireConsumer

    specs = _build_airborne_fire_specs(orchestrator)
    if not specs:
        logger.info(
            "airborne fire consumer: 등록된 live-airborne 전략 없음 — 미시작",
        )
        return None

    store_path = os.environ.get(
        "AIRBORNE_FIRE_STORE_PATH", "logs/airborne_fires/history.jsonl",
    )
    from src.dashboard.airborne_fire_store import AirborneFireStore
    fire_store = AirborneFireStore(store_path)

    async def _route(intents: list) -> None:
        if not intents:
            return
        # run_bar OrderIntent 라우팅과 동일 — signal_emitted WAL 선기록 후
        # execute_intents (kill_switch/conversion/broker/post-only fallback).
        for intent in intents:
            try:
                wal.write(WALEvent(
                    ts=datetime.now(timezone.utc).isoformat(),
                    event_type="signal_emitted",
                    payload={
                        "strategy_id": intent.strategy_id,
                        "symbol": intent.symbol,
                        "side": intent.side,
                        "qty": str(intent.qty),
                        "reason": intent.reason,
                    },
                ))
            except Exception as wal_err:  # noqa: BLE001
                logger.warning(
                    "wal.signal_emitted_write_failed (airborne_fire) "
                    "strategy_id=%s error=%s", intent.strategy_id, wal_err,
                )
        await execute_intents(
            intents, broker=router, kill_switch=kill_switch,
            wal=wal, metrics=metrics, market_state=None,
            position_store=position_store,
            on_entry_unfilled=release_live_entered,
        )

    def _equity_provider() -> float:
        try:
            return float(snapshot_cache.get("equity_usdt", 0.0) or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def _btc_ohlcv_provider():
        ohlcv = snapshot_cache.get("ohlcv_history")
        if isinstance(ohlcv, dict):
            return ohlcv.get("BTCUSDT")
        return None

    freshness = float(os.environ.get("AIRBORNE_FIRE_FRESHNESS_SEC", "600") or 600)
    long_freshness = float(os.environ.get("AIRBORNE_LONG_FRESHNESS_SEC", "90") or 90)
    # 숏 차단 시간대 — KST 07시 기본 (유럽장 상승추세 가드, 2026-06-15). csv.
    _sbh_env = os.environ.get("AIRBORNE_SHORT_BLOCK_HOURS", "7")
    short_block_hours = frozenset(
        int(h) for h in _sbh_env.split(",") if h.strip().isdigit()
    )
    interval = float(os.environ.get("AIRBORNE_FIRE_INTERVAL_SEC", "15") or 15)
    pace = float(os.environ.get("AIRBORNE_FIRE_PACE_SEC", "0.15") or 0.15)

    def _skip_notify(text: str) -> None:
        """시간게이트 진입 스킵 → 텔레그램 (fail-soft). consumer 가 to_thread 로 호출."""
        try:
            from src.observability.alerts import notify as _alerts_notify
            _alerts_notify("info", "airborne 진입 스킵", text)
        except Exception as err:  # noqa: BLE001 — 알림 실패가 거래 막지 않음
            logger.warning("airborne skip notify wiring failed: %s", err)

    consumer = AirborneFireConsumer(
        fire_store=fire_store,
        orchestrator=orchestrator,
        strategy_specs=specs,
        route_intents=_route,
        equity_provider=_equity_provider,
        btc_ohlcv_provider=_btc_ohlcv_provider,
        notify=_skip_notify,
        freshness_sec=freshness,
        long_freshness_sec=long_freshness,
        short_block_hours=short_block_hours,
        interval_sec=interval,
        pace_sec=pace,
    )
    task = asyncio.create_task(
        consumer.run_loop(stop_event), name="airborne-fire-consumer",
    )
    # run_loop 자체가 "started (decoupled from bar loop)" 를 emit — 여기선 배선
    # 세부(specs/store)만 추가 기록.
    logger.info(
        "airborne fire consumer wired: specs=%d store=%s", len(specs), store_path,
    )
    return task


def _setup_signal_handlers(stop_event: asyncio.Event) -> None:
    """SIGINT/SIGTERM → stop_event.set(). Windows 호환."""
    def handler(*_args):
        stop_event.set()
    try:
        loop = asyncio.get_running_loop()
        if sys.platform != "win32":
            loop.add_signal_handler(signal.SIGINT, handler)
            loop.add_signal_handler(signal.SIGTERM, handler)
        else:
            signal.signal(signal.SIGINT, handler)
    except (NotImplementedError, ValueError):
        pass
