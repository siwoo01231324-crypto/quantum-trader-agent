"""Bitget Demo / Mainnet 어댑터 smoke 검증 (P4 — pre-integration).

실제 trade 가동(live_run.py)에 Bitget 을 연결하기 전에 운영 환경에서 어댑터가
end-to-end 작동하는지 검증한다. live loop 통합은 P4b PR 에서 별도 진행.

검증 항목:
  1. ``_build_bitget_adapter`` 가 env vars 로부터 어댑터 빌드
  2. health_check  → OK
  3. get_balance   → USDT 잔고 (Demo 는 5,000 기본)
  4. get_positions → list 반환
  5. ensure_leverage(BTCUSDT, 1)
  6. (옵션) ``--place-test-order`` — LIMIT BUY 0.001 BTCUSDT @ $50,000 발주 후
     상태 확인 후 즉시 cancel (실 체결 X — 시장가에서 한참 떨어진 가격)

Usage::

    # Demo (paptrading + wspap subdomain)
    python scripts/bitget_smoke.py --broker bitget-demo

    # Demo + 실제 주문 1싸이클까지
    python scripts/bitget_smoke.py --broker bitget-demo --place-test-order

    # 메인넷 read-only (발주 안 함)
    python scripts/bitget_smoke.py --broker bitget-mainnet

Env vars (live_run.py 와 동일 chain):
  Demo:     BITGET_DEMO_API_KEY / BITGET_DEMO_SECRET / BITGET_DEMO_PASSPHRASE
  Mainnet:  BITGET_API_KEY      / BITGET_API_SECRET  / BITGET_API_PASSPHRASE
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _autoload_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for candidate in (Path.cwd(), _ROOT, _ROOT.parent):
        env = candidate / ".env"
        if env.exists():
            load_dotenv(env)
            return


_autoload_dotenv()

# Reuse the same builder live_run.py uses → env-var contract identical.
from scripts.live_run import _build_bitget_adapter  # noqa: E402


async def _run(broker_mode: str, place_test_order: bool) -> int:
    ad = _build_bitget_adapter(broker_mode)
    if ad is None:
        print(f"ERROR: broker_mode={broker_mode!r} did not return an adapter "
              f"(expected bitget-demo or bitget-mainnet)")
        return 2

    print(f"== Bitget smoke ({broker_mode}, paper={ad.paper}) ==")
    try:
        # 1. health
        h = await ad.health_check()
        print(f"  1. health_check          : {h}")
        if str(h) != "HealthStatus.OK":
            print("     ABORT — health not OK")
            return 3

        # 2. balance
        bals = await ad.get_balance()
        if not bals:
            print("  2. get_balance           : EMPTY — env vars correct?")
            return 4
        for b in bals:
            print(f"  2. get_balance           : {b.asset} free={b.free} locked={b.locked}")

        # 3. positions
        poss = await ad.get_positions()
        print(f"  3. get_positions         : {len(poss)} non-zero "
              f"({', '.join(p.symbol for p in poss[:5])}{'...' if len(poss) > 5 else ''})")

        # 4. leverage idempotent set
        await ad.ensure_leverage("BTCUSDT", 1)
        print("  4. ensure_leverage(1x)   : OK")

        # 5. optional test order
        if place_test_order:
            from src.brokers.base import OrderRequest, OrderType, PositionSide
            from src.execution.base import Side, TimeInForce

            req = OrderRequest(
                client_order_id="bgsmoke0001",
                symbol="BTCUSDT",
                side=Side.BUY,
                qty=Decimal("0.001"),
                order_type=OrderType.LIMIT,
                price=Decimal("50000.0"),
                tif=TimeInForce.GTC,
                position_side=PositionSide.BOTH,
            )
            ack = await ad.place_order(req)
            print(f"  5. place_order LIMIT BUY : oid={ack.broker_order_id} status={ack.status}")
            detail = await ad.get_order(symbol="BTCUSDT", broker_order_id=ack.broker_order_id)
            print(f"     get_order            : status={detail.status} filled={detail.filled_qty}")
            await ad.cancel_order(symbol="BTCUSDT", broker_order_id=ack.broker_order_id)
            print("     cancel_order         : OK")
            detail2 = await ad.get_order(symbol="BTCUSDT", broker_order_id=ack.broker_order_id)
            print(f"     get_order (after)    : status={detail2.status}")

        print("== ALL CHECKS PASSED ==")
        return 0
    finally:
        await ad.aclose()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument(
        "--broker", choices=["bitget-demo", "bitget-mainnet"],
        default="bitget-demo",
    )
    p.add_argument(
        "--place-test-order", action="store_true",
        help="LIMIT BUY 0.001 BTCUSDT @ $50,000 → cancel 1싸이클 검증 "
             "(시장가 한참 아래라 실 체결 위험 0). Demo 권장.",
    )
    args = p.parse_args(argv)
    return asyncio.run(_run(args.broker, args.place_test_order))


if __name__ == "__main__":
    sys.exit(main())
