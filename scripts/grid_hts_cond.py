"""HTS 검색식 grid search (#230).

lake 의 1m 데이터로 시간대 필터 + TP/SL 비대칭 조합 일괄 평가.
채택 임계값 (win_rate ≥ 50%, avg_pnl ≥ +0.3%, signals ≥ 30) 통과 조합 찾기.

사용:
    python scripts/grid_hts_cond.py                   # 오늘 1일
    python scripts/grid_hts_cond.py --multi-day 5     # 최근 5거래일 walk-forward 종합
"""
from __future__ import annotations

import argparse
import logging
import sys
from dataclasses import dataclass, field
from datetime import date as _date, datetime, timezone, timedelta
from pathlib import Path

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE))
sys.path.insert(0, str(WORKTREE / "src"))

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger("grid")
log.setLevel(logging.INFO)

KST = timezone(timedelta(hours=9))

from scripts.run_hts_cond_pilot import (
    build_universe_fdr, load_1m_for_date, load_daily_cache_as_of,
    get_recent_krx_trading_days,
    simulate_pilot, factory_dts, factory_wait5m, factory_swing, factory_hybrid_or,
)

LAKE = WORKTREE.parent.parent / "lake"
CACHE = WORKTREE.parent.parent / "data" / "cache" / "krx_daily"


@dataclass
class DateBundle:
    target_date: _date
    daily_cache: dict = field(default_factory=dict)
    bars_by_sym: dict = field(default_factory=dict)


def evaluate_grid(universe, date_bundles: list[DateBundle],
                  profile_name: str, factory,
                  *, tp_pct, sl_pct, max_entry_hour=None, min_entry_hour=None):
    """모든 (date, symbol) 페어에 simulate_pilot 적용 후 trades 종합."""
    all_trades = []
    signals_total = 0
    for bundle in date_bundles:
        for u in universe:
            bars = bundle.bars_by_sym.get(u.symbol)
            if bars is None or len(bars) < 25:
                continue
            res = simulate_pilot(
                bundle.daily_cache, bars, u.symbol, profile_name, None, factory,
                tp_pct=tp_pct, sl_pct=sl_pct,
                max_entry_hour=max_entry_hour, min_entry_hour=min_entry_hour,
            )
            signals_total += res["signals"]
            all_trades.extend(res["trades"])
    if not all_trades:
        return {"signals": signals_total, "trades": 0, "win_rate": 0.0, "avg_pnl": 0.0,
                "expectancy_pct": 0.0}
    wins = sum(1 for t in all_trades if t["ret_pct"] > 0)
    return {
        "signals": signals_total,
        "trades": len(all_trades),
        "wins": wins,
        "win_rate": wins / len(all_trades),
        "avg_pnl": sum(t["ret_pct"] for t in all_trades) / len(all_trades),
        "median_pnl": sorted([t["ret_pct"] for t in all_trades])[len(all_trades)//2],
        "expectancy_pct": wins / len(all_trades) * tp_pct - (1 - wins / len(all_trades)) * sl_pct,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--multi-day", type=int, default=1,
                    help="최근 N 거래일 walk-forward (default 1 = 오늘 1일).")
    args = ap.parse_args()

    log.info("== Loading universe (FDR) ==")
    universe = build_universe_fdr()
    log.info("universe: %d", len(universe))

    n_days = max(args.multi_day, 1)
    trading_dates = get_recent_krx_trading_days(n_days)
    log.info("== Loading lake (n_days=%d) — %s ==", n_days,
             [d.isoformat() for d in trading_dates])

    symbols = [u.symbol for u in universe]
    date_bundles: list[DateBundle] = []
    for target_date in trading_dates:
        bundle = DateBundle(target_date=target_date)
        bundle.daily_cache = load_daily_cache_as_of(CACHE, symbols, target_date)
        for u in universe:
            b = load_1m_for_date(LAKE, u.symbol, target_date)
            if b is not None and len(b) >= 25:
                bundle.bars_by_sym[u.symbol] = b
        log.info("  date=%s: daily_cache=%d, 1m_loaded=%d",
                 target_date.isoformat(), len(bundle.daily_cache), len(bundle.bars_by_sym))
        date_bundles.append(bundle)
    total_pairs = sum(len(b.bars_by_sym) for b in date_bundles)
    log.info("total (date×symbol) pairs: %d", total_pairs)

    log.info("== Grid search ==")
    profiles = [
        ("dts", factory_dts),
        ("wait5m", factory_wait5m),
        ("swing", factory_swing),
        ("hybrid_or", factory_hybrid_or),
    ]

    # Grid: (label, max_entry_hour, tp_pct, sl_pct)
    grids = [
        ("baseline",    None, 0.02, 0.02),
        ("≤10:30",      10.5, 0.02, 0.02),
        ("≤10:00",      10.0, 0.02, 0.02),
        ("≤11:00",      11.0, 0.02, 0.02),
        ("TP3/SL1.5",   None, 0.03, 0.015),
        ("TP3/SL2",     None, 0.03, 0.020),
        ("TP2.5/SL1.5", None, 0.025, 0.015),
        ("TP1.5/SL1",   None, 0.015, 0.010),
        ("≤10:30+TP3/SL1.5", 10.5, 0.03, 0.015),
        ("≤10:30+TP2.5/SL1.5", 10.5, 0.025, 0.015),
        ("≤10:00+TP3/SL1.5", 10.0, 0.03, 0.015),
        ("≤10:00+TP2/SL1",   10.0, 0.02, 0.01),
        ("≤11:00+TP3/SL1.5", 11.0, 0.03, 0.015),
    ]

    print()
    header = f"{'config':<24} | {'profile':<8} | {'signals':>7} | {'trades':>6} | {'win':>5} | {'avg_pnl':>8} | {'expect':>8} | decision"
    print(header)
    print("-" * len(header))
    best = []
    for label, mh, tp, sl in grids:
        for prof_name, fac in profiles:
            r = evaluate_grid(universe, date_bundles, prof_name, fac,
                              tp_pct=tp, sl_pct=sl, max_entry_hour=mh)
            if r["trades"] == 0:
                continue
            decision = "ADOPT" if (r["win_rate"] >= 0.50 and r["avg_pnl"] >= 0.003 and r["trades"] >= 30) else (
                "promising" if (r["win_rate"] >= 0.55 and r["avg_pnl"] >= 0.001 and r["trades"] >= 15) else "."
            )
            mark = "  *" if decision == "ADOPT" else ("  ~" if decision == "promising" else "")
            print(f"{label:<24} | {prof_name:<8} | {r['signals']:>7} | {r['trades']:>6} | "
                  f"{r['win_rate']*100:>4.1f}% | {r['avg_pnl']*100:>+6.3f}% | "
                  f"{r['expectancy_pct']*100:>+6.3f}% | {decision}{mark}")
            if decision in ("ADOPT", "promising"):
                best.append((label, prof_name, r))
        print()

    if best:
        print(f"\n=== TOP candidates ({len(best)}) ===")
        best.sort(key=lambda x: x[2]["avg_pnl"], reverse=True)
        for label, prof_name, r in best[:10]:
            print(f"  {label:<24} {prof_name:<8} win={r['win_rate']*100:.1f}% "
                  f"avg_pnl={r['avg_pnl']*100:+.3f}% trades={r['trades']}")
    else:
        print("\n채택 또는 promising 후보 없음 — 1일 표본 + 비용 0.05% 슬리피지 양방향 제약 명확.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
