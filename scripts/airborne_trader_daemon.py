"""Standalone airborne trader daemon entry point.

Usage::

    python scripts/airborne_trader_daemon.py [--dry-run]

기본 dry_run=True. 실거래는 명시적 ``AIRBORNE_TRADER_DRY_RUN=false`` env 또는
``--live`` 플래그 (후속 PR 에서 broker 통합 후 활성화).

본 PR scope: skeleton + DummyBroker. 실제 Binance Futures client 통합 +
Daily loss alert + docker-compose service 는 후속 PR.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from live.airborne_fire_listener import AirborneFireListener  # noqa: E402
from live.airborne_trader import (  # noqa: E402
    AirborneTrader,
    AirborneTraderConfig,
    AirborneTraderRisk,
    AirborneTraderState,
)
from live.airborne_trader.trader import DummyBroker  # noqa: E402

logger = logging.getLogger("airborne_trader_daemon")


def _setup_signal_handlers(stop_event: asyncio.Event) -> None:
    """SIGINT / SIGTERM → graceful shutdown."""
    loop = asyncio.get_running_loop()

    def _handler(sig_name: str) -> None:
        logger.info("[airborne_trader] received %s, stopping...", sig_name)
        stop_event.set()

    try:
        loop.add_signal_handler(signal.SIGINT, _handler, "SIGINT")
        loop.add_signal_handler(signal.SIGTERM, _handler, "SIGTERM")
    except NotImplementedError:
        # Windows — KeyboardInterrupt 만 작동, SIGTERM 등 미지원
        pass


async def _main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    cfg = AirborneTraderConfig.from_env()
    if args.dry_run:
        cfg = AirborneTraderConfig(
            api_key=cfg.api_key,
            api_secret=cfg.api_secret,
            daemon_container=cfg.daemon_container,
            position_usd=cfg.position_usd,
            leverage=cfg.leverage,
            max_concurrent_positions=cfg.max_concurrent_positions,
            stop_loss_pct=cfg.stop_loss_pct,
            take_profit_pct=cfg.take_profit_pct,
            kst_entry_hours=cfg.kst_entry_hours,
            cooldown_after_stop_sec=cfg.cooldown_after_stop_sec,
            daily_loss_limit_usd=cfg.daily_loss_limit_usd,
            fire_max_age_seconds=cfg.fire_max_age_seconds,
            state_path=cfg.state_path,
            dry_run=True,
            poll_interval_seconds=cfg.poll_interval_seconds,
        )

    state = AirborneTraderState(path=cfg.state_path)
    listener = AirborneFireListener(container_name=cfg.daemon_container)
    risk = AirborneTraderRisk(cfg, state)
    # 본 PR scope: DummyBroker. 후속 PR 에서 BinanceFuturesBroker 추가.
    broker = DummyBroker()

    trader = AirborneTrader(
        config=cfg, state=state, risk=risk,
        listener=listener, broker=broker,
    )
    _setup_signal_handlers(trader.stop_event)

    try:
        await trader.run()
    finally:
        state.close()
    return 0


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="airborne_trader_daemon")
    p.add_argument("--dry-run", action="store_true",
                   help="force dry_run=True regardless of env")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
