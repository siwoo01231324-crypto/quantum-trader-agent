#!/usr/bin/env python3
"""Universe-scan 주간 rebal Telegram 디지스트 (#218 Phase 4).

`cs_rebalance_dispatch.telegram_digest_message` 의 결과 또는 JSON 페이로드를
LIVE 봇 단일 채널 (#214 fallback chain) 로 발송. 종목별 entry/exit 알림 폭주를
방지하고 주간 1건 합산 디지스트만 발송.

Usage (CLI):
  # JSON 페이로드 stdin 입력
  echo '{"strategy_id": "cs_tsmom_kr_daily", "n_buys": 4, "n_sells": 4,
         "n_held": 16, "buy_symbols": ["005930", "000660", "035420", "035720"],
         "sell_symbols": ["247540", "086520", "...", "..."]}' \\
    | python scripts/telegram_rebal.py

  # CLI args
  python scripts/telegram_rebal.py --strategy cs_tsmom_kr_daily \\
      --buys 005930,000660 --sells 247540,086520 --held 005380,000270

Library usage (from src/portfolio/cs_rebalance_dispatch.py):
  from scripts.telegram_rebal import send_rebal_digest
  send_rebal_digest(strategy_id, buys, sells, held, n_submitted, n_rejected)

Env (#214 / telegram_alert.py 와 동일 fallback chain):
  TELEGRAM_LIVE_BOT_TOKEN / TELEGRAM_LIVE_CHAT_ID  (1순위, 운영 표준)
  TELEGRAM_QTA_BOT_TOKEN  / TELEGRAM_QTA_CHAT_ID   (2순위)
  TELEGRAM_BOT_TOKEN      / TELEGRAM_CHAT_ID       (legacy)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

# 같은 scripts/ 디렉토리의 telegram_alert.send_telegram 재사용
sys.path.insert(0, str(Path(__file__).resolve().parent))
from telegram_alert import send_telegram  # noqa: E402

log = logging.getLogger("telegram_rebal")


def format_digest(
    strategy_id: str,
    buys: Iterable[str],
    sells: Iterable[str],
    held: Iterable[str],
    *,
    n_submitted: int = 0,
    n_rejected: int = 0,
    portfolio_pnl_pct: float | None = None,
    max_symbol_preview: int = 5,
) -> str:
    """Return Telegram 메시지 (Markdown).

    종목 리스트가 길면 처음 N 개만 표시 (메시지 길이 4096 제한 회피).
    """
    buys = list(buys)
    sells = list(sells)
    held = list(held)

    def _preview(items: list[str]) -> str:
        if not items:
            return "—"
        head = ", ".join(items[:max_symbol_preview])
        if len(items) > max_symbol_preview:
            head += f", ... (+{len(items) - max_symbol_preview})"
        return head

    lines = [
        f"*[REBAL]* `{strategy_id}`",
        f"  매수 {len(buys)}종: {_preview(buys)}",
        f"  매도 {len(sells)}종: {_preview(sells)}",
        f"  유지 {len(held)}종: {_preview(held)}",
    ]
    if n_submitted or n_rejected:
        lines.append(f"  주문: {n_submitted} 제출 / {n_rejected} 거부")
    if portfolio_pnl_pct is not None:
        sign = "+" if portfolio_pnl_pct >= 0 else ""
        lines.append(f"  주간 PnL: {sign}{portfolio_pnl_pct*100:.2f}%")
    return "\n".join(lines)


def send_rebal_digest(
    strategy_id: str,
    buys: Iterable[str],
    sells: Iterable[str],
    held: Iterable[str],
    *,
    n_submitted: int = 0,
    n_rejected: int = 0,
    portfolio_pnl_pct: float | None = None,
) -> bool:
    """Library 진입점. 메시지 포맷 + Telegram 발송."""
    msg = format_digest(
        strategy_id, buys, sells, held,
        n_submitted=n_submitted, n_rejected=n_rejected,
        portfolio_pnl_pct=portfolio_pnl_pct,
    )
    return send_telegram(msg, parse_mode="Markdown")


def main() -> int:
    p = argparse.ArgumentParser(description="Universe-scan rebal Telegram digest")
    p.add_argument("--strategy", help="strategy_id (e.g., cs_tsmom_kr_daily)")
    p.add_argument("--buys", default="", help="comma-sep 매수 종목")
    p.add_argument("--sells", default="", help="comma-sep 매도 종목")
    p.add_argument("--held", default="", help="comma-sep 유지 종목")
    p.add_argument("--submitted", type=int, default=0)
    p.add_argument("--rejected", type=int, default=0)
    p.add_argument("--pnl", type=float, default=None,
                   help="주간 PnL fraction (e.g., 0.025 = +2.5%%)")
    p.add_argument("--from-stdin", action="store_true",
                   help="JSON 페이로드를 stdin 으로 받음")
    p.add_argument("--dry-run", action="store_true",
                   help="발송 안 하고 메시지만 출력")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO)

    if args.from_stdin:
        payload = json.load(sys.stdin)
        strategy = payload.get("strategy_id", "")
        buys = payload.get("buy_symbols", [])
        sells = payload.get("sell_symbols", [])
        held = payload.get("hold_symbols", [])
        submitted = payload.get("n_submitted", 0)
        rejected = payload.get("n_rejected", 0)
        pnl = payload.get("portfolio_pnl_pct")
    else:
        if not args.strategy:
            p.error("--strategy required (or --from-stdin)")
        strategy = args.strategy
        buys = [s for s in args.buys.split(",") if s]
        sells = [s for s in args.sells.split(",") if s]
        held = [s for s in args.held.split(",") if s]
        submitted, rejected = args.submitted, args.rejected
        pnl = args.pnl

    msg = format_digest(strategy, buys, sells, held,
                        n_submitted=submitted, n_rejected=rejected,
                        portfolio_pnl_pct=pnl)
    if args.dry_run:
        print(msg)
        return 0
    ok = send_telegram(msg, parse_mode="Markdown")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
