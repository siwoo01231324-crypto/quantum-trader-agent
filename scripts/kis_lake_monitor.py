"""KIS lake 누적 모니터 (#152).

`scripts/cron_fetch_kis_daily.py` 가 매일 적재하는 hive-partitioned 분봉 lake 의
누적 진척도를 종목별로 집계하고 Telegram digest 용 markdown 으로 렌더링한다.

Lake 레이아웃 (matches data_lake.fetcher.save_ohlcv_parquet):
    lake/ohlcv/freq={freq}/year=YYYY/month=MM/symbol=SYMBOL/part-0.parquet

Usage
-----
    # 표준 출력에 markdown
    python scripts/kis_lake_monitor.py --lake-dir lake/ --interval 1m

    # 파일에 기록 (주간 02_implementation.md 갱신)
    python scripts/kis_lake_monitor.py --lake-dir lake/ --interval 1m \\
        --out docs/work/active/000152-kis-1m-cron-ops/02_implementation.md

    # Telegram 발송 (TELEGRAM_BOT_TOKEN/CHAT_ID 필요)
    python scripts/kis_lake_monitor.py --lake-dir lake/ --interval 1m --telegram
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

DEFAULT_TARGET_DAYS = 90
SCHEMA_COLUMNS = ("symbol", "n_bars", "n_days", "first_ts", "last_ts")


_KRX_SYMBOL_RE = __import__("re").compile(r"^\d{6}$")


def scan_lake(
    lake_dir: Path,
    interval: str = "1m",
    *,
    krx_only: bool = True,
) -> pd.DataFrame:
    """Walk lake/ohlcv/freq={interval}/.../symbol=*/part-0.parquet and return
    per-symbol stats (symbol, n_bars, n_days, first_ts, last_ts).

    Args:
        krx_only: when True (default — matches the "KIS 1분봉 누적 모니터" intent),
            skip symbols whose name does not match the 6-digit KRX format. The
            same `lake/ohlcv/freq=1m/.../symbol=*` partition is also used by
            Binance backfill (`fetch_alt_universe_ohlcv.py`) and BTC/ETH 5y
            datasets, so without this filter the monitor would pick up
            ``BTCUSDT`` / ``ETHUSDT`` and report nonsensical "2192 거래일"
            against KIS-only target.
    """
    lake_dir = Path(lake_dir)
    freq_dir = lake_dir / "ohlcv" / f"freq={interval}"
    if not freq_dir.exists():
        return pd.DataFrame(columns=list(SCHEMA_COLUMNS))

    rows: dict[str, dict] = {}
    for part in freq_dir.rglob("symbol=*/part-0.parquet"):
        sym = part.parent.name.split("=", 1)[1]
        if krx_only and not _KRX_SYMBOL_RE.match(sym):
            continue
        try:
            df = pd.read_parquet(part, columns=["ts"])
        except Exception:
            continue
        if df.empty:
            continue
        ts = pd.to_datetime(df["ts"], utc=True)
        days = ts.dt.normalize().unique()
        bucket = rows.setdefault(sym, {"bars": 0, "days": set(), "min_ts": None, "max_ts": None})
        bucket["bars"] += int(len(df))
        bucket["days"].update(pd.to_datetime(days, utc=True))
        bucket["min_ts"] = min(bucket["min_ts"], ts.min()) if bucket["min_ts"] is not None else ts.min()
        bucket["max_ts"] = max(bucket["max_ts"], ts.max()) if bucket["max_ts"] is not None else ts.max()

    if not rows:
        return pd.DataFrame(columns=list(SCHEMA_COLUMNS))

    records = [
        {
            "symbol": sym,
            "n_bars": v["bars"],
            "n_days": len(v["days"]),
            "first_ts": v["min_ts"],
            "last_ts": v["max_ts"],
        }
        for sym, v in sorted(rows.items())
    ]
    return pd.DataFrame.from_records(records)


def aggregate_stats(df: pd.DataFrame, *, target_days: int = DEFAULT_TARGET_DAYS) -> dict:
    """Roll up per-symbol stats into a single dict for the markdown header."""
    if df.empty:
        return {
            "n_symbols": 0,
            "total_bars": 0,
            "max_days": 0,
            "last_ts": None,
            "progress_pct": 0.0,
            "target_days": target_days,
        }
    max_days = int(df["n_days"].max())
    return {
        "n_symbols": int(len(df)),
        "total_bars": int(df["n_bars"].sum()),
        "max_days": max_days,
        "last_ts": df["last_ts"].max(),
        "progress_pct": round(100.0 * max_days / target_days, 2) if target_days else 0.0,
        "target_days": target_days,
    }


def render_markdown(df: pd.DataFrame, agg: dict) -> str:
    """Render telegram-friendly markdown digest."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.append(f"# KIS 1분봉 누적 모니터")
    lines.append(f"> 생성: {now}")
    lines.append("")

    lines.append("## 진척도")
    if agg["n_symbols"] == 0:
        lines.append("- 데이터 없음 (lake 비어있음 또는 fetch 미시작)")
    else:
        last_ts = agg["last_ts"]
        last_str = pd.Timestamp(last_ts).strftime("%Y-%m-%d %H:%M UTC") if last_ts is not None else "-"
        lines.append(f"- 누적 거래일: **{agg['max_days']}/{agg['target_days']}** ({agg['progress_pct']}%)")
        lines.append(f"- 종목 수: {agg['n_symbols']}")
        lines.append(f"- 총 bars: {agg['total_bars']:,}")
        lines.append(f"- 마지막 fetch: {last_str}")
    lines.append("")

    lines.append("## 종목별 누적")
    if df.empty:
        lines.append("- (없음)")
    else:
        lines.append("| 종목 | bars | 거래일 | 첫 fetch | 마지막 fetch |")
        lines.append("|------|------|--------|----------|--------------|")
        for _, row in df.iterrows():
            first = pd.Timestamp(row["first_ts"]).strftime("%Y-%m-%d") if row["first_ts"] is not None else "-"
            last = pd.Timestamp(row["last_ts"]).strftime("%Y-%m-%d") if row["last_ts"] is not None else "-"
            lines.append(
                f"| {row['symbol']} | {row['n_bars']:,} | {row['n_days']} | {first} | {last} |"
            )
    lines.append("")
    return "\n".join(lines)


def _send_telegram(text: str) -> bool:
    """Telegram 발송 (telegram_alert.send_telegram 재사용). 실패 시 False."""
    sys.path.insert(0, str(WORKTREE / "scripts"))
    try:
        from telegram_alert import send_telegram  # type: ignore
    except ImportError:
        print("[WARN] telegram_alert import 실패 — 텔레그램 발송 skip", file=sys.stderr)
        return False
    return send_telegram(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KIS 1분봉 lake 누적 모니터 (#152)")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--target-days", type=int, default=DEFAULT_TARGET_DAYS)
    parser.add_argument("--out", type=Path, default=None, help="markdown 출력 경로 (없으면 stdout)")
    parser.add_argument("--telegram", action="store_true", help="markdown 을 Telegram 으로 발송")
    parser.add_argument(
        "--include-non-krx", action="store_true",
        help="KRX 6자리 종목 외 (BTCUSDT 등 백테스트 데이터) 도 포함. default=False — KIS 1분봉 모니터 의도",
    )
    args = parser.parse_args(argv)

    df = scan_lake(args.lake_dir, interval=args.interval, krx_only=not args.include_non_krx)
    agg = aggregate_stats(df, target_days=args.target_days)
    md = render_markdown(df, agg)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"[monitor] markdown written to {args.out}", file=sys.stderr)
    else:
        print(md)

    if args.telegram:
        ok = _send_telegram(md)
        print(f"[monitor] telegram: {'sent' if ok else 'skipped/failed'}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
