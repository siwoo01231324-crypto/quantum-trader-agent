"""5y backtest harness for live-scanner strategies (#227 S6).

This is a *harness skeleton*. The full 5-year run for all 5 strategies × 2
universes (KRX + Binance) is a multi-hour data-fetch + simulation job and is
deferred to a follow-up issue. The skeleton wires:

  1. Strategy module discovery (LiveScannerMixin instances under
     ``src/backtest/strategies/live_*.py``)
  2. Universe loaders: KRX (KOSPI 200 + KOSDAQ 150 panel via
     ``src/backtest/bundle.py``) and Binance USDT-perp top-30
  3. A simple per-symbol replay loop that feeds 1-day bars into ``on_bar``,
     records buy signals, then applies the spec's stop/take_profit/trailing
     to derive exit timing — bar resolution is daily (universe live-scanner
     intraday simulation requires 1-minute panels and ~390x the data; that
     belongs to a separate harness)
  4. Per-(strategy, universe) metric output: Sharpe / MDD / AnnRet / Trades
     / WinRate / AvgHoldDays / RealizedPnLProfit / RealizedPnLLoss

Usage::

    python scripts/bench_live_scanner.py --strategy live_rsi_oversold_volume_spike --universe krx
    python scripts/bench_live_scanner.py --all  # all 5 × 2 = 10 runs
    python scripts/bench_live_scanner.py --strategy live_breakout_with_atr_stop \\
        --universe binance --period 1y --output results.json
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

logger = logging.getLogger("bench_live_scanner")


# --- Strategy registry --------------------------------------------------------

LIVE_SCANNER_STRATEGIES: dict[str, tuple[str, str]] = {
    # strategy_id → (module_path, class_name)
    "live_rsi_oversold_volume_spike": (
        "backtest.strategies.live_rsi_oversold_volume_spike",
        "LiveRsiOversoldVolumeSpike",
    ),
    "live_macd_bullish_cross_breakout": (
        "backtest.strategies.live_macd_bullish_cross_breakout",
        "LiveMacdBullishCrossBreakout",
    ),
    "live_bb_lower_bounce": (
        "backtest.strategies.live_bb_lower_bounce",
        "LiveBbLowerBounce",
    ),
    "live_breakout_with_atr_stop": (
        "backtest.strategies.live_breakout_with_atr_stop",
        "LiveBreakoutWithAtrStop",
    ),
    "live_oversold_with_divergence": (
        "backtest.strategies.live_oversold_with_divergence",
        "LiveOversoldWithDivergence",
    ),
}


def _load_strategy(strategy_id: str):
    module_path, class_name = LIVE_SCANNER_STRATEGIES[strategy_id]
    module = importlib.import_module(module_path)
    return getattr(module, class_name)()


# --- Bench result ------------------------------------------------------------

@dataclass
class BenchResult:
    strategy_id: str
    universe: str
    period: str
    n_symbols: int
    trades: int
    win_rate: float
    avg_hold_days: float
    sharpe: float
    mdd: float
    ann_return: float
    realized_pnl_profit: float
    realized_pnl_loss: float


# --- Per-symbol replay -------------------------------------------------------

async def _replay_symbol(
    strategy,
    symbol: str,
    panel: pd.DataFrame,
    *,
    cost_bps: float,
) -> list[dict]:
    """Walk the daily OHLCV panel of *symbol* once, dispatching ``on_bar``
    each bar. Returns the list of (entry_ts, entry_px, exit_ts, exit_px,
    exit_reason) trades.

    Stop / take_profit / trailing exits are applied *between* on_bar calls
    using the strategy's class attributes — same rules ``LivePositionRiskManager``
    enforces in production.
    """
    from signals.rsi import compute_rsi
    rsi_full = compute_rsi(panel["close"], period=14)
    trades: list[dict] = []
    in_pos = False
    entry_ts = None
    entry_px = None
    high_water = None

    for i in range(len(panel)):
        bar_history = panel.iloc[: i + 1]
        if in_pos:
            last_px = float(bar_history["close"].iloc[-1])
            if last_px > (high_water or entry_px):
                high_water = last_px
            sl = entry_px * (1 - strategy.stop_loss_pct)
            tp = entry_px * (1 + strategy.take_profit_pct)
            trail_pct = getattr(strategy, "trailing_stop_pct", None)
            exit_reason = None
            if last_px <= sl:
                exit_reason = "stop_loss"
            elif last_px >= tp:
                exit_reason = "take_profit"
            elif trail_pct is not None and high_water > entry_px:
                trail_px = high_water * (1 - trail_pct)
                if last_px <= trail_px:
                    exit_reason = "trailing_stop"
            if exit_reason is not None:
                trades.append({
                    "entry_ts": entry_ts.isoformat(),
                    "exit_ts": bar_history.index[-1].isoformat(),
                    "entry_px": entry_px,
                    "exit_px": last_px,
                    "exit_reason": exit_reason,
                    "ret": (last_px / entry_px) - 1 - 2 * cost_bps / 10000.0,
                })
                in_pos = False
                entry_ts = entry_px = high_water = None
                continue

        if not in_pos and len(bar_history) >= 60:
            ctx = {
                "ts": bar_history.index[-1],
                "market_snapshot": {
                    "symbol": symbol,
                    "history": bar_history,
                    "price": float(bar_history["close"].iloc[-1]),
                },
                "factors": {"rsi": rsi_full.iloc[: i + 1]},
            }
            sig = await strategy.on_bar(ctx)
            if sig is not None and sig.action == "buy":
                in_pos = True
                entry_ts = bar_history.index[-1]
                entry_px = float(bar_history["close"].iloc[-1])
                high_water = entry_px
    return trades


def _aggregate(trades: list[dict]) -> dict:
    """Aggregate per-trade returns into daily-PnL metrics.

    Each trade contributes its return on its *exit date*. Days with no exit
    are zero. Sharpe / MDD / annualised return are then computed in
    daily-equivalent units so ``× √252`` is dimensionally correct.

    The previous version mistakenly applied trade-count as the time axis,
    which inflated Sharpe / MDD whenever the strategy turned positions over
    multiple times per day (#227 follow-up bug-fix).
    """
    if not trades:
        return {
            "trades": 0, "win_rate": 0.0, "avg_hold_days": 0.0,
            "sharpe": 0.0, "mdd": 0.0, "ann_return": 0.0,
            "realized_pnl_profit": 0.0, "realized_pnl_loss": 0.0,
        }
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    holds = [
        (pd.Timestamp(t["exit_ts"]) - pd.Timestamp(t["entry_ts"])).days
        for t in trades
    ]

    # Aggregate to daily PnL. Multiple trades exiting on the same day add up
    # (linear approximation — sufficient for low single-digit % per trade).
    by_day: dict[pd.Timestamp, float] = {}
    for t in trades:
        day = pd.Timestamp(t["exit_ts"]).normalize()
        by_day[day] = by_day.get(day, 0.0) + float(t["ret"])
    if not by_day:
        return {
            "trades": int(len(trades)),
            "win_rate": float(len(wins) / max(len(rets), 1)),
            "avg_hold_days": float(np.mean(holds)) if holds else 0.0,
            "sharpe": 0.0, "mdd": 0.0, "ann_return": 0.0,
            "realized_pnl_profit": float(wins.sum()),
            "realized_pnl_loss": float(losses.sum()),
        }
    days = sorted(by_day.keys())
    daily_pnl = np.array([by_day[d] for d in days], dtype=float)
    n_days = len(daily_pnl)
    sharpe = (
        float(daily_pnl.mean() / daily_pnl.std() * np.sqrt(252))
        if daily_pnl.std() > 0 else 0.0
    )
    equity = np.cumprod(1 + daily_pnl)
    cum_max = np.maximum.accumulate(equity)
    dd = equity / cum_max - 1
    mdd = float(dd.min()) if len(dd) > 0 else 0.0
    final = float(equity[-1])
    ann_return = (
        float(final ** (252 / max(n_days, 1)) - 1) if final > 0 else -1.0
    )
    return {
        "trades": int(len(trades)),
        "win_rate": float(len(wins) / max(len(rets), 1)),
        "avg_hold_days": float(np.mean(holds)) if holds else 0.0,
        "sharpe": sharpe,
        "mdd": mdd,
        "ann_return": ann_return,
        "n_days_with_trades": int(n_days),
        "realized_pnl_profit": float(wins.sum()),
        "realized_pnl_loss": float(losses.sum()),
    }


# --- Universe loaders --------------------------------------------------------

def _load_krx_universe(period: str) -> dict[str, pd.DataFrame]:
    """KRX panel loader — delegates to bench_cs_tsmom_kr's cache + fetch path.

    Returns ``dict[code, OHLCV DataFrame]``. Daily-bar resolution (intraday
    1m bench requires ~390x the data and is a separate harness).
    """
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    bench_kr = importlib.import_module("bench_cs_tsmom_kr")
    uni = bench_kr.build_universe(
        bench_kr.DEFAULT_KOSPI_TOP, bench_kr.DEFAULT_KOSDAQ_TOP,
    )
    return bench_kr.fetch_universe(
        uni, bench_kr.DEFAULT_START, bench_kr.DEFAULT_END, refresh=False,
    )


def _load_binance_universe(period: str) -> dict[str, pd.DataFrame]:
    """Binance top-30 loader — delegates to bench_cs_tsmom_crypto helpers."""
    sys.path.insert(0, str(_REPO_ROOT / "scripts"))
    bench_bn = importlib.import_module("bench_cs_tsmom_crypto")
    universe = bench_bn.fetch_top_universe(bench_bn.DEFAULT_UNIVERSE_SIZE)
    panels = bench_bn.fetch_universe(
        universe, bench_bn.DEFAULT_START, bench_bn.DEFAULT_END,
        refresh=False,
    )
    return {s: df for s, df in panels.items() if len(df) >= 60}


# --- Main --------------------------------------------------------------------

async def _run_bench(
    strategy_id: str, universe: str, period: str, cost_bps: float,
) -> BenchResult:
    strategy = _load_strategy(strategy_id)
    if universe == "krx":
        panels = _load_krx_universe(period)
    elif universe == "binance":
        panels = _load_binance_universe(period)
    else:
        raise ValueError(f"unknown universe: {universe}")

    all_trades: list[dict] = []
    for symbol, panel in panels.items():
        trades = await _replay_symbol(
            strategy, symbol, panel, cost_bps=cost_bps,
        )
        all_trades.extend(trades)

    metrics = _aggregate(all_trades)
    return BenchResult(
        strategy_id=strategy_id,
        universe=universe,
        period=period,
        n_symbols=len(panels),
        **metrics,
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bench_live_scanner",
        description="5y backtest harness for #227 Live Universe Scanner strategies.",
    )
    parser.add_argument(
        "--strategy",
        choices=list(LIVE_SCANNER_STRATEGIES.keys()),
        default=None,
    )
    parser.add_argument(
        "--universe", choices=["krx", "binance"], default="krx",
    )
    parser.add_argument("--period", default="5y", help="e.g. 1y, 5y")
    parser.add_argument(
        "--cost-bps", type=float, default=55.0,
        help="round-trip cost in basis points (KRX default: 55bp).",
    )
    parser.add_argument(
        "--all", action="store_true",
        help="run every (strategy, universe) pair (10 total).",
    )
    parser.add_argument("--output", default=None, help="results JSON path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.all:
        combos = [
            (sid, univ)
            for sid in LIVE_SCANNER_STRATEGIES
            for univ in ("krx", "binance")
        ]
    elif args.strategy is None:
        logger.error("either --strategy or --all is required")
        return 2
    else:
        combos = [(args.strategy, args.universe)]

    results = []
    for sid, univ in combos:
        logger.info("running bench: strategy=%s universe=%s", sid, univ)
        result = asyncio.run(_run_bench(sid, univ, args.period, args.cost_bps))
        logger.info(
            "  → trades=%d sharpe=%.3f ann=%.3f mdd=%.3f win_rate=%.2f",
            result.trades, result.sharpe, result.ann_return,
            result.mdd, result.win_rate,
        )
        results.append(asdict(result))

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2))
        logger.info("wrote %d results to %s", len(results), args.output)
    else:
        print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
