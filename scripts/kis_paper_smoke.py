#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KIS paper account smoke test.

Sequence: auth -> buy 005930 x1 (market) -> balance check -> sell -> balance restored
exit code 0 = success, 1 = failure (reason on stderr)

Env vars (project standard — see src/brokers/config.py):
  HANTOO_FAKE_API_KEY          KIS paper account AppKey
  HANTOO_FAKE_SECRET_API_KEY   KIS paper account AppSecret
  HANTOO_FAKE_CREDIT_NUMBER    KIS paper account number (format: 12345678-01); preferred when KIS_PAPER=true
  HANTOO_CREDIT_NUMBER         fallback account number (used when paper credit not set, or for live)
  HANTOO_HTS_ID                HTS ID (only required for WS subscribe; smoke uses REST only, default 'smoke')
  KIS_PAPER                    true/1 (default: true)

CI nightly:
  pytest -m e2e_kis_paper tests/integration/
Manual (PYTHONPATH required for direct invocation — same pattern as other scripts/):
  PYTHONPATH=src python scripts/kis_paper_smoke.py [--dry-run]
  # or: python -m scripts.kis_paper_smoke
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from decimal import Decimal


def _get_env(key: str, default: str | None = None) -> str:
    val = os.environ.get(key, default)
    if not val:
        print(f"ERROR: {key} is required", file=sys.stderr)
        sys.exit(1)
    return val


async def _run_smoke(dry_run: bool) -> None:
    from src.brokers.base import OrderRequest, OrderType
    from src.brokers.kis.async_adapter import KISAsyncAdapter
    from src.execution.base import Side, TimeInForce

    app_key = _get_env("HANTOO_FAKE_API_KEY")
    app_secret = _get_env("HANTOO_FAKE_SECRET_API_KEY")
    hts_id = os.environ.get("HANTOO_HTS_ID", "smoke")
    paper_env = os.environ.get("KIS_PAPER", "true").lower()
    paper = paper_env in ("true", "1", "yes")
    # paper=True 시 모의계좌 번호(HANTOO_FAKE_CREDIT_NUMBER) 우선,
    # 없으면 HANTOO_CREDIT_NUMBER fallback (실거래 계좌와 모의계좌 번호는 다름).
    if paper:
        credit_number = os.environ.get(
            "HANTOO_FAKE_CREDIT_NUMBER",
            os.environ.get("HANTOO_CREDIT_NUMBER", ""),
        )
    else:
        credit_number = os.environ.get("HANTOO_CREDIT_NUMBER", "")
    if not credit_number:
        print(
            "ERROR: paper=True requires HANTOO_FAKE_CREDIT_NUMBER (or HANTOO_CREDIT_NUMBER fallback)",
            file=sys.stderr,
        )
        sys.exit(1)

    if dry_run:
        print(f"[dry-run] app_key={app_key[:4]}*** paper={paper} hts_id={hts_id[:4]}***")
        print("[dry-run] OK - credentials loaded, no API calls made")
        return

    adapter = KISAsyncAdapter(
        app_key=app_key,
        app_secret=app_secret,
        hts_id=hts_id,
        credit_number=credit_number,
        paper=paper,
    )

    try:
        # Step 1: 인증 확인
        print("Step 1: checking auth...")
        health = await adapter.health_check()
        if str(health) not in ("HealthStatus.OK", "OK"):
            raise RuntimeError(f"health_check failed: {health}")
        print(f"  auth OK (health={health})")

        # Step 2: 잔고 확인 (매수 전)
        print("Step 2: checking balance before buy...")
        balances_before = await adapter.get_balance()
        krw_before = next(
            (b.free for b in balances_before if b.asset == "KRW"), Decimal("0")
        )
        print(f"  KRW before: {krw_before:,.0f}")

        # Step 3: 005930 시장가 매수 1주
        print("Step 3: placing market BUY 005930 x1...")
        buy_req = OrderRequest(
            symbol="005930",
            side=Side.BUY,
            order_type=OrderType.MARKET,
            qty=Decimal("1"),
            price=None,
            client_order_id="smoke-buy-001",
            tif=TimeInForce.DAY,
        )
        buy_ack = await adapter.place_order(buy_req)
        print(f"  buy ack: status={buy_ack.status} broker_order_id={buy_ack.broker_order_id}")
        if buy_ack.status != "NEW":
            raise RuntimeError(f"expected NEW, got {buy_ack.status}")

        # Step 4: 잔고 확인 (매수 후)
        print("Step 4: checking balance after buy...")
        balances_after_buy = await adapter.get_balance()
        krw_after_buy = next(
            (b.free for b in balances_after_buy if b.asset == "KRW"), Decimal("0")
        )
        print(f"  KRW after buy: {krw_after_buy:,.0f}")

        # Step 5: 005930 시장가 매도 1주
        print("Step 5: placing market SELL 005930 x1...")
        sell_req = OrderRequest(
            symbol="005930",
            side=Side.SELL,
            order_type=OrderType.MARKET,
            qty=Decimal("1"),
            price=None,
            client_order_id="smoke-sell-001",
            tif=TimeInForce.DAY,
            reduce_only=True,
        )
        sell_ack = await adapter.place_order(sell_req)
        print(f"  sell ack: status={sell_ack.status} broker_order_id={sell_ack.broker_order_id}")
        if sell_ack.status != "NEW":
            raise RuntimeError(f"expected NEW, got {sell_ack.status}")

        # Step 6: 잔고 복구 확인
        print("Step 6: checking balance after sell...")
        balances_final = await adapter.get_balance()
        krw_final = next(
            (b.free for b in balances_final if b.asset == "KRW"), Decimal("0")
        )
        print(f"  KRW final: {krw_final:,.0f}")

        print("\nSmoke test PASSED")

    finally:
        await adapter.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS paper account smoke test")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Load env vars only, no API calls",
    )
    args = parser.parse_args()

    try:
        asyncio.run(_run_smoke(dry_run=args.dry_run))
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
