import pandas as pd
import numpy as np


def compute_sharpe(equity_curve: pd.Series, periods_per_year: int = 365) -> float:
    """MUST resample to daily, then compute. This is CRITICAL."""
    daily = equity_curve.resample("1D").last().dropna()
    returns = daily.pct_change().dropna()
    if len(returns) < 2 or returns.std() == 0:
        return 0.0
    return float(returns.mean() / returns.std() * np.sqrt(periods_per_year))


def compute_max_drawdown(equity_curve: pd.Series) -> float:
    peak = equity_curve.cummax()
    drawdown = (peak - equity_curve) / peak
    return float(drawdown.max()) if len(drawdown) > 0 else 0.0


def compute_total_return(equity_curve: pd.Series) -> float:
    if len(equity_curve) < 2:
        return 0.0
    return float((equity_curve.iloc[-1] - equity_curve.iloc[0]) / equity_curve.iloc[0])


def compute_win_rate(trades: list[dict]) -> float:
    if not trades:
        return 0.0
    # Pair buys with sells
    pnls = []
    buy_price = None
    for t in trades:
        if t["action"] == "buy":
            buy_price = t["price"]
        elif t["action"] == "sell" and buy_price is not None:
            pnls.append(t["price"] - buy_price)
            buy_price = None
    if not pnls:
        return 0.0
    return sum(1 for p in pnls if p > 0) / len(pnls)


def compute_all_metrics(equity_curve: pd.Series, trades: list[dict]) -> dict:
    return {
        "sharpe": compute_sharpe(equity_curve),
        "mdd": compute_max_drawdown(equity_curve),
        "total_return": compute_total_return(equity_curve),
        "trades": len(trades),  # KEY: 'trades' not 'trade_count' for doc_agent compat
        "win_rate": compute_win_rate(trades),
    }
