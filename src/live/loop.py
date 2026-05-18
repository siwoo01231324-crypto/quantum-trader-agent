from __future__ import annotations
import sys
import asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import logging
import signal
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
from src.live.feed import MarketDataFeed, BinancePublicFeed
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
):
    """Return the active broker/router for the given broker_mode.

    broker_mode == "paper-only"              → PaperBroker directly (Phase 1 regression 0)
    broker_mode == "kis-paper"               → AsyncOrderRouter(active=KIS adapter)
    broker_mode == "kis-paper-shadow"        → AsyncOrderRouter(active=KIS, fallback swap to PaperBroker)
    broker_mode == "binance-testnet-shadow"  → AsyncOrderRouter(active=Binance testnet, fallback swap to PaperBroker) (#231 S1)
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
        )
        orchestrator = _load_orchestrator(config, paper_broker)
        if config.on_orchestrator_ready is not None:
            try:
                config.on_orchestrator_ready(orchestrator)
            except Exception as err:
                logger.warning(
                    "live.loop.on_orchestrator_ready_failed error=%s", err,
                )

        snapshot_builder = SnapshotBuilder(
            config.symbols,
            kis_client=config.kis_client,
            config=config.snapshot_builder_config,
            universe_quote_provider=config.universe_quote_provider,  # #231 S2
            balance_provider=config.balance_provider,  # #238 Item 9
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

        async def producer():
            # WS reconnect loop (#133 hotfix): keepalive ping timeout / network
            # errors restart the feed with exponential backoff instead of
            # killing the daemon. The original implementation had no reconnect
            # so a single ConnectionClosedError after ~24h ended the run.
            attempt = 0
            max_attempts = 100  # ~ days of retries; daemon should outlive any
                                # transient outage but eventually give up if
                                # the exchange WS is permanently down.
            while not stop_event.is_set() and attempt < max_attempts:
                try:
                    async for tick in feed:
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
                    # async for exited cleanly (e.g., feed closed) — stop.
                    break
                except (asyncio.CancelledError, KeyboardInterrupt):
                    raise
                except BaseException as err:
                    if stop_event.is_set():
                        break
                    attempt += 1
                    delay = backoff_delay(attempt - 1, base=1.0, cap=60.0)
                    logger.warning(
                        "feed disconnect (attempt=%d/%d, sleep=%.1fs): %s: %s",
                        attempt, max_attempts, delay,
                        type(err).__name__, err,
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
            while not stop_event.is_set():
                try:
                    tick = await asyncio.wait_for(tick_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
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
                intents = await orchestrator.run_bar(ts, snapshot)
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
                iter_count += 1
                if config.max_iterations is not None and iter_count >= config.max_iterations:
                    stop_event.set()
                    break

        producer_task = asyncio.create_task(producer())
        consumer_task = asyncio.create_task(consumer())

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

        try:
            await asyncio.wait(
                {producer_task, consumer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_event.set()
            shutdown_tasks = [producer_task, consumer_task]
            if fill_task is not None:
                shutdown_tasks.append(fill_task)
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
            await feed.aclose()

    finally:
        lock.release()


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
