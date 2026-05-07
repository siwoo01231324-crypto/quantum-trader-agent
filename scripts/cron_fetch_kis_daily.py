"""Daily cron script: fetch KIS intraday OHLCV bars and write to the data lake.

Fetches KRX 15-minute bars for a single symbol (default 005930 / Samsung Electronics)
after market close and appends them to the hive-partitioned Parquet lake.

Cron registration guide
-----------------------
Linux / macOS (crontab):
    # Edit with: crontab -e
    # Run at 16:00 KST (UTC+9 = 07:00 UTC) every weekday (Mon-Fri)
    0 7 * * 1-5 KIS_TOKEN=<token> /path/to/venv/bin/python /path/to/scripts/cron_fetch_kis_daily.py >> /var/log/kis_fetch.log 2>&1

    Required env vars (set in crontab or via a secrets manager):
        KIS_TOKEN        Bearer access token (or use KIS_APP_KEY + KIS_APP_SECRET for auto-refresh)
        KIS_APP_KEY      Application key from KIS developer portal
        KIS_APP_SECRET   Application secret
        KIS_CANO         Account number (계좌번호 앞 8자리)
        KIS_ACNT_PRDT_CD Account product code (계좌번호 뒤 2자리, e.g. "01")

Windows Task Scheduler:
    1. Open Task Scheduler → Create Basic Task
    2. Trigger: Daily, start time 16:05 KST, repeat Mon-Fri only
       (Conditions → uncheck "Run only if on AC power" for servers)
    3. Action: Start a program
         Program: C:\\path\\to\\venv\\Scripts\\python.exe
         Arguments: C:\\path\\to\\scripts\\cron_fetch_kis_daily.py
         Start in: C:\\path\\to\\project
    4. Set environment variables in the task's environment or via a .env loader.

Usage
-----
    # Dry-run (no API calls, no files written):
    python scripts/cron_fetch_kis_daily.py --dry-run

    # Live fetch (requires KIS credentials in env):
    python scripts/cron_fetch_kis_daily.py --symbol 005930 --interval 15m

    # Multi-symbol via pool:
    python scripts/cron_fetch_kis_daily.py --n-pool 30 --interval 1m

    # Multi-symbol manual list:
    python scripts/cron_fetch_kis_daily.py --symbols 005930,000660,035720 --interval 1m

    # Override lake root:
    python scripts/cron_fetch_kis_daily.py --lake-dir /mnt/data/lake
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — allow running from project root or scripts/ directory
# ---------------------------------------------------------------------------
WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)


def _today_kst() -> datetime:
    """Return current date in KST (UTC+9)."""
    kst_offset = timezone(timedelta(hours=9))
    return datetime.now(tz=kst_offset)


def _partition_preview(lake_dir: Path, symbol: str, interval: str, date: datetime) -> str:
    """Return the expected partition path for a given date."""
    freq = interval if interval.endswith("m") else f"{interval}m"
    return str(
        lake_dir
        / "ohlcv"
        / f"freq={freq}"
        / f"year={date.year}"
        / f"month={date.month:02d}"
        / f"symbol={symbol}"
        / "part-0.parquet"
    )


def _count_lake_bars(lake_dir: Path, symbol: str, interval: str) -> int:
    """Count total rows across all existing lake parquet files for this symbol."""
    try:
        import pyarrow.parquet as pq
    except ImportError:
        return -1

    freq = interval if interval.endswith("m") else f"{interval}m"
    pattern_dir = lake_dir / "ohlcv" / f"freq={freq}"
    if not pattern_dir.exists():
        return 0

    total = 0
    for part_file in pattern_dir.rglob(f"symbol={symbol}/part-0.parquet"):
        try:
            total += pq.read_metadata(part_file).num_rows
        except Exception:
            pass
    return total


def _resolve_symbols(args: argparse.Namespace) -> list[str]:
    """Return the list of symbols to fetch based on args priority."""
    if args.symbols:
        return [s.strip() for s in args.symbols.split(",") if s.strip()]
    if args.n_pool > 0:
        WORKTREE = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(WORKTREE / "src"))
        sys.path.insert(0, str(WORKTREE))
        from universe.krx_pool import get_pool_codes
        return get_pool_codes(n=args.n_pool)
    return [args.symbol]


def resolve_kis_credentials(env: dict[str, str]) -> tuple[str, str, str, str]:
    """Return (app_key, app_secret, cano, acnt_prdt_cd) from env vars.

    Primary: HANTOO_FAKE_* (matches src/brokers/config.py and #133 .env layout).
    Fallback: KIS_APP_KEY / KIS_APP_SECRET / KIS_CANO / KIS_ACNT_PRDT_CD (legacy).

    HANTOO_FAKE_CREDIT_NUMBER / HANTOO_CREDIT_NUMBER (or KIS_CREDIT_NUMBER) accepts
    the dash format 'NNNNNNNN-NN' — split into cano (8 digits) + acnt_prdt_cd (2 digits).
    Falls back to separate KIS_CANO / KIS_ACNT_PRDT_CD when the dash form is absent.

    Missing values are returned as empty strings (caller decides to abort).
    """
    app_key = env.get("HANTOO_FAKE_API_KEY") or env.get("KIS_APP_KEY") or ""
    app_secret = env.get("HANTOO_FAKE_SECRET_API_KEY") or env.get("KIS_APP_SECRET") or ""

    credit = (
        env.get("HANTOO_FAKE_CREDIT_NUMBER")
        or env.get("HANTOO_CREDIT_NUMBER")
        or env.get("KIS_CREDIT_NUMBER")
        or ""
    )
    if credit and "-" in credit:
        parts = credit.split("-", 1)
        cano = parts[0]
        acnt_prdt_cd = parts[1]
    else:
        cano = env.get("KIS_CANO") or ""
        acnt_prdt_cd = env.get("KIS_ACNT_PRDT_CD") or "01"

    return app_key, app_secret, cano, acnt_prdt_cd


def _run_dry(args: argparse.Namespace) -> int:
    """Print what would be fetched without making any API calls."""
    today = _today_kst()
    fetch_date = today.strftime("%Y-%m-%d")

    lake_dir = Path(args.lake_dir)
    symbols = _resolve_symbols(args)

    print("[DRY-RUN] KIS daily intraday fetch simulation")
    print(f"  symbols  : {', '.join(symbols)} ({len(symbols)} total)")
    print(f"  interval : {args.interval}")
    print(f"  date     : {fetch_date} (KST)")
    for sym in symbols:
        planned_path = _partition_preview(lake_dir, sym, args.interval, today)
        existing_bars = _count_lake_bars(lake_dir, sym, args.interval)
        print(f"  [{sym}] planned partition : {planned_path}")
        print(f"  [{sym}] existing lake bars: {existing_bars if existing_bars >= 0 else '(pyarrow not installed)'}")
    print("[DRY-RUN] No API call made. exit 0.")
    return 0


def _run_live(args: argparse.Namespace) -> int:
    """Fetch real KIS data and write parquet partitions."""
    # -----------------------------------------------------------------------
    # Credential check — graceful exit on missing token (cron-friendly)
    # -----------------------------------------------------------------------
    app_key, app_secret, cano, acnt_prdt_cd = resolve_kis_credentials(dict(os.environ))

    if not all([app_key, app_secret, cano]):
        print(
            "[WARN] KIS credentials not set (HANTOO_FAKE_API_KEY / HANTOO_FAKE_SECRET_API_KEY / "
            "HANTOO_CREDIT_NUMBER, or legacy KIS_APP_KEY / KIS_APP_SECRET / KIS_CANO). "
            "Skipping fetch. Set env vars and re-run.",
            file=sys.stderr,
        )
        return 0  # exit 0 so cron daemon does not spam failure alerts

    try:
        from data_lake.fetcher import fetch_kis_intraday_ohlcv, save_ohlcv_parquet
        from brokers.kis.auth import KISAuth
    except ImportError as exc:
        print(f"[ERROR] Import failed: {exc}", file=sys.stderr)
        return 1

    today_kst = _today_kst()
    fetch_date = today_kst.strftime("%Y-%m-%d")
    interval_min = args.interval.rstrip("m")
    freq = args.interval if args.interval.endswith("m") else f"{args.interval}m"
    lake_dir = Path(args.lake_dir)

    try:
        auth = KISAuth(app_key=app_key, app_secret=app_secret, paper=True)
    except Exception as exc:
        print(f"[ERROR] KISAuth init failed: {exc}", file=sys.stderr)
        return 1

    symbols = _resolve_symbols(args)

    import time as _time
    for i, symbol in enumerate(symbols):
        if i > 0:
            _time.sleep(args.sleep_between)

        try:
            df = fetch_kis_intraday_ohlcv(
                symbol=symbol,
                start=fetch_date,
                end=fetch_date,
                interval=interval_min,
                auth=auth,
                app_key=app_key,
                app_secret=app_secret,
                cano=cano,
                acnt_prdt_cd=acnt_prdt_cd,
                paper=True,
            )
        except Exception as exc:
            print(f"[ERROR] KIS API fetch failed for {symbol}: {exc}", file=sys.stderr)
            continue

        if df.empty:
            print(f"[WARN] No bars returned for {symbol} on {fetch_date}.")
            continue

        try:
            written = save_ohlcv_parquet(df, lake_dir, symbol, freq)
        except Exception as exc:
            print(f"[ERROR] Failed to write parquet for {symbol}: {exc}", file=sys.stderr)
            continue

        existing_bars = _count_lake_bars(lake_dir, symbol, args.interval)
        print(f"[OK] Fetched {len(df)} bars for {symbol} on {fetch_date}")
        print(f"     date range : {df['ts'].min()} → {df['ts'].max()}")
        print(f"     written    : {[str(p) for p in written]}")
        print(f"     lake total : {existing_bars} bars (all time)")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch KIS intraday OHLCV bars and write to the data lake (daily cron).",
    )
    parser.add_argument(
        "--symbol",
        default="005930",
        help="KRX stock code (6 digits). Default: 005930 (Samsung Electronics). Single-symbol mode.",
    )
    parser.add_argument(
        "--n-pool",
        type=int,
        default=0,
        help="Auto-select N symbols from krx_pool (overrides --symbol). Default: 0 (disabled).",
    )
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated manual symbol list (overrides --symbol and --n-pool).",
    )
    parser.add_argument(
        "--interval",
        default="15m",
        help="Bar interval. Default: 15m. Options: 1m, 3m, 5m, 10m, 15m, 30m, 60m.",
    )
    parser.add_argument(
        "--lake-dir",
        default=str(WORKTREE / "lake"),
        help="Root directory of the data lake. Default: <repo>/lake.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned partition path and exit 0 without making any API calls.",
    )
    parser.add_argument(
        "--sleep-between",
        type=float,
        default=0.6,
        help="Seconds to sleep between symbol fetches (multi-symbol mode). Default: 0.6.",
    )
    parser.add_argument(
        "--auth-token-env",
        default="KIS_TOKEN",
        help="Env var name for the KIS bearer token (informational; actual auth uses KIS_APP_KEY/SECRET).",
    )
    args = parser.parse_args()

    if args.dry_run:
        return _run_dry(args)
    return _run_live(args)


if __name__ == "__main__":
    sys.exit(main())
