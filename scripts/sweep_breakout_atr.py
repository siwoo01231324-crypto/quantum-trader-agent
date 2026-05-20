"""Parameter sweep for live_breakout_with_atr_stop (#227 S6 follow-up).

Spec ``docs/specs/strategies/live-breakout-with-atr-stop.md`` §백테스트 pre-
registers a "trailing-stop 2/4/6% sensitivity sweep"; research note
``docs/background/45-donchian-breakout-turtle.md`` §9 pre-registers the
parameter search. This driver runs that sweep WITHOUT changing production:
it reuses the *exact* exit logic + metric aggregation from
``bench_live_scanner`` (the same rules ``LivePositionRiskManager`` enforces
live) and only overrides the three risk attributes per combo.

Why a separate driver (not ``bench_live_scanner --all`` in a loop): the CLI
reloads + re-reads every parquet panel per run. A sweep of N combos would
re-read ~1 GB N times. Here the 1m panels are loaded ONCE and reused across
every combo (strategy state is per-instance, panels are read-only in
``_replay_symbol`` → no cross-combo contamination), turning an N× I/O job
into 1× I/O + N× CPU.

Usage::

    python scripts/sweep_breakout_atr.py                 # 1y cache, default grid
    python scripts/sweep_breakout_atr.py --cost-bps 10   # crypto round-trip
    python scripts/sweep_breakout_atr.py --output reports/sweep_breakout_1y.json

Stage 1 (this run): 1y directional read over whatever is cached in
``data/cache/binance_1m/``. Stage 2 (separate): re-run the top finalists on
the full 5y cache for the CLAUDE.md ``Sharpe ≥ 1.0`` production gate.
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

logger = logging.getLogger("sweep_breakout_atr")

# Pre-registered grid. Baseline (current production-spec values) is included
# as a reference row so every tuned combo is read RELATIVE to status quo.
BASELINE = {"stop_loss_pct": 0.05, "take_profit_pct": 0.20, "trailing_stop_pct": 0.04}
GRID_TRAIL = [0.02, 0.03, 0.04]
GRID_STOP = [0.02, 0.025, 0.03]
GRID_TP = [0.06, 0.10]


def _finalists() -> list[dict]:
    """Stage-2 (5y) finalists chosen from the 1y sweep.

    BASELINE = current production-spec control. The two tuned combos keep the
    DOMINANT lever (trailing 4%) untouched — the 1y sweep showed trailing 3%
    halves Sharpe and 2% turns it negative — and only tighten the hard stop
    (5%→2.5/2.0) and take-profit (20%→10%), which the 1y data showed is
    Sharpe-neutral while materially cutting MDD. These get the multi-regime
    5y gate (CLAUDE.md: live-scanner needs Sharpe ≥ 1.0 over 5y).
    """
    return [
        {**BASELINE, "_tag": "BASELINE(current)"},
        {"stop_loss_pct": 0.025, "take_profit_pct": 0.10,
         "trailing_stop_pct": 0.04, "_tag": "tr4%/sl2.5%/tp10%"},
        {"stop_loss_pct": 0.020, "take_profit_pct": 0.10,
         "trailing_stop_pct": 0.04, "_tag": "tr4%/sl2.0%/tp10%"},
    ]


def _combos() -> list[dict]:
    out = [{**BASELINE, "_tag": "BASELINE(current)"}]
    for tr in GRID_TRAIL:
        for sl in GRID_STOP:
            for tp in GRID_TP:
                out.append({
                    "stop_loss_pct": sl,
                    "take_profit_pct": tp,
                    "trailing_stop_pct": tr,
                    "_tag": f"tr{tr:.0%}/sl{sl:.1%}/tp{tp:.0%}",
                })
    return out


async def _run_combo(panels: dict, combo: dict, cost_bps: float) -> dict:
    """Fresh strategy instance with the combo's risk attrs; replay all panels.

    The instance attribute shadows the ``ClassVar`` so ``bench._replay_symbol``
    (which reads ``strategy.stop_loss_pct`` / ``.take_profit_pct`` /
    ``getattr(.., "trailing_stop_pct")``) sees the swept values — identical to
    how the live ``LivePositionRiskManager`` reads them.
    """
    strat = bench._load_strategy("live_breakout_with_atr_stop")
    strat.stop_loss_pct = combo["stop_loss_pct"]
    strat.take_profit_pct = combo["take_profit_pct"]
    strat.trailing_stop_pct = combo["trailing_stop_pct"]

    all_trades: list[dict] = []
    for symbol, panel in panels.items():
        trades = await bench._replay_symbol(
            strat, symbol, panel, cost_bps=cost_bps,
        )
        all_trades.extend(trades)
    metrics = bench._aggregate(all_trades)
    return {**{k: v for k, v in combo.items()}, **metrics}


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    logger.info("loading 1m panels (load-once, reused across all combos)...")
    panels = bench._load_binance_universe(args.period, bar="1m")
    if not panels:
        logger.error(
            "binance_1m cache empty - run "
            "`python scripts/fetch_binance_1m_5y.py --years 1` first "
            "(or wait for the running fetch to finish).",
        )
        return 2
    logger.info(
        "loaded %d symbols in %.1fs: %s",
        len(panels), time.time() - t0, ", ".join(sorted(panels)[:8]) + " ...",
    )

    combos = _finalists() if args.preset == "finalists" else _combos()
    if args.max_combos:
        combos = combos[: args.max_combos]  # timing probe / partial run
    logger.info("sweep: %d combos x %d symbols", len(combos), len(panels))
    rows: list[dict] = []
    for i, combo in enumerate(combos, 1):
        c0 = time.time()
        row = await _run_combo(panels, combo, args.cost_bps)
        rows.append(row)
        _ann = row["ann_return"]
        _ann_s = "inf" if _ann == float("inf") else f"{_ann * 100:.1f}%"
        logger.info(
            "  [%d/%d] %-18s sharpe=%.3f mdd=%.1f%% ann=%s "
            "trades=%d win=%.1f%% hold=%.2fd (%.1fs)",
            i, len(combos), combo["_tag"], row["sharpe"],
            row["mdd"] * 100, _ann_s, row["trades"],
            row["win_rate"] * 100, row["avg_hold_days"], time.time() - c0,
        )

    rows_sorted = sorted(rows, key=lambda r: r["sharpe"], reverse=True)

    # Persist results FIRST (json.dumps is ASCII-safe) so a console encoding
    # hiccup never costs the multi-minute compute. Pretty-print AFTER.
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(json.dumps(
            {"cost_bps": args.cost_bps, "period": args.period,
             "n_symbols": len(panels), "results": rows_sorted}, indent=2,
        ))
        logger.info("wrote %s", args.output)

    print("\n" + "=" * 92)
    print("live_breakout_with_atr_stop - 1y 1m sweep (cost_bps=%.0f)  *=BASELINE"
          % args.cost_bps)
    print("=" * 92)
    print(f"{'combo':<20}{'Sharpe':>9}{'MDD':>9}{'AnnRet':>10}"
          f"{'Trades':>8}{'Win%':>7}{'Hold(d)':>9}")
    print("-" * 92)
    for r in rows_sorted:
        star = " *" if r.get("_tag", "").startswith("BASELINE") else ""
        ann = r["ann_return"]
        ann_s = "  inf" if ann == float("inf") else f"{ann*100:8.1f}%"
        print(f"{r.get('_tag',''):<20}{r['sharpe']:9.3f}{r['mdd']*100:8.1f}%"
              f"{ann_s:>10}{r['trades']:8d}{r['win_rate']*100:6.1f}%"
              f"{r['avg_hold_days']:9.2f}{star}")
    print("=" * 92)
    logger.info("total %.1f min", (time.time() - t0) / 60)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="sweep_breakout_atr")
    p.add_argument("--period", default="1y")
    p.add_argument(
        "--cost-bps", type=float, default=10.0,
        help="round-trip cost bps. Binance perp taker ~4bp x2 + slippage "
             "~= 10bp (KRX bench default 55 does NOT apply to crypto).",
    )
    p.add_argument(
        "--output", default="reports/sweep_breakout_atr_1y.json",
    )
    p.add_argument(
        "--max-combos", type=int, default=0,
        help="cap combos (0=all). Use 1-2 for a wall-time probe.",
    )
    p.add_argument(
        "--preset", choices=["full", "finalists"], default="full",
        help="full=19-combo grid (stage-1 1y). finalists=3 combos "
             "carried to the stage-2 5y gate.",
    )
    args = p.parse_args(argv)
    # Korean-locale Windows pipes stdout/stderr as cp949 → a non-ASCII char
    # in any print/log line aborts the whole run AFTER the multi-minute
    # compute. Make both streams lossy-UTF-8 so output never kills results.
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
