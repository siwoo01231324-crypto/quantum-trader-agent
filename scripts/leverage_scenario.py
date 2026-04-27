"""Leverage scenario analysis for AC3 — monthly 10% feasibility study.

Applies post-multiplication leverage to a daily returns series and computes
risk metrics. Approximation: r_t^(L) = L * r_t - (L-1) * daily_funding.
Ruin: if leveraged return <= -1, cumulative product is clamped to 0.

Usage:
    python scripts/leverage_scenario.py --input <returns.parquet> \
        --L 1.0,3.0,5.0 --funding 0.073 --out <out.json>
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def apply_leverage(
    returns: pd.Series,
    L: float,
    funding_rate_annual: float = 0.073,
) -> pd.Series:
    """Apply leverage L to a daily returns series with funding cost.

    Args:
        returns: Daily simple returns (not log).
        L: Leverage multiplier (e.g. 3.0 = 3x).
        funding_rate_annual: Annual funding/borrow rate (e.g. 0.073 = 7.3%).

    Returns:
        Leveraged daily returns, clamped to -1 at ruin.
    """
    daily_funding = funding_rate_annual / 252
    leveraged = L * returns - (L - 1) * daily_funding
    # Clamp ruin: return <= -1 means full loss
    leveraged = leveraged.clip(lower=-1.0)
    return leveraged


def compute_leverage_metrics(
    returns: pd.Series,
    L: float,
    funding: float,
) -> dict:
    """Compute risk/return metrics for a leveraged returns series.

    Args:
        returns: Daily simple returns (unleveraged).
        L: Leverage multiplier.
        funding: Annual funding rate (e.g. 0.073).

    Returns:
        Dict with keys: annual_return, sharpe, mdd, cvar_975,
        monthly_10pct_hit_ratio.
    """
    lev = apply_leverage(returns, L, funding)

    # Cumulative wealth (clamped at 0 after ruin)
    cum = (1 + lev).cumprod()
    cum = cum.clip(lower=0.0)

    # Annual return (CAGR)
    n = len(lev)
    if n == 0 or cum.iloc[-1] <= 0:
        annual_return = -1.0
    else:
        annual_return = cum.iloc[-1] ** (252 / n) - 1

    # Sharpe (annualised, risk-free = 0)
    if lev.std(ddof=1) > 0:
        sharpe = (lev.mean() / lev.std(ddof=1)) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Max drawdown on cumulative wealth
    roll_max = cum.cummax()
    drawdown = (cum - roll_max) / roll_max.replace(0, np.nan)
    mdd = float(drawdown.min()) if len(drawdown) > 0 else 0.0

    # CVaR 97.5% (expected shortfall, sign convention: positive = loss)
    tail_threshold = np.percentile(lev, 2.5)
    tail_losses = lev[lev <= tail_threshold]
    cvar_975 = float(-tail_losses.mean()) if len(tail_losses) > 0 else 0.0

    # Monthly 10% hit ratio
    # Resample to monthly, compute simple cumulative return for each month
    monthly = (1 + lev).resample("ME").prod() - 1
    if len(monthly) > 0:
        monthly_10pct_hit_ratio = float((monthly >= 0.10).mean())
    else:
        monthly_10pct_hit_ratio = 0.0

    return {
        "L": L,
        "funding_annual": funding,
        "annual_return": round(annual_return, 6),
        "sharpe": round(sharpe, 4),
        "mdd": round(mdd, 6),
        "cvar_975": round(cvar_975, 6),
        "monthly_10pct_hit_ratio": round(monthly_10pct_hit_ratio, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Leverage scenario analysis")
    parser.add_argument("--input", required=True, help="Path to parquet with daily returns column 'returns'")
    parser.add_argument("--L", default="1.0,3.0,5.0", help="Comma-separated leverage values")
    parser.add_argument("--funding", default="0.073", help="Annual funding rate (e.g. 0.073)")
    parser.add_argument("--out", required=True, help="Output JSON path")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        return 1

    df = pd.read_parquet(input_path)
    if "returns" not in df.columns:
        print(f"ERROR: 'returns' column not found in {input_path}", file=sys.stderr)
        return 1

    returns = df["returns"].dropna()
    returns.index = pd.DatetimeIndex(returns.index)

    leverage_values = [float(x.strip()) for x in args.L.split(",")]
    funding_rate = float(args.funding)

    results = []
    for L in leverage_values:
        metrics = compute_leverage_metrics(returns, L, funding_rate)
        results.append(metrics)
        print(
            f"  L={L:.1f}  funding={funding_rate:.1%}  "
            f"annual={metrics['annual_return']:.2%}  "
            f"sharpe={metrics['sharpe']:.3f}  "
            f"mdd={metrics['mdd']:.2%}  "
            f"hit10%={metrics['monthly_10pct_hit_ratio']:.2%}"
        )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nResults written to: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
