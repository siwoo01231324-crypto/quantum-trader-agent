"""5y per-strategy edge evaluation for ALL live-scanner strategies (#227 S6).

The 1y→5y breakout sweep exposed that the bench's headline Sharpe/AnnRet is a
daily-mean + ``**(252/n_days)`` aggregation artifact that can show +261%/yr on
a strategy whose Profit Factor is < 1. The TRUSTWORTHY, un-gameable metrics are
**Profit Factor** (sum gross profit / sum gross loss) and **per-trade
expectancy** (= overall mean trade return). This driver runs every live-scanner
strategy ONCE over the full cached 5y 1m panels at its production-spec default
params and reports PF / expectancy / payoff so a strategy with no real edge
cannot hide behind an inflated Sharpe.

Reuses the exact replay + aggregation seam from ``bench_live_scanner`` (same
exit logic the live ``LivePositionRiskManager`` enforces). Panels are loaded
ONCE and reused across all strategies (per-instance state, read-only panels).

Usage::

    python scripts/eval_live_scanners_5y.py            # all 5, 30 sym, 5y
    python scripts/eval_live_scanners_5y.py --cost-bps 10
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_scanner")
logger = logging.getLogger("eval_live_scanners_5y")


def _edge(metrics: dict) -> dict:
    """Derive the un-gameable edge metrics from the bench aggregate.

    PF = gross profit / |gross loss|; PF < 1 ⇒ net loser regardless of the
    Sharpe the daily-mean aggregation reports. expectancy = (P + L) / n =
    mean per-trade return — negative ⇒ bleeds out over many trades.
    """
    n = int(metrics.get("trades", 0))
    w = float(metrics.get("win_rate", 0.0))
    P = float(metrics.get("realized_pnl_profit", 0.0))
    L = float(metrics.get("realized_pnl_loss", 0.0))
    nw = round(n * w)
    nl = n - nw
    avg_w = (P / nw) if nw else 0.0
    avg_l = (L / nl) if nl else 0.0
    pf = (P / abs(L)) if L else float("inf")
    payoff = (avg_w / abs(avg_l)) if avg_l else float("inf")
    exp = ((P + L) / n) if n else 0.0
    return {
        "trades": n, "win_rate": w, "avg_win": avg_w, "avg_loss": avg_l,
        "payoff": payoff, "profit_factor": pf, "expectancy": exp,
        "sharpe_bench": float(metrics.get("sharpe", 0.0)),
        "mdd_bench": float(metrics.get("mdd", 0.0)),
        "ann_bench": float(metrics.get("ann_return", 0.0)),
    }


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    logger.info("loading 5y 1m panels (load-once, reused across strategies)...")
    panels = bench._load_binance_universe("5y", bar="1m")
    if not panels:
        logger.error("binance_1m cache empty - run fetch_binance_1m_5y.py first.")
        return 2
    logger.info("loaded %d symbols in %.1fs", len(panels), time.time() - t0)

    sids = list(bench.LIVE_SCANNER_STRATEGIES.keys())  # all 5 (incl. breakout)
    rows: list[dict] = []
    for i, sid in enumerate(sids, 1):
        c0 = time.time()
        strat = bench._load_strategy(sid)  # production-spec DEFAULT params
        all_trades: list[dict] = []
        for symbol, panel in panels.items():
            all_trades.extend(
                await bench._replay_symbol(
                    strat, symbol, panel, cost_bps=args.cost_bps,
                )
            )
        e = _edge(bench._aggregate(all_trades))
        e["strategy_id"] = sid
        e["stop_loss_pct"] = getattr(strat, "stop_loss_pct", None)
        e["take_profit_pct"] = getattr(strat, "take_profit_pct", None)
        e["trailing_stop_pct"] = getattr(strat, "trailing_stop_pct", None)
        rows.append(e)
        logger.info(
            "  [%d/%d] %-34s PF=%.3f exp/trade=%+.4f%% win=%.1f%% "
            "trades=%d payoff=%.2f (%.0fs)",
            i, len(sids), sid, e["profit_factor"], e["expectancy"] * 100,
            e["win_rate"] * 100, e["trades"], e["payoff"], time.time() - c0,
        )

    rows_sorted = sorted(rows, key=lambda r: r["profit_factor"], reverse=True)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(
            {"cost_bps": args.cost_bps, "n_symbols": len(panels),
             "period": "5y", "results": rows_sorted}, indent=2,
        ))
        logger.info("wrote %s", args.output)

    print("\n" + "=" * 100)
    print("Live-scanner 5y edge (default params, cost_bps=%.0f)  "
          "PF<1 or exp<=0 = NET LOSER" % args.cost_bps)
    print("=" * 100)
    print(f"{'strategy':<34}{'PF':>7}{'exp/trade':>11}{'win%':>7}"
          f"{'payoff':>8}{'trades':>9}{'sl/tp/tr':>14}")
    print("-" * 100)
    for r in rows_sorted:
        verdict = "OK" if (r["profit_factor"] > 1.0 and r["expectancy"] > 0) else "LOSER"
        sltp = (f"{(r['stop_loss_pct'] or 0)*100:.1f}/"
                f"{(r['take_profit_pct'] or 0)*100:.0f}/"
                f"{(r['trailing_stop_pct'] or 0)*100:.0f}")
        print(f"{r['strategy_id']:<34}{r['profit_factor']:7.3f}"
              f"{r['expectancy']*100:+10.4f}%{r['win_rate']*100:6.1f}%"
              f"{r['payoff']:8.2f}{r['trades']:9d}{sltp:>14}  {verdict}")
    print("=" * 100)
    logger.info("total %.1f min", (time.time() - t0) / 60)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eval_live_scanners_5y")
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--output", default="reports/eval_live_scanners_5y.json")
    args = p.parse_args(argv)
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    )
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
