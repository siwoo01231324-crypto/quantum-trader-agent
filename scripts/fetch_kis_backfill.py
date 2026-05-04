"""Backfill KIS intraday OHLCV bars for a pool of KRX symbols.

Fetches up to 30 days of intraday minute bars for N symbols selected from the
KOSPI200 pool and writes hive-partitioned Parquet files to the data lake.

Usage
-----
    # Dry-run: print pool symbols + estimated requests + planned paths, exit 0
    python scripts/fetch_kis_backfill.py --dry-run --n-symbols 5

    # Live backfill (requires KIS credentials in env):
    python scripts/fetch_kis_backfill.py --n-symbols 30 --interval 1m

    # Manual symbol list:
    python scripts/fetch_kis_backfill.py --symbols 005930,000660

Required env vars (live mode only):
    KIS_APP_KEY      Application key from KIS developer portal
    KIS_APP_SECRET   Application secret
    KIS_CANO         Account number (계좌번호 앞 8자리)
    KIS_ACNT_PRDT_CD Account product code (계좌번호 뒤 2자리, e.g. "01")

Notes
-----
- KIS intraday API limit: last 30 days only.
- --async-concurrency > 1 uses asyncio.Semaphore; max 2 per KIS rate limit.
- Synthetic fixtures in tests/ are for unit testing only — do not use for
  production verdict.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))
sys.path.insert(0, str(WORKTREE))

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

# Module-level imports so tests can patch fetch_kis_backfill.<name>
try:
    from data_lake.fetcher import fetch_kis_intraday_ohlcv, save_ohlcv_parquet
except ImportError:
    fetch_kis_intraday_ohlcv = None  # type: ignore[assignment]
    save_ohlcv_parquet = None  # type: ignore[assignment]

_BACKFILL_DAYS = 30


def _date_range_last_n(n: int) -> tuple[str, str]:
    """Return (start, end) ISO date strings covering the last n calendar days."""
    kst_offset = timezone(timedelta(hours=9))
    today = datetime.now(tz=kst_offset).date()
    start = today - timedelta(days=n - 1)
    return start.isoformat(), today.isoformat()


def _partition_preview(lake_dir: Path, symbol: str, interval: str, year: int, month: int) -> str:
    freq = interval if interval.endswith("m") else f"{interval}m"
    return str(
        lake_dir / "ohlcv" / f"freq={freq}"
        / f"year={year}" / f"month={month:02d}"
        / f"symbol={symbol}" / "part-0.parquet"
    )


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    from universe.krx_pool import get_pool_codes
    return get_pool_codes(n=args.n_symbols, seed=args.seed)


def _run_dry(args: argparse.Namespace) -> int:
    symbols = _resolve_symbols(args)
    start, end = _date_range_last_n(_BACKFILL_DAYS)
    lake_dir = Path(args.lake_dir)

    from datetime import date as _date
    import pandas as pd
    # Estimate trading days (rough: 5/7 of calendar days, no holiday correction)
    start_d = _date.fromisoformat(start)
    end_d = _date.fromisoformat(end)
    trading_days = len(pd.bdate_range(start, end))
    estimated_requests = len(symbols) * trading_days

    kst_offset = timezone(timedelta(hours=9))
    today = datetime.now(tz=kst_offset)

    print("[DRY-RUN] KIS intraday backfill simulation")
    print(f"  symbols ({len(symbols)}): {', '.join(symbols)}")
    print(f"  interval        : {args.interval}")
    print(f"  date range      : {start} → {end} ({trading_days} est. trading days)")
    print(f"  est. requests   : {estimated_requests}")
    print(f"  concurrency     : {args.async_concurrency}")
    print(f"  sleep-between   : {args.sleep_between}s")
    print()
    print("  Planned partition paths (year/month of today):")
    path = _partition_preview(lake_dir, symbols[0] if symbols else "XXXXX", args.interval, today.year, today.month)
    print(f"    example: {path}")
    print("[DRY-RUN] No API call made. exit 0.")
    return 0


def _fetch_one(symbol: str, start: str, end: str, args: argparse.Namespace,
               auth: object, app_key: str, app_secret: str,
               cano: str, acnt_prdt_cd: str) -> None:
    interval_min = args.interval.rstrip("m")
    paper = os.environ.get("KIS_PAPER", "false").lower() == "true"
    df = fetch_kis_intraday_ohlcv(
        symbol=symbol,
        start=start,
        end=end,
        interval=interval_min,
        auth=auth,
        app_key=app_key,
        app_secret=app_secret,
        cano=cano,
        acnt_prdt_cd=acnt_prdt_cd,
        paper=paper,
    )
    if df.empty:
        log.warning("No bars returned for %s (%s → %s)", symbol, start, end)
        return

    lake_dir = Path(args.lake_dir)
    freq = args.interval if args.interval.endswith("m") else f"{args.interval}m"
    written = save_ohlcv_parquet(df, lake_dir, symbol, freq)
    log.info("  %s: %d bars → %s", symbol, len(df), [str(p) for p in written])


async def _fetch_async(symbols: list[str], start: str, end: str, args: argparse.Namespace,
                       auth: object, app_key: str, app_secret: str,
                       cano: str, acnt_prdt_cd: str) -> None:
    sem = asyncio.Semaphore(min(args.async_concurrency, 2))  # KIS rate limit max 2

    async def _bounded(symbol: str) -> None:
        async with sem:
            await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _fetch_one(symbol, start, end, args, auth,
                                   app_key, app_secret, cano, acnt_prdt_cd),
            )

    await asyncio.gather(*[_bounded(sym) for sym in symbols])


def _resolve_kis_credentials() -> tuple[str, str, str, str, bool]:
    """KIS_* 우선, 없으면 HANTOO_FAKE_* (paper) 폴백.

    Returns
    -------
    (app_key, app_secret, cano, acnt_prdt_cd, paper_flag)
    """
    app_key = os.environ.get("KIS_APP_KEY") or os.environ.get("HANTOO_FAKE_API_KEY", "")
    app_secret = os.environ.get("KIS_APP_SECRET") or os.environ.get("HANTOO_FAKE_SECRET_API_KEY", "")
    cano = os.environ.get("KIS_CANO", "")
    acnt_prdt_cd = os.environ.get("KIS_ACNT_PRDT_CD", "")

    # HANTOO_FAKE_CREDIT_NUMBER ("XXXXXXXX-XX") 으로 cano/acnt_prdt_cd 보강
    if not cano:
        credit = (
            os.environ.get("HANTOO_FAKE_CREDIT_NUMBER")
            or os.environ.get("HANTOO_CREDIT_NUMBER", "")
        )
        if credit:
            try:
                from brokers.kis.async_adapter import _parse_credit_number
                cano, acnt_prdt_cd_parsed = _parse_credit_number(credit)
                if not acnt_prdt_cd:
                    acnt_prdt_cd = acnt_prdt_cd_parsed
            except Exception:
                pass

    if not acnt_prdt_cd:
        acnt_prdt_cd = "01"

    # HANTOO_FAKE_* 사용 시 자동 paper. KIS_PAPER 가 명시되면 우선.
    if "KIS_PAPER" in os.environ:
        paper = os.environ["KIS_PAPER"].lower() == "true"
    else:
        paper = bool(os.environ.get("HANTOO_FAKE_API_KEY"))

    return app_key, app_secret, cano, acnt_prdt_cd, paper


def _run_live(args: argparse.Namespace) -> int:
    app_key, app_secret, cano, acnt_prdt_cd, paper = _resolve_kis_credentials()

    if not all([app_key, app_secret, cano]):
        print(
            "KIS credentials missing. Skipping backfill. "
            "Set either KIS_APP_KEY/KIS_APP_SECRET/KIS_CANO or "
            "HANTOO_FAKE_API_KEY/HANTOO_FAKE_SECRET_API_KEY/HANTOO_FAKE_CREDIT_NUMBER. "
            "Synthetic fixture is for tests/ only — do not use for production verdict.",
            file=sys.stderr,
        )
        return 0

    try:
        from brokers.kis.auth import KISAuth
    except ImportError as exc:
        print(f"[ERROR] Import failed: {exc}", file=sys.stderr)
        return 1
    try:
        auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=paper)
    except Exception as exc:
        print(
            f"KIS_TOKEN env not set. Skipping backfill. "
            f"To enable: set KIS_APP_KEY/KIS_APP_SECRET. "
            f"Synthetic fixture is for tests/ only — do not use for production verdict. "
            f"({exc})",
            file=sys.stderr,
        )
        return 0

    symbols = _resolve_symbols(args)
    start, end = _date_range_last_n(_BACKFILL_DAYS)

    log.info("Starting KIS backfill: %d symbols, %s → %s, interval=%s",
             len(symbols), start, end, args.interval)

    if args.async_concurrency > 1:
        asyncio.run(_fetch_async(symbols, start, end, args, auth,
                                 app_key, app_secret, cano, acnt_prdt_cd))
    else:
        for i, symbol in enumerate(symbols):
            if i > 0:
                time.sleep(args.sleep_between)
            _fetch_one(symbol, start, end, args, auth,
                       app_key, app_secret, cano, acnt_prdt_cd)

    log.info("Backfill complete.")
    return 0


def _make_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build argparse Namespace from argv list (or sys.argv). Used by tests."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--n-symbols", type=int, default=30)
    parser.add_argument("--symbols", default="")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--lake-dir", default=str(WORKTREE / "lake"))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--sleep-between", type=float, default=0.6)
    parser.add_argument("--async-concurrency", type=int, default=1)
    return parser.parse_args(argv)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Backfill KIS intraday OHLCV for a pool of KRX symbols.",
    )
    parser.add_argument("--n-symbols", type=int, default=30,
                        help="Number of symbols to select from krx_pool. Default: 30.")
    parser.add_argument("--symbols", default="",
                        help="Comma-separated manual symbol list (overrides --n-symbols).")
    parser.add_argument("--interval", default="1m",
                        help="Bar interval. Default: 1m. Options: 1m, 3m, 5m, 10m, 15m, 30m, 60m.")
    parser.add_argument("--lake-dir", default=str(WORKTREE / "lake"),
                        help="Root directory of the data lake. Default: <repo>/lake.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned actions and exit 0 without API calls.")
    parser.add_argument("--seed", type=int, default=42,
                        help="Random seed for krx_pool sampling. Default: 42.")
    parser.add_argument("--sleep-between", type=float, default=0.6,
                        help="Seconds to sleep between symbol fetches (serial mode). Default: 0.6.")
    parser.add_argument("--async-concurrency", type=int, default=1,
                        help="Async concurrency (max 2 per KIS rate limit). Default: 1 (serial).")
    args = parser.parse_args()

    if args.dry_run:
        return _run_dry(args)
    return _run_live(args)


if __name__ == "__main__":
    sys.exit(main())
