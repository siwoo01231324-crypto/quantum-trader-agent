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
from typing import Optional

from src.execution.base import MarketState, Tick as ExecTick
from src.execution.mock_matching import MockMatchingEngine
from src.execution.paper_broker import PaperBroker
from src.live.executor import execute_intents
from src.live.feed import MarketDataFeed, BinancePublicFeed
from src.live.process_lock import ProcessLock
from src.live.types import Tick
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


def _load_orchestrator(config: ShadowConfig, broker: PaperBroker) -> AsyncStrategyOrchestrator:
    """#94 production.yaml 부트 — 미존재 시 fallback (Phase 1 stub).

    #94 머지 후 본 함수가 load_orchestrator_from_yaml 사용으로 전환됨.
    fallback: 빈 orchestrator 생성, 명시적 warning 로그.
    """
    if config.production_yaml.exists():
        try:
            from src.portfolio.config_loader import load_orchestrator_from_yaml
            orch = load_orchestrator_from_yaml(config.production_yaml, policy=config.policy)
            logger.info("Loaded orchestrator from %s", config.production_yaml)
            return orch
        except ImportError as err:
            logger.warning(
                "production.yaml exists but config_loader missing (#94 not merged): %s. "
                "Falling back to empty orchestrator.", err,
            )
        except (RuntimeError, FileNotFoundError, OSError) as err:
            # 메타라벨러 모델 등 의존 자원 부재 → fallback (Phase 1 운영자가 모델 학습 후 활성화)
            logger.warning(
                "production.yaml load failed (likely missing model artifact): %s. "
                "Falling back to empty orchestrator. Train metalabeler first.", err,
            )
    else:
        logger.warning(
            "production.yaml not found at %s; running with empty orchestrator "
            "(Phase 1 stub, #94 merge required for full strategy roster).",
            config.production_yaml,
        )
    # Fallback: 빈 orchestrator
    return AsyncStrategyOrchestrator(policy=config.policy, broker=broker)


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
        wal = WAL(config.wal_path)
        matching_engine = MockMatchingEngine()
        broker = PaperBroker(
            wal=wal, kill_switch=kill_switch,
            matching_engine=matching_engine, initial_balance=config.initial_balance,
        )
        orchestrator = _load_orchestrator(config, broker)

        if feed is None:
            feed = BinancePublicFeed(config.symbols)
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
                broker.update_market(_tick_to_market_state(tick))
                ts = datetime.fromisoformat(tick.ts)
                snapshot = _tick_to_market_snapshot(tick)
                intents = await orchestrator.run_bar(ts, snapshot)
                if intents:
                    await execute_intents(
                        intents, broker=broker, kill_switch=kill_switch,
                        wal=wal, metrics=metrics,
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
