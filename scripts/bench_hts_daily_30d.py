"""HTS 검색식 30일 daily-level backtest (#230 본검증 proxy).

KIS 1분봉 API 가 당일만 반환 → 30일 intraday backtest 불가. 대안으로 일봉 cache (350+
종목 × 70일+) 로 daily-level evaluation 수행.

Profile:
  - A. SWING-only (정확): A~G 일간 조건만, H 없음 → 가능한 가장 정확한 30일 backtest
  - B. hybrid_daily_or (근사): SWING ∪ WAIT5M-daily 근사 (today_close ≥ prev_close × 1.067)
                              DTS H 는 daily 로 평가 불가 (3분봉 필요) → 본 근사에 미포함

Entry/Exit:
  - Entry: day D 종가 매수 (signal 발생 일자)
  - Hold to day D+1
  - TP: day D+1 high ≥ entry × 1.02 → 익절
  - SL: day D+1 low ≤ entry × 0.98 → 손절 (동시 도달 시 손절 우선)
  - 미체결: day D+1 종가 청산
  - 비용: 수수료 0.015% + 슬리피지 0.05% (양방향)

사용:
    python scripts/bench_hts_daily_30d.py --days 30
    python scripts/bench_hts_daily_30d.py --days 10 --report-dir tmp/
"""
from __future__ import annotations

import argparse
import csv as _csv
import logging
import sys
from datetime import datetime, timedelta, timezone, date as _date
from pathlib import Path
from typing import Iterable

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "src"))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    datefmt="%Y-%m-%dT%H:%M:%S")
log = logging.getLogger("bench_30d")

KST = timezone(timedelta(hours=9))

from src.screeners.hts_cond.common import (
    DailyScreeningInputs,
    PROFILE_DTS, PROFILE_WAIT5M, PROFILE_SWING,
    common_passes, evaluate_common,
)


CACHE_DEFAULT = WORKTREE.parent.parent / "data" / "cache" / "krx_daily"
REPORT_DEFAULT = WORKTREE / "docs" / "work" / "active" / "000230-hts-cond-eval"


def get_recent_krx_trading_days(n_days: int, end_date=None) -> list[_date]:
    if end_date is None:
        end_date = datetime.now(KST).date()
    try:
        from src.universe.krx_calendar import is_krx_holiday
    except ImportError:
        def is_krx_holiday(d):  # type: ignore
            return False
    days: list[_date] = []
    d = end_date
    limit = n_days * 3 + 30
    while len(days) < n_days and limit > 0:
        if d.weekday() < 5 and not is_krx_holiday(d):
            days.append(d)
        d -= timedelta(days=1)
        limit -= 1
    return list(reversed(days))


def daily_inputs_for_date(df_full, target_date: _date) -> tuple | None:
    """Return (DailyScreeningInputs, day_D_high, day_D_low, day_D_close) or None.

    df_full: 전체 daily history (Date index).
    target_date: day D (entry candidate day) — 본 함수가 "오늘" 로 본다.
    history-as-of D-1 까지로 prev_close, MA, vol_5d_cumsum 계산. day D close/volume 은
    "today" 시그널 입력으로 사용.
    """
    import pandas as pd
    # day D row
    if target_date not in df_full.index.date:
        return None
    day_D_row = df_full[df_full.index.date == target_date].iloc[0]
    day_D_close = float(day_D_row["close"])
    day_D_high = float(day_D_row["high"])
    day_D_low = float(day_D_row["low"])
    day_D_volume = int(day_D_row["volume"])
    # history < D
    hist = df_full[df_full.index.date < target_date]
    if len(hist) < 60:
        return None
    closes = hist["close"].astype(float)
    vols = hist["volume"].astype(int)
    inputs = DailyScreeningInputs(
        symbol="",  # filled by caller
        prev_close=float(closes.iloc[-1]),
        prev_close_2=float(closes.iloc[-2]),
        today_close=day_D_close,
        today_volume=day_D_volume,
        vol_5d_cumsum=int(vols.tail(5).sum()) + day_D_volume,
        power_ratio=100.0,  # placeholder
        ma5=float(closes.tail(5).mean()),
        ma20=float(closes.tail(20).mean()),
        ma60=float(closes.tail(60).mean()),
    )
    return inputs, day_D_high, day_D_low, day_D_close


def cond_wait5m_daily_proxy(inputs: DailyScreeningInputs) -> bool:
    """WAIT5M H 의 daily 근사: today_close ≥ prev_close × 1.067.

    원본: 상승방향 정적 VI 근접율 ≤ 3% (분봉 시점 평가).
    근사: EOD 기준 daily close 가 VI 발동가 (prev_close×1.10) 의 3% 이내까지 도달.
    → today_close ≥ prev_close × 1.10 × 0.97 = prev_close × 1.067.
    """
    if inputs.prev_close <= 0:
        return False
    return inputs.today_close >= inputs.prev_close * 1.067


def passes_profile(inputs: DailyScreeningInputs, profile_name: str) -> bool:
    if profile_name == "swing":
        return common_passes(inputs, PROFILE_SWING)
    if profile_name == "hybrid_daily":
        # SWING ∪ WAIT5M-daily (DTS H 는 분봉 필요로 미포함)
        if common_passes(inputs, PROFILE_SWING):
            return True
        if common_passes(inputs, PROFILE_WAIT5M) and cond_wait5m_daily_proxy(inputs):
            return True
        return False
    raise ValueError(f"unknown profile: {profile_name}")


def simulate_next_day_exit(
    df_full, target_date: _date, entry_price: float,
    *, tp_pct: float, sl_pct: float, fee_pct: float, slippage_pct: float,
) -> tuple | None:
    """Day D 종가 매수 → Day D+1 high/low/close 로 청산. (exit_price, exit_reason, ret_pct)."""
    import pandas as pd
    future = df_full[df_full.index.date > target_date]
    if future.empty:
        return None
    next_row = future.iloc[0]
    next_high = float(next_row["high"])
    next_low = float(next_row["low"])
    next_close = float(next_row["close"])
    next_date = future.index[0].date()

    actual_entry = entry_price * (1.0 + slippage_pct)
    tp = actual_entry * (1.0 + tp_pct)
    sl = actual_entry * (1.0 - sl_pct)

    # 동일봉 동시 도달 시 손절 우선 (보수)
    if next_low <= sl:
        exit_price = sl * (1.0 - slippage_pct)
        exit_reason = "sl"
    elif next_high >= tp:
        exit_price = tp * (1.0 - slippage_pct)
        exit_reason = "tp"
    else:
        exit_price = next_close * (1.0 - slippage_pct)
        exit_reason = "eod"
    ret_pct = (exit_price - actual_entry) / actual_entry - 2 * fee_pct
    return next_date, exit_price, exit_reason, ret_pct


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=30, help="최근 N 거래일 backtest (default 30)")
    ap.add_argument("--daily-cache", default=str(CACHE_DEFAULT))
    ap.add_argument("--report-dir", default=str(REPORT_DEFAULT))
    ap.add_argument("--tp-pct", type=float, default=0.02)
    ap.add_argument("--sl-pct", type=float, default=0.02)
    ap.add_argument("--fee-pct", type=float, default=0.00015)
    ap.add_argument("--slippage-pct", type=float, default=0.0005)
    args = ap.parse_args()

    import pandas as pd
    cache_dir = Path(args.daily_cache)
    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    log.info("== Step 1: load universe daily cache ==")
    parquet_files = sorted(cache_dir.glob("*.parquet"))
    log.info("daily cache files: %d", len(parquet_files))

    log.info("== Step 2: target dates ==")
    trading_dates = get_recent_krx_trading_days(args.days)
    log.info("trading dates: %d (%s ~ %s)", len(trading_dates),
             trading_dates[0].isoformat(), trading_dates[-1].isoformat())

    log.info("== Step 3: backtest loop ==")
    profiles = ["swing", "hybrid_daily"]
    trades_by_profile: dict[str, list[dict]] = {p: [] for p in profiles}
    signals_by_profile: dict[str, int] = {p: 0 for p in profiles}
    n_evaluated = 0

    for f in parquet_files:
        symbol = f.stem
        if not symbol.isdigit() or len(symbol) != 6:
            continue
        try:
            df_full = pd.read_parquet(f)
        except Exception:
            continue
        if len(df_full) < 65 or "close" not in df_full.columns:
            continue
        if not hasattr(df_full.index, "date"):
            continue
        n_evaluated += 1
        for target_date in trading_dates:
            r = daily_inputs_for_date(df_full, target_date)
            if r is None:
                continue
            inputs, day_high, day_low, day_close = r
            object.__setattr__(inputs, "symbol", symbol)
            for prof in profiles:
                if not passes_profile(inputs, prof):
                    continue
                signals_by_profile[prof] += 1
                exit_data = simulate_next_day_exit(
                    df_full, target_date, day_close,
                    tp_pct=args.tp_pct, sl_pct=args.sl_pct,
                    fee_pct=args.fee_pct, slippage_pct=args.slippage_pct,
                )
                if exit_data is None:
                    continue
                exit_date, exit_px, exit_reason, ret_pct = exit_data
                trades_by_profile[prof].append({
                    "date": target_date.isoformat(),
                    "profile": prof, "symbol": symbol,
                    "entry_date": target_date.isoformat(),
                    "entry_px": float(day_close),
                    "exit_date": exit_date.isoformat(),
                    "exit_px": float(exit_px),
                    "exit_reason": exit_reason,
                    "ret_pct": float(ret_pct),
                })
        if n_evaluated % 50 == 0:
            log.info("  evaluated %d / %d symbols", n_evaluated, len(parquet_files))

    log.info("evaluated symbols: %d", n_evaluated)

    log.info("== Step 4: summary ==")
    summary: dict[str, dict] = {}
    for prof in profiles:
        trades = trades_by_profile[prof]
        signals = signals_by_profile[prof]
        if not trades:
            summary[prof] = {"signals": signals, "trades": 0, "decision": "skip"}
            log.info("%-15s signals=%4d trades=0 → 신호 부족", prof, signals)
            continue
        wins = sum(1 for t in trades if t["ret_pct"] > 0)
        n = len(trades)
        win_rate = wins / n
        avg_pnl = sum(t["ret_pct"] for t in trades) / n
        total_pnl = sum(t["ret_pct"] for t in trades)
        decision = "adopt" if (win_rate >= 0.50 and avg_pnl >= 0.003 and n >= 30) else "reject"
        summary[prof] = {
            "signals": signals, "trades": n, "wins": wins,
            "win_rate": win_rate, "avg_pnl": avg_pnl, "total_pnl": total_pnl,
            "decision": decision,
        }
        log.info("%-15s signals=%4d trades=%5d win_rate=%.1f%% avg_pnl=%+.3f%% total=%+.2f%% → %s",
                 prof, signals, n, win_rate * 100, avg_pnl * 100, total_pnl * 100, decision)

    # CSV dump
    log.info("== Step 5: trades CSV ==")
    label = f"{trading_dates[0].isoformat()}_to_{trading_dates[-1].isoformat()}_d{args.days}"
    csv_path = report_dir / f"06_daily_bench_{label}.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = _csv.writer(f)
        w.writerow(["date", "profile", "symbol", "entry_date", "entry_px",
                    "exit_date", "exit_px", "exit_reason", "ret_pct"])
        for prof in profiles:
            for t in trades_by_profile[prof]:
                w.writerow([t["date"], t["profile"], t["symbol"],
                            t["entry_date"], f"{t['entry_px']:.2f}",
                            t["exit_date"], f"{t['exit_px']:.2f}",
                            t["exit_reason"], f"{t['ret_pct']:.5f}"])
    log.info("trades csv: %s", csv_path)

    # Report
    out = report_dir / f"06_daily_bench_report_{label}.draft.md"
    lines = [
        f"# HTS 검색식 {args.days}일 daily-level backtest — {trading_dates[0].isoformat()} ~ {trading_dates[-1].isoformat()}",
        "",
        f"KIS 분봉 API 30일 backfill 불가 (#97 v5) → daily-level 근사 backtest.",
        "",
        f"- 평가 종목: {n_evaluated} (daily cache 350 + KOSPI200/KOSDAQ150 mix)",
        f"- 거래일: {len(trading_dates)}",
        f"- Entry: day D 종가, Exit: day D+1 high/low/close (+2%/-2%/EOD)",
        f"- 비용: 수수료 {args.fee_pct*100:.3f}% + 슬리피지 {args.slippage_pct*100:.3f}% (양방향)",
        f"- DTS H (3분봉 20MA 지지): daily 로 평가 불가 → 본 backtest 미포함",
        f"- WAIT5M H 근사: today_close ≥ prev_close × 1.067 (VI ±10% 의 3% 이내)",
        "",
        "## 결과 요약",
        "",
        "| profile | signals | trades | win_rate | avg_pnl | total_pnl | decision |",
        "|---------|--------:|-------:|---------:|--------:|----------:|----------|",
    ]
    for prof, s in summary.items():
        lines.append(
            f"| {prof} | {s['signals']} | {s['trades']} | "
            f"{s.get('win_rate', 0) * 100:.1f}% | "
            f"{s.get('avg_pnl', 0) * 100:+.3f}% | "
            f"{s.get('total_pnl', 0) * 100:+.2f}% | "
            f"**{s['decision']}** |"
        )
    lines += [
        "",
        "## 한계",
        f"1. **30일 표본** — 시장 regime 1개 (강세/약세/횡보) 만 커버. 5y backtest 가 본 안전선.",
        "2. **DTS 미포함** — 분봉 3분봉 20MA 지지 조건 평가 불가 → 1분봉 데이터 축적 후 별도 backtest (5/15~ cron).",
        "3. **Entry/Exit 모델 차이** — 원본은 1분봉 walk-forward, 본 backtest 는 일봉 close 진입 + 다음날 청산. 진입 timing precision 손실.",
        "4. **WAIT5M 근사**: 정적 VI 근접율 분봉 평가 → 일봉 EOD 종가 비율로 대체. 진입 빈도·정확도 차이.",
        "",
        "## 출처",
        "- 검색식 캡처 3장: 사용자 제공 (2026-05-14, 이슈 #230)",
        "- KIS API 30일 분봉 제한: #97 v5",
        "- Daily cache: `data/cache/krx_daily/` (KOSPI200 + KOSDAQ150)",
    ]
    out.write_text("\n".join(lines), encoding="utf-8")
    log.info("report saved: %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
