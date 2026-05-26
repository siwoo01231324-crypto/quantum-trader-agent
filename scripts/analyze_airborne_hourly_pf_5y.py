"""5y 전체 trade 의 KST hour 별 PF / win / expectancy 분포 분석.

bench_live_airborne_kst_morning_5y.simulate_bidir_v12 를 ``kst_filter=False``
로 돌려 *모든 시각* 의 trade 를 모은 뒤, 각 trade 의 entry_ts 를 KST hour
(0–23) 로 매핑해 hour-of-day cross-tab 생성. 4일치 daemon 의 06–12 PF 3.07
같은 over-fit 진단 vs 5y 진짜 분포 비교가 목적.

Output:
- console table (24 시각 × {trades, win%, PF, exp%, long_PF, short_PF})
- ``reports/airborne_hourly_pf_5y.json`` (raw + 추천 hour set)
- 추천 hour set: PF > 1.0 AND trades >= 100 (under-sample 차단)
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_airborne_kst_morning_5y")
logger = logging.getLogger("analyze_airborne_hourly_pf_5y")

_KST = ZoneInfo("Asia/Seoul")


def hourly_breakdown(trades: list[dict]) -> list[dict]:
    """trade 리스트를 entry_ts KST hour 별로 그룹 → metrics dict 리스트."""
    by_h: dict[int, list[dict]] = defaultdict(list)
    for t in trades:
        try:
            ts = datetime.fromisoformat(t["entry_ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            h = ts.astimezone(_KST).hour
            by_h[h].append(t)
        except (KeyError, ValueError, TypeError):
            continue

    rows = []
    for h in range(24):
        rs = by_h.get(h, [])
        n = len(rs)
        if n == 0:
            rows.append({"hour": h, "trades": 0, "win_rate": None,
                         "PF": None, "exp": None,
                         "long_n": 0, "short_n": 0,
                         "long_PF": None, "short_PF": None})
            continue
        rets = np.array([t["ret"] for t in rs])
        wins = rets[rets > 0]; losses = rets[rets <= 0]
        tp = float(wins.sum()) if len(wins) else 0.0
        tl = float(-losses.sum()) if len(losses) else 0.0
        pf = (tp / tl) if tl > 0 else None
        exp = float(rets.mean())
        win = float(len(wins) / n)

        longs = [t for t in rs if t["side"] == "long"]
        shorts = [t for t in rs if t["side"] == "short"]

        def _pf(ts: list[dict]) -> float | None:
            if not ts:
                return None
            r = np.array([t["ret"] for t in ts])
            p = float(r[r > 0].sum()); l = float(-r[r <= 0].sum())
            return (p / l) if l > 0 else None

        rows.append({
            "hour": h, "trades": n, "win_rate": win, "PF": pf, "exp": exp,
            "long_n": len(longs), "short_n": len(shorts),
            "long_PF": _pf(longs), "short_PF": _pf(shorts),
        })
    return rows


def recommend_hours(
    rows: list[dict], *, min_pf: float = 1.0, min_trades: int = 100,
) -> list[int]:
    """PF >= min_pf AND trades >= min_trades 인 hour 만 추천."""
    return [
        r["hour"] for r in rows
        if r["trades"] >= min_trades
        and r["PF"] is not None
        and r["PF"] >= min_pf
    ]


def estimate_filtered_metrics(trades: list[dict], hours: list[int]) -> dict:
    """추천 hour set 으로 필터링했을 때의 aggregate metrics (over-fit 인 점
    명시 — 같은 데이터로 selection + evaluation)."""
    hs = set(hours)
    filtered = []
    for t in trades:
        try:
            ts = datetime.fromisoformat(t["entry_ts"])
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts.astimezone(_KST).hour in hs:
                filtered.append(t)
        except (KeyError, ValueError, TypeError):
            continue
    if not filtered:
        return {"trades": 0, "PF": None, "exp": None, "win_rate": None}
    rets = np.array([t["ret"] for t in filtered])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    tp = float(wins.sum()); tl = float(-losses.sum())
    pf = (tp / tl) if tl > 0 else None
    return {
        "trades": len(filtered),
        "PF": pf, "exp": float(rets.mean()),
        "win_rate": float(len(wins) / len(filtered)),
        "selected_hours": sorted(hs),
    }


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    symbols = bench._load_universe_symbols(args.top_n)
    panels, coverage = bench._load_panels(symbols, args.months, "1h")
    if not panels:
        logger.error("no usable panels - abort.")
        return 3

    print("\n" + "=" * 130)
    print(f"hourly PF / win / exp breakdown — v1.2 bidir, 24h all-fire baseline")
    print(f"  months={args.months}  symbols={len(panels)}  cost={args.cost_bps:.0f}bp  R/R {args.stop*100}%/{args.tp*100}%")
    print("=" * 130)

    # 전체 trade 수집 — kst_filter=False
    all_trades = []
    for sym, panel in panels.items():
        trades = await asyncio.to_thread(
            bench.simulate_bidir_v12,
            panel, stop=args.stop, tp=args.tp,
            cost_bps=args.cost_bps, kst_filter=False,
        )
        all_trades.extend(trades)
    print(f"\ntotal trades: {len(all_trades)} (long {sum(1 for t in all_trades if t['side']=='long')} / short {sum(1 for t in all_trades if t['side']=='short')})")

    rows = hourly_breakdown(all_trades)
    print(f"\n{'KST':>3}  {'n':>5}  {'win%':>6}  {'PF':>6}  {'exp%':>8}  "
          f"{'long_n':>7}  {'long_PF':>7}  {'short_n':>7}  {'short_PF':>8}  flag")
    print("-" * 100)
    for r in rows:
        if r["trades"] == 0:
            print(f"  {r['hour']:>2}h    0")
            continue
        flag = ""
        if r["PF"] is not None and r["PF"] >= 1.0:
            flag = "*"
        if r["PF"] is not None and r["PF"] >= 1.5:
            flag = "**"
        pf_t = f"{r['PF']:6.3f}" if r['PF'] is not None else "  -   "
        lpf = f"{r['long_PF']:7.3f}" if r['long_PF'] is not None else "    -  "
        spf = f"{r['short_PF']:8.3f}" if r['short_PF'] is not None else "     -  "
        print(f"  {r['hour']:>2}h  {r['trades']:>5}  {r['win_rate']*100:5.2f}%  "
              f"{pf_t}  {r['exp']*100:+7.4f}%  "
              f"{r['long_n']:>7}  {lpf}  {r['short_n']:>7}  {spf}  {flag}")

    rec = recommend_hours(rows, min_pf=1.0, min_trades=100)
    rec_strict = recommend_hours(rows, min_pf=1.2, min_trades=200)
    print()
    print("=" * 130)
    print(f"추천 hour set:")
    print(f"  loose  (PF>=1.0 & n>=100):  {sorted(rec)}  ({len(rec)} hours)")
    print(f"  strict (PF>=1.2 & n>=200):  {sorted(rec_strict)}  ({len(rec_strict)} hours)")

    rec_metrics = estimate_filtered_metrics(all_trades, rec)
    rec_strict_metrics = estimate_filtered_metrics(all_trades, rec_strict)
    print(f"\nin-sample (over-fit, same data used for selection):")
    print(f"  loose:  PF={rec_metrics.get('PF')}  exp={rec_metrics.get('exp')}  trades={rec_metrics['trades']}")
    print(f"  strict: PF={rec_strict_metrics.get('PF')}  exp={rec_strict_metrics.get('exp')}  trades={rec_strict_metrics['trades']}")
    print("=" * 130)

    out_path = _REPO_ROOT / "reports" / "airborne_hourly_pf_5y.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": args.months,
        "symbols_count": len(panels),
        "cost_bps": args.cost_bps,
        "stop_pct": args.stop, "tp_pct": args.tp,
        "total_trades": len(all_trades),
        "hourly": rows,
        "recommendations": {
            "loose": {"hours": sorted(rec), **rec_metrics},
            "strict": {"hours": sorted(rec_strict), **rec_strict_metrics},
        },
        "elapsed_sec": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str),
                        encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(_REPO_ROOT).as_posix()}")
    return 0


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="analyze_airborne_hourly_pf_5y")
    p.add_argument("--months", type=int, default=60)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--stop", type=float, default=0.03)
    p.add_argument("--tp", type=float, default=0.06)
    p.add_argument("--cost-bps", type=float, default=10.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
