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
from src.live.process_lock import ProcessLock
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
    broker_mode: Literal["paper-only", "kis-paper-shadow", "kis-paper"] = "paper-only"
    # Phase 2 feed mode (#177).
    #   "auto"   — KIS REST polling for any 6-digit KRX symbol; Binance WS otherwise
    #   "binance" / "kis" / "mock" — explicit override
    feed_mode: Literal["auto", "binance", "kis", "mock"] = "auto"
    # Optional KIS REST client for snapshot warmup + KISMarketFeed; supplied by
    # caller (live_run.py builds via KISClient(...)). None disables warmup.
    kis_client: Any | None = None
    # Optional WAL observer (#181 timeline broker / metrics tap).
    wal_observer: Callable[[WALEvent], None] | None = None
    # Mock-mode feed payload (deterministic smoke tests, --feed mock).
    mock_ticks: list[Tick] | None = None
    snapshot_builder_config: SnapshotBuilderConfig | None = None


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
):
    """Return the active broker/router for the given broker_mode.

    broker_mode == "paper-only"       → PaperBroker directly (Phase 1 regression 0)
    broker_mode == "kis-paper"        → AsyncOrderRouter(active=KIS adapter)
    broker_mode == "kis-paper-shadow" → AsyncOrderRouter(active=KIS, fallback swap to PaperBroker)
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
    raise ValueError(f"Unknown broker_mode: '{broker_mode}'")


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
    if mode == "kis" or (mode == "auto" and any(is_krx_symbol(s) for s in config.symbols)):
        from src.live.feed_kis import KISMarketFeed
        if config.kis_client is None:
            raise ValueError(
                "feed_mode=kis (or auto with KRX symbols) requires ShadowConfig.kis_client"
            )
        return KISMarketFeed(config.symbols, config.kis_client)
    return BinancePublicFeed(config.symbols)


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
) -> None:
    """Phase 1 Shadow Live Loop.

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
    metrics = metrics or Metrics()
    kill_switch = kill_switch or KillSwitch()
    config.lock_path.parent.mkdir(parents=True, exist_ok=True)
    config.wal_path.parent.mkdir(parents=True, exist_ok=True)

    lock = ProcessLock(config.lock_path)
    lock.acquire()
    try:
        wal = WAL(config.wal_path, observer=config.wal_observer)
        matching_engine = MockMatchingEngine()
        paper_broker = PaperBroker(
            wal=wal, kill_switch=kill_switch,
            matching_engine=matching_engine, initial_balance=config.initial_balance,
        )
        router = _build_router(
            config.broker_mode, kill_switch, metrics, paper_broker, kis_adapter
        )
        orchestrator = _load_orchestrator(config, paper_broker)

        snapshot_builder = SnapshotBuilder(
            config.symbols,
            kis_client=config.kis_client,
            config=config.snapshot_builder_config,
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
                    )
                iter_count += 1
                if config.max_iterations is not None and iter_count >= config.max_iterations:
                    stop_event.set()
                    break

        producer_task = asyncio.create_task(producer())
        consumer_task = asyncio.create_task(consumer())
        try:
            await asyncio.wait(
                {producer_task, consumer_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            stop_event.set()
            for t in (producer_task, consumer_task):
                if not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, BaseException):
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
