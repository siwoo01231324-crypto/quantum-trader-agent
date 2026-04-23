"""Live smoke test: fetch real daily candles → portfolio risk → evaluate().

Binance public klines (no API key required). Run from repo root:
    PYTHONPATH=src python docs/work/active/000070-portfolio-risk/smoke_live.py
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")
except AttributeError:
    pass

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np
import pandas as pd

from data_lake.fetcher import fetch_binance_klines
from risk import (
    Policy, PerPortfolioRisk, Snapshot, Order, Action, evaluate,
    compute_portfolio_risk_from_df,
)

SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT"]
INTERVAL = "1d"


def fetch_close_series(symbol: str, start: str, end: str) -> pd.Series:
    df = fetch_binance_klines(symbol=symbol, interval=INTERVAL, start=start, end=end)
    s = pd.Series(df["close"].to_numpy(dtype=float),
                  index=pd.to_datetime(df["ts"], utc=True),
                  name=symbol)
    return s


def log_returns(prices: pd.DataFrame) -> pd.DataFrame:
    return np.log(prices / prices.shift(1)).dropna(how="any")


def describe(report):
    print(f"  n_strategies     = {report.n_strategies}")
    print(f"  n_observations   = {report.n_observations}")
    print(f"  alpha            = {report.alpha}")
    print(f"  CVaR(α)          = {report.cvar_pct:.4%}   (daily worst-tail mean loss)")
    print(f"  VaR(α)           = {report.var_pct:.4%}")
    print(f"  avg pairwise ρ   = {report.corr_avg:.3f}")
    print(f"  ENB (Meucci)     = {report.enb:.2f}")
    print(f"  ENB / N          = {report.enb_ratio:.3f}")


def run_policy(name: str, ppr: PerPortfolioRisk, report) -> None:
    policy = Policy(policy_version=1, name=name, per_portfolio_risk=ppr)
    snap = Snapshot(
        intent=Order(symbol="BTCUSDT", side="buy", qty=0.01, price=50_000),
        equity_krw=100_000_000,
        portfolio_risk=report,
    )
    d = evaluate(policy, snap)
    mark = "[OK]    ALLOW " if d.action == Action.ALLOW else f"[BREACH] {d.action.value.upper():7s}"
    extra = f"[{d.rule_id}] {d.message}" if d.rule_id else ""
    print(f"  {name:12s} → {mark} {extra}")


def main() -> None:
    end = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    start = (datetime.now(tz=timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%d")

    print(f"▶ Fetching {len(SYMBOLS)} symbols, {INTERVAL}, {start} → {end}")
    series = []
    for sym in SYMBOLS:
        print(f"  · {sym} …", end=" ", flush=True)
        s = fetch_close_series(sym, start, end)
        print(f"{len(s)} candles")
        series.append(s)

    prices = pd.concat(series, axis=1).dropna(how="any")
    rets = log_returns(prices)
    print(f"\n▶ Aligned returns: T={rets.shape[0]} days, N={rets.shape[1]} symbols")
    print(rets.tail(3).round(4).to_string())

    print("\n▶ Computing portfolio risk (equal weight)")
    report = compute_portfolio_risk_from_df(rets)
    describe(report)

    print("\n▶ Applying three policy regimes")
    policies = {
        "conservative": PerPortfolioRisk(max_cvar_pct=0.03, max_corr_avg=0.50, min_enb_ratio=0.70),
        "neutral":      PerPortfolioRisk(max_cvar_pct=0.08, max_corr_avg=0.80, min_enb_ratio=0.40),
        "aggressive":   PerPortfolioRisk(max_cvar_pct=0.20, max_corr_avg=0.95, min_enb_ratio=0.20),
    }
    for name, ppr in policies.items():
        run_policy(name, ppr, report)


if __name__ == "__main__":
    main()
