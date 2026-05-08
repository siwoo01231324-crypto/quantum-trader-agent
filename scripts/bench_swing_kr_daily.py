"""5y daily KRX backtest of swing strategies.

Fetches 5+ year daily OHLCV via FinanceDataReader (free, no API key) for
3 KOSPI200 leaders, runs each strategy in
`src.backtest.strategies.swing_kr_daily`, and reports per-ticker and
aggregate metrics (Sharpe, MDD, Calmar, annual return, trades, win rate).

Usage:
    python scripts/bench_swing_kr_daily.py
    python scripts/bench_swing_kr_daily.py --start 2020-01-01 --end 2025-12-31
    python scripts/bench_swing_kr_daily.py --tickers 005930,035720

Output:
    docs/work/active/swing-strategy-portfolio/bench_output_kr_daily.json
    docs/work/active/swing-strategy-portfolio/bench_report_kr_daily.md

Notes:
- Long-only: signal in {0, 1}. Returns = signal.shift(1) * close.pct_change()
  (signal observed at close of day t executes on day t+1 open ≈ next close).
- Costs: 25 bps per round-trip (KRX retail typical: 15bp commission + 10bp
  slippage). Applied as `cost_bps × |signal_change|` daily.
- Sharpe annualised by sqrt(252). No risk-free rate adjustment (rate ≈ 3.5%
  in 2024-2025 KRW would shift Sharpe down by ~0.2 — note in report).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Repo root (worktree)
ROOT = Path(__file__).resolve().parents[1]

# Avoid `src.backtest` package side-effects (orchestrator imports portfolio.*)
# by loading the strategies module file directly.
_spec = importlib.util.spec_from_file_location(
    "swing_kr_daily",
    ROOT / "src" / "backtest" / "strategies" / "swing_kr_daily.py",
)
swing = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(swing)

DEFAULT_TICKERS = ["005930", "035720", "000660"]  # 삼성전자, 카카오, SK하이닉스
DEFAULT_START = "2020-01-01"
DEFAULT_END = "2025-12-31"
COST_BPS = 25  # round-trip transaction cost
TRADING_DAYS = 252


def fetch_ohlcv(ticker: str, start: str, end: str) -> pd.DataFrame:
    import FinanceDataReader as fdr
    raw = fdr.DataReader(ticker, start, end)
    if raw.empty:
        raise RuntimeError(f"No data for {ticker} in {start}..{end}")
    df = raw.rename(columns={
        "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Volume": "volume",
    })[["open", "high", "low", "close", "volume"]].copy()
    return df


def compute_metrics(close: pd.Series, signal: pd.Series, cost_bps: float = COST_BPS
                    ) -> dict:
    """Long-only daily backtest metrics. Returns dict of stats."""
    bar_ret = close.pct_change().fillna(0.0)
    pos = signal.shift(1).fillna(0).astype(float)
    pos_change = pos.diff().abs().fillna(pos.abs())
    cost_per_change = cost_bps / 10_000.0 / 2.0  # half cost per change (in/out)
    strat_ret = pos * bar_ret - pos_change * cost_per_change
    equity = (1.0 + strat_ret).cumprod()

    n_days = len(strat_ret)
    if n_days == 0 or strat_ret.std() == 0:
        return {
            "sharpe": 0.0, "mdd": 0.0, "calmar": 0.0, "ann_return": 0.0,
            "n_trades": 0, "win_rate": 0.0, "exposure": 0.0, "final_equity": 1.0,
            "buy_hold_ann": 0.0, "buy_hold_sharpe": 0.0,
        }

    sharpe = float(strat_ret.mean() / strat_ret.std() * np.sqrt(TRADING_DAYS))
    cum_max = equity.cummax()
    drawdown = (equity - cum_max) / cum_max
    mdd = float(drawdown.min())
    ann_return = float(equity.iloc[-1] ** (TRADING_DAYS / n_days) - 1) if equity.iloc[-1] > 0 else -1.0
    calmar = float(ann_return / abs(mdd)) if mdd < 0 else 0.0
    exposure = float(pos.mean())

    # Round-trip trades: count signal=0->1 transitions
    entries = int(((signal.diff() == 1) & (signal == 1)).sum())
    # win rate per trade: track entry/exit prices
    trade_returns = []
    in_pos = False
    entry_p = 0.0
    for i in range(len(signal)):
        s = int(signal.iloc[i])
        c = float(close.iloc[i])
        if not in_pos and s == 1:
            in_pos = True
            entry_p = c
        elif in_pos and s == 0:
            in_pos = False
            trade_returns.append(c / entry_p - 1)
    if in_pos:
        trade_returns.append(float(close.iloc[-1]) / entry_p - 1)
    win_rate = float(np.mean([r > 0 for r in trade_returns])) if trade_returns else 0.0

    # Buy & hold benchmark
    bh_equity = (1.0 + bar_ret).cumprod()
    bh_ann = float(bh_equity.iloc[-1] ** (TRADING_DAYS / n_days) - 1) if bh_equity.iloc[-1] > 0 else -1.0
    bh_sharpe = float(bar_ret.mean() / bar_ret.std() * np.sqrt(TRADING_DAYS)) if bar_ret.std() > 0 else 0.0

    return {
        "sharpe": round(sharpe, 3),
        "mdd": round(mdd, 4),
        "calmar": round(calmar, 3),
        "ann_return": round(ann_return, 4),
        "n_trades": entries,
        "win_rate": round(win_rate, 3),
        "exposure": round(exposure, 3),
        "final_equity": round(float(equity.iloc[-1]), 4),
        "buy_hold_ann": round(bh_ann, 4),
        "buy_hold_sharpe": round(bh_sharpe, 3),
    }


def run_bench(tickers: list[str], start: str, end: str) -> dict:
    results: dict[str, dict] = {}
    for ticker in tickers:
        print(f"[fetch] {ticker} {start}..{end}", flush=True)
        df = fetch_ohlcv(ticker, start, end)
        per_strategy: dict[str, dict] = {}
        for name, meta in swing.STRATEGY_REGISTRY.items():
            sig = meta["fn"](df)
            metrics = compute_metrics(df["close"], sig)
            per_strategy[name] = metrics
            print(
                f"  {name:24s} sharpe={metrics['sharpe']:6.3f} "
                f"mdd={metrics['mdd']*100:6.2f}% ann={metrics['ann_return']*100:6.2f}% "
                f"trades={metrics['n_trades']:3d} winrate={metrics['win_rate']*100:5.1f}% "
                f"expo={metrics['exposure']*100:5.1f}%",
                flush=True,
            )
        per_strategy["_buy_hold"] = {
            "sharpe": metrics["buy_hold_sharpe"],
            "ann_return": metrics["buy_hold_ann"],
        }
        results[ticker] = {
            "bars": int(len(df)),
            "first_close": float(df["close"].iloc[0]),
            "last_close": float(df["close"].iloc[-1]),
            "strategies": per_strategy,
        }

    # Aggregate across tickers (equal-weighted basket per strategy)
    aggregate: dict[str, dict] = {}
    for name in swing.STRATEGY_REGISTRY.keys():
        sharpes = [results[t]["strategies"][name]["sharpe"] for t in tickers]
        mdds = [results[t]["strategies"][name]["mdd"] for t in tickers]
        anns = [results[t]["strategies"][name]["ann_return"] for t in tickers]
        trades = [results[t]["strategies"][name]["n_trades"] for t in tickers]
        winrates = [results[t]["strategies"][name]["win_rate"] for t in tickers]
        exposures = [results[t]["strategies"][name]["exposure"] for t in tickers]
        aggregate[name] = {
            "mean_sharpe": round(float(np.mean(sharpes)), 3),
            "mean_mdd": round(float(np.mean(mdds)), 4),
            "mean_ann": round(float(np.mean(anns)), 4),
            "total_trades": int(sum(trades)),
            "mean_winrate": round(float(np.mean(winrates)), 3),
            "mean_exposure": round(float(np.mean(exposures)), 3),
        }

    return {
        "config": {
            "tickers": tickers, "start": start, "end": end, "cost_bps": COST_BPS,
        },
        "per_ticker": results,
        "aggregate": aggregate,
    }


def write_report(out: dict, json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# KRX Daily Swing Strategy 5y Bench",
        "",
        f"Tickers: {', '.join(out['config']['tickers'])}",
        f"Period:  {out['config']['start']} .. {out['config']['end']}",
        f"Costs:   {out['config']['cost_bps']} bps round-trip",
        "",
        "## Aggregate (equal-weighted across tickers)",
        "",
        "| Strategy | Sharpe | MDD | Ann.Return | Trades | WinRate | Exposure |",
        "|----------|-------:|----:|-----------:|-------:|--------:|---------:|",
    ]
    for name, agg in out["aggregate"].items():
        lines.append(
            f"| {name} | {agg['mean_sharpe']:.3f} | {agg['mean_mdd']*100:.2f}% | "
            f"{agg['mean_ann']*100:.2f}% | {agg['total_trades']} | "
            f"{agg['mean_winrate']*100:.1f}% | {agg['mean_exposure']*100:.1f}% |"
        )
    lines.append("")
    lines.append("## Per-Ticker Detail")
    for ticker, data in out["per_ticker"].items():
        lines.append(f"\n### {ticker} ({data['bars']} bars, {data['first_close']:.0f} → {data['last_close']:.0f})\n")
        lines.append("| Strategy | Sharpe | MDD | Ann.Return | Trades | WinRate | Exposure |")
        lines.append("|----------|-------:|----:|-----------:|-------:|--------:|---------:|")
        for name, m in data["strategies"].items():
            if name == "_buy_hold":
                continue
            lines.append(
                f"| {name} | {m['sharpe']:.3f} | {m['mdd']*100:.2f}% | "
                f"{m['ann_return']*100:.2f}% | {m['n_trades']} | "
                f"{m['win_rate']*100:.1f}% | {m['exposure']*100:.1f}% |"
            )
        bh = data["strategies"].get("_buy_hold", {})
        if bh:
            lines.append(f"| _buy & hold_ | {bh['sharpe']:.3f} | — | {bh['ann_return']*100:.2f}% | 1 | — | 100.0% |")
    lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", default=",".join(DEFAULT_TICKERS))
    p.add_argument("--start", default=DEFAULT_START)
    p.add_argument("--end", default=DEFAULT_END)
    p.add_argument(
        "--out-dir",
        default=str(ROOT / "docs" / "work" / "active" / "swing-strategy-portfolio"),
    )
    args = p.parse_args()
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    out = run_bench(tickers, args.start, args.end)
    out_dir = Path(args.out_dir)
    write_report(
        out,
        out_dir / "bench_output_kr_daily.json",
        out_dir / "bench_report_kr_daily.md",
    )
    print(f"\nWrote {out_dir / 'bench_output_kr_daily.json'}", flush=True)
    print(f"Wrote {out_dir / 'bench_report_kr_daily.md'}", flush=True)


if __name__ == "__main__":
    main()
