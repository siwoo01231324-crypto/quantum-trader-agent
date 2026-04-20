#!/usr/bin/env python3
"""Run a backtest strategy.

Usage:
    python scripts/run_backtest.py --strategy momo-btc-v2 \
        --data-dir lake/ --start 2025-04-01 --end 2026-04-01

Output:
    - Prints metrics to stdout (Sharpe, MDD, total return, trade count, win rate)
    - Updates strategy frontmatter sharpe_bt field
    - Generates backtest draft note via doc_agent
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.bundle import load_ohlcv_from_parquet
from backtest.engine import BacktestConfig, run_backtest
from backtest.frontmatter import update_strategy_frontmatter
from backtest.strategies.momo_btc_v2 import MomoBtcV2

STRATEGY_REGISTRY: dict[str, type] = {
    "momo-btc-v2": MomoBtcV2,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a backtest strategy.")
    parser.add_argument("--strategy", required=True, choices=list(STRATEGY_REGISTRY.keys()),
                        help="Strategy name")
    parser.add_argument("--data-dir", default="lake/", help="Data lake directory (default: lake/)")
    parser.add_argument("--start", default=None, help="Start date ISO (optional)")
    parser.add_argument("--end", default=None, help="End date ISO (optional)")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    # 1. Load OHLCV from Parquet
    data_dir = Path(args.data_dir)
    strategy_id = args.strategy
    strategy_cls = STRATEGY_REGISTRY[strategy_id]

    # Determine symbol and freq from strategy
    symbol = "BTCUSDT"
    freq = "15m"

    df = load_ohlcv_from_parquet(data_dir, symbol=symbol, freq=freq,
                                 start=args.start, end=args.end)
    if df.empty:
        print("No data found. Run: python scripts/fetch_candles.py first")
        sys.exit(1)

    print(f"Loaded {len(df)} bars for {symbol} ({freq})")

    # 2. Instantiate strategy
    strategy = strategy_cls()

    # 3. Run backtest
    config = BacktestConfig()
    result = run_backtest(df, strategy, config)

    # 4. Print metrics to stdout
    m = result.metrics
    print(f"sharpe: {m['sharpe']:.4f}")
    print(f"mdd: {m['mdd']:.4f}")
    print(f"total_return: {m['total_return']:.4f}")
    print(f"trades: {m['trades']}")
    print(f"win_rate: {m['win_rate']:.4f}")

    # 5. Update frontmatter
    docs_dir = Path(__file__).parent.parent / "docs"
    try:
        updated_path = update_strategy_frontmatter(strategy_id, m, docs_dir)
        print(f"Updated frontmatter: {updated_path}")
    except FileNotFoundError as e:
        print(f"Warning: {e}")

    # 6. Generate draft note via doc_agent
    try:
        from services.doc_agent.generators import generate_backtest_draft

        period_start = args.start or str(df.index[0].date())
        period_end = args.end or str(df.index[-1].date())

        bt_result_json = {
            "strategy": strategy_id,
            "period": [period_start, period_end],
            "metrics": {
                "sharpe": m["sharpe"],
                "mdd": m["mdd"],
                "trades": m["trades"],
                "win_rate": m["win_rate"],
            },
        }
        draft_path = generate_backtest_draft(bt_result_json)
        print(f"Draft note: {draft_path}")
    except Exception as e:
        print(f"Warning: Could not generate draft note: {e}")

    print("Done.")


if __name__ == "__main__":
    main()
