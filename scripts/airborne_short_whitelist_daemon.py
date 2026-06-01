"""Airborne SHORT-only Whitelist trader daemon entry.

기존 ``airborne_trader_daemon.py`` 와 동일한 main loop 인프라 (broker, state,
listener) 를 그대로 사용하되, **Risk gate 만** ``AirborneShortWhitelistRisk``
로 교체한다. 기존 daemon 코드를 *수정하지 않음*.

Usage::

    python scripts/airborne_short_whitelist_daemon.py [--dry-run]
    python scripts/airborne_short_whitelist_daemon.py --status
    python scripts/airborne_short_whitelist_daemon.py --unlock-daily-kill

State path 는 기존 daemon 과 *분리* — 동시 운영 가능
(``logs/airborne_short_whitelist/state.db`` default).

Whitelist 는 ``config/airborne_short_whitelist.yaml`` 에서 시작 시 로드.
runtime 갱신 미지원 — 새 whitelist 적용은 daemon 재시작 필요.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses as _dc
import logging
import os
import signal as _signal
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

from live.airborne_fire_listener import AirborneFireListener  # noqa: E402
from live.airborne_short_whitelist import (  # noqa: E402
    AirborneShortWhitelistRisk,
    active_symbols,
    load_whitelist,
)
from live.airborne_trader import (  # noqa: E402
    AirborneTrader,
    AirborneTraderConfig,
    AirborneTraderState,
)
from live.airborne_trader.trader import DummyBroker  # noqa: E402

logger = logging.getLogger("airborne_short_whitelist_daemon")

DEFAULT_WHITELIST_PATH = _REPO_ROOT / "config" / "airborne_short_whitelist.yaml"
DEFAULT_STATE_PATH = _REPO_ROOT / "logs" / "airborne_short_whitelist" / "state.db"


def _setup_signal_handlers(stop_event: asyncio.Event) -> None:
    loop = asyncio.get_running_loop()

    def _handler(sig_name: str) -> None:
        logger.info("[short_whitelist] received %s, stopping...", sig_name)
        stop_event.set()

    try:
        loop.add_signal_handler(_signal.SIGINT, _handler, "SIGINT")
        loop.add_signal_handler(_signal.SIGTERM, _handler, "SIGTERM")
    except NotImplementedError:
        pass  # Windows


def _resolve_config(args: argparse.Namespace) -> AirborneTraderConfig:
    cfg = AirborneTraderConfig.from_env()
    # state path 분리 — 기존 daemon 의 SQLite 와 안 겹침
    state_path = Path(os.environ.get(
        "AIRBORNE_SHORT_WHITELIST_STATE_PATH", str(DEFAULT_STATE_PATH),
    ))
    cfg = _dc.replace(cfg, state_path=state_path)
    if args.dry_run:
        cfg = _dc.replace(cfg, dry_run=True)
    return cfg


async def _main_async(args: argparse.Namespace) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    cfg = _resolve_config(args)

    wl_path = Path(args.whitelist or DEFAULT_WHITELIST_PATH)
    logger.info("[short_whitelist] loading whitelist from %s", wl_path)
    try:
        wl_cfg = load_whitelist(wl_path)
    except Exception as err:  # noqa: BLE001
        logger.error("[short_whitelist] whitelist load failed: %s", err)
        return 2
    actives = active_symbols(wl_cfg)
    if not actives:
        logger.error("[short_whitelist] whitelist 의 status=active 0개 — abort")
        return 3

    # KST hour gate override (선택) — yaml 의 ``kst_entry_hours`` 가 있으면
    # AirborneTraderConfig 의 default {8,11,16,22} 를 덮어쓴다. Hard OOS
    # 검증으로 legacy gate 가 SHORT-only 알파의 92% 를 버리는 것이 확인돼
    # 본 strategy 는 train_PF>1 인 19시간을 yaml 로 지정함.
    # (composition — frozen config 를 dataclasses.replace 로 swap)
    if wl_cfg.kst_entry_hours is not None:
        cfg = _dc.replace(cfg, kst_entry_hours=wl_cfg.kst_entry_hours)
        logger.info(
            "[short_whitelist] kst_entry_hours override from yaml: %s "
            "(legacy default 무시)",
            sorted(wl_cfg.kst_entry_hours),
        )

    logger.info(
        "[short_whitelist] whitelist as_of=%s active=%d %s",
        wl_cfg.as_of, len(actives), sorted(actives),
    )
    logger.info(
        "[short_whitelist] kst_entry_hours effective: %s",
        sorted(cfg.kst_entry_hours),
    )

    state = AirborneTraderState(path=cfg.state_path)
    listener = AirborneFireListener(container_name=cfg.daemon_container)
    risk = AirborneShortWhitelistRisk(cfg, state, active_symbols=actives)

    if cfg.dry_run:
        logger.info("[short_whitelist] DRY_RUN — DummyBroker")
        broker = DummyBroker()
    else:
        if not (cfg.api_key and cfg.api_secret):
            logger.error(
                "[short_whitelist] dry_run=False 인데 API key/secret 누락 (venue=%s)",
                cfg.venue,
            )
            return 2
        from src.brokers.async_rate_limiter import AsyncBinanceRateLimiter
        from src.brokers.binance.async_http import AsyncBinanceFuturesClient
        from src.live.airborne_trader.brokers import BinanceFuturesBroker

        rate_limiter = AsyncBinanceRateLimiter()
        client = AsyncBinanceFuturesClient(
            api_key=cfg.api_key,
            secret=cfg.api_secret,
            base_url=cfg.base_url,
            rate_limiter=rate_limiter,
        )
        logger.info(
            "[short_whitelist] LIVE — venue=%s base_url=%s",
            cfg.venue, cfg.base_url,
        )
        broker = BinanceFuturesBroker(client)

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
    p = argparse.ArgumentParser(prog="airborne_short_whitelist_daemon")
    p.add_argument("--dry-run", action="store_true",
                   help="force dry_run=True regardless of env")
    p.add_argument("--whitelist", default=None,
                   help=f"yaml 경로 (default: {DEFAULT_WHITELIST_PATH})")
    p.add_argument("--unlock-daily-kill", action="store_true",
                   help="daily loss kill switch 해제 후 종료")
    p.add_argument("--status", action="store_true",
                   help="state 출력 후 종료 — open positions / kill switch / today PnL")
    return p.parse_args(argv)


def _cmd_unlock_daily_kill() -> int:
    cfg = AirborneTraderConfig.from_env()
    cfg = _dc.replace(cfg, state_path=Path(os.environ.get(
        "AIRBORNE_SHORT_WHITELIST_STATE_PATH", str(DEFAULT_STATE_PATH),
    )))
    state = AirborneTraderState(path=cfg.state_path)
    try:
        last = state.last_kill_switch_event()
        if not state.is_kill_switch_active():
            print(f"활성 kill switch 없음 (last={last})")
            return 0
        ok = state.unlock_kill_switch(unlocked_by="cli")
        print(f"kill switch 해제: ok={ok}  prev={last}")
        return 0
    finally:
        state.close()


def _cmd_status() -> int:
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    cfg = AirborneTraderConfig.from_env()
    cfg = _dc.replace(cfg, state_path=Path(os.environ.get(
        "AIRBORNE_SHORT_WHITELIST_STATE_PATH", str(DEFAULT_STATE_PATH),
    )))
    state = AirborneTraderState(path=cfg.state_path)
    try:
        kst = ZoneInfo("Asia/Seoul")
        now = datetime.now(timezone.utc)
        kst_midnight = now.astimezone(kst).replace(
            hour=0, minute=0, second=0, microsecond=0,
        )
        midnight_utc = kst_midnight.astimezone(timezone.utc).isoformat()
        today_pnl = state.realized_pnl_since(midnight_utc)
        print(f"== AirborneShortWhitelist Status ({now.isoformat()}) ==")
        print(f"  state_path: {cfg.state_path}")
        print(f"  kill_switch_active: {state.is_kill_switch_active()}")
        print(f"  today realized PnL (KST 자정~): {today_pnl:+.2f} USDT "
              f"(limit {cfg.daily_loss_limit_usd:+.0f})")
        positions = state.list_open_positions()
        print(f"  open positions: {len(positions)} (max {cfg.max_concurrent_positions})")
        for p in positions:
            print(f"    #{p.id} {p.symbol} {p.side} qty={p.qty:.6f} "
                  f"entry={p.entry_px} stop={p.stop_px} tp={p.tp_px}")
        return 0
    finally:
        state.close()


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    if args.unlock_daily_kill:
        return _cmd_unlock_daily_kill()
    if args.status:
        return _cmd_status()
    try:
        return asyncio.run(_main_async(args))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
