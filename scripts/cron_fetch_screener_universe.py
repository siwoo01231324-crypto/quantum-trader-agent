"""일일 cron: 단타 검색식 universe (FDR 사전필터) 의 1분봉 + 일봉 lake 적재 (#230 옵션 B).

매일 KRX 마감 30분 후 (KST 16:00) 1회 실행하여 그 날의 1m + 일봉을 누적.
5거래일 누적 후 옵션 A 의 walk-forward 백테스트를 1일 → 5일 시계열로 확장 가능.

사용법:
    python scripts/cron_fetch_screener_universe.py                # full daily fetch
    python scripts/cron_fetch_screener_universe.py --dry-run      # universe + 추정 시간
    python scripts/cron_fetch_screener_universe.py --max-syms 100 # cap for testing
    python scripts/cron_fetch_screener_universe.py --rate-sleep 0.5  # slower for 안전

Windows Task Scheduler (PowerShell):
    schtasks /Create /SC DAILY /TN "QTA-Screener-Fetch" /ST 16:00 /D MON,TUE,WED,THU,FRI ^
        /TR "powershell -Command \"cd D:\\project\\quantum-trader-agent\\.worktree\\000230-hts-cond-eval; python scripts/cron_fetch_screener_universe.py > logs/screener-fetch.log 2>&1\""

환경변수: HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY, HANTOO_FAKE_CREDIT_NUMBER (.env autoload).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "src"))

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv(WORKTREE / ".env", override=False)
except ImportError:
    pass

KST = timezone(timedelta(hours=9))

# Re-use pilot script's primitives
from scripts.run_hts_cond_pilot import (
    build_universe_fdr,
    fetch_today_1m,
    refresh_daily_from_kis,
    _kis_client,
)


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now(KST).strftime("%Y-%m-%d")
    log_file = log_dir / f"screener_fetch_{today_str}.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    root.addHandler(logging.StreamHandler(sys.stdout))
    return log_file


def is_krx_trading_day(date) -> bool:
    """Best-effort: weekday + not in known holiday list. 정확히는 KRX 캘린더 사용."""
    try:
        from src.universe.krx_calendar import is_krx_holiday
        return date.weekday() < 5 and not is_krx_holiday(date)
    except ImportError:
        return date.weekday() < 5


def already_fetched(lake_dir: Path, symbol: str, today) -> bool:
    """Idempotency: today's partition 이 이미 있고 bars ≥ 200 (반나절 이상) 이면 skip."""
    import pandas as pd
    p = (lake_dir / "ohlcv" / "freq=1m"
         / f"year={today.year}" / f"month={today.month:02d}"
         / f"symbol={symbol}" / "part-0.parquet")
    if not p.exists():
        return False
    try:
        df = pd.read_parquet(p, columns=["ts"])
        df["ts_kst"] = pd.to_datetime(df["ts"]).dt.tz_convert("Asia/Seoul")
        today_rows = (df["ts_kst"].dt.date == today).sum()
        return today_rows >= 200
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lake-dir", default=str(WORKTREE.parent.parent / "lake"))
    ap.add_argument("--daily-cache", default=str(WORKTREE.parent.parent / "data" / "cache" / "krx_daily"))
    ap.add_argument("--log-dir", default=str(WORKTREE.parent.parent / "logs" / "screener-fetch"))
    ap.add_argument("--rate-sleep", type=float, default=0.3, help="Sleep between KIS calls (default 300ms)")
    ap.add_argument("--max-syms", type=int, default=0, help="Cap universe size (0=all, for testing)")
    ap.add_argument("--skip-daily-refresh", action="store_true", help="Skip daily cache refresh")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lake_dir = Path(args.lake_dir)
    daily_cache_dir = Path(args.daily_cache)
    log_file = setup_logging(Path(args.log_dir))
    log = logging.getLogger("cron_screener")

    today = datetime.now(KST).date()
    log.info("=" * 60)
    log.info("Screener universe fetch — %s", today.isoformat())
    log.info("=" * 60)

    if not is_krx_trading_day(today):
        log.info("Not a KRX trading day → skip.")
        return 0

    # 1. Universe (FDR pre-filter)
    log.info("Step 1: FDR universe pre-filter (A+B+C eod 근사)")
    universe = build_universe_fdr()
    if args.max_syms > 0:
        universe = universe[: args.max_syms]
    log.info("universe size: %d", len(universe))

    if args.dry_run:
        log.info("[DRY-RUN] est. calls: daily %d + 1m %d = %d", len(universe),
                 len(universe) * 13, len(universe) * 14)
        log.info("[DRY-RUN] est. time: %.1f min @ rate=%.2fs",
                 len(universe) * 14 * args.rate_sleep / 60.0, args.rate_sleep)
        return 0

    # 2. KIS client
    log.info("Step 2: KIS client init")
    try:
        client = _kis_client()
    except Exception as e:
        log.error("KIS client init failed: %s", e)
        return 1

    # 3. Daily refresh
    if not args.skip_daily_refresh:
        log.info("Step 3: Daily cache refresh")
        sym_list = [u.symbol for u in universe]
        try:
            refresh_daily_from_kis(client, sym_list, daily_cache_dir, args.rate_sleep)
        except Exception as e:
            log.error("Daily refresh failed: %s", e)
            # 계속 진행 — 1m fetch 가 더 중요

    # 4. 1m fetch (idempotent)
    log.info("Step 4: KIS 1m fetch (today)")
    ok, skipped, fail = 0, 0, 0
    for i, u in enumerate(universe):
        if already_fetched(lake_dir, u.symbol, today):
            skipped += 1
            continue
        try:
            n = fetch_today_1m(u.symbol, client, lake_dir)
            if n > 0:
                ok += 1
            else:
                fail += 1
        except Exception as e:
            log.warning("fetch fail %s: %s", u.symbol, e)
            fail += 1
        if (i + 1) % 50 == 0:
            log.info("  progress %d/%d (ok=%d skipped=%d fail=%d)",
                     i + 1, len(universe), ok, skipped, fail)
        time.sleep(args.rate_sleep)
    log.info("1m fetch done: ok=%d skipped=%d fail=%d", ok, skipped, fail)

    log.info("Cron complete. Log file: %s", log_file)
    return 0


if __name__ == "__main__":
    sys.exit(main())
