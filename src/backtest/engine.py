from __future__ import annotations

from dataclasses import dataclass, field
import pandas as pd
import numpy as np
from .protocol import Strategy, Bar, Signal
from .metrics import compute_all_metrics


@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    commission_pct: float = 0.001       # 0.1% Binance taker
    slippage_pct: float = 0.0005        # 0.05%
    max_drawdown_halt_pct: float = 0.05 # 5%


@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: list
    metrics: dict


def run_backtest(
    ohlcv: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig = None,
) -> BacktestResult:
    if config is None:
        config = BacktestConfig()
    if not isinstance(strategy, Strategy):
        raise TypeError(f"{type(strategy)} does not conform to Strategy protocol")

    # Initialize
    cash = config.initial_cash
    position = 0.0  # units held
    equity_values = []
    equity_timestamps = []
    trades = []
    halted = False
    peak_equity = config.initial_cash

    required_factors = list(getattr(strategy, "required_factors", []) or [])
    if required_factors:
        from signals.registry import FACTOR_REGISTRY

        unknown = [n for n in required_factors if n not in FACTOR_REGISTRY]
        if unknown:
            raise KeyError(f"strategy requires unregistered factors: {unknown}")

    precomputed_factors: dict[str, pd.Series | pd.DataFrame] = {}
    if required_factors:
        from signals.registry import FACTOR_REGISTRY, compute

        non_causal = [n for n in required_factors if not FACTOR_REGISTRY[n].causal]
        if non_causal:
            raise ValueError(
                f"non-causal factors cannot use precompute path: {non_causal}. "
                "register with causal=False is not supported by engine batch path."
            )

        for name in required_factors:
            spec = FACTOR_REGISTRY[name]
            kwargs = {col: ohlcv[col] for col in spec.inputs if col in ohlcv.columns}
            precomputed_factors[name] = compute(name, **kwargs, **spec.default_params)

    strategy.on_init({})

    for i in range(len(ohlcv)):
        row = ohlcv.iloc[i]
        bar = Bar(
            ts=row.name if isinstance(row.name, pd.Timestamp) else pd.Timestamp(row.name),
            open=float(row['open']),
            high=float(row['high']),
            low=float(row['low']),
            close=float(row['close']),
            volume=float(row['volume']),
        )

        # Mark to market
        equity = cash + position * bar.close

        # Check MDD halt
        if equity > peak_equity:
            peak_equity = equity
        drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0

        if drawdown >= config.max_drawdown_halt_pct and not halted:
            # Flatten position
            if position > 0:
                sell_price = bar.close * (1 - config.slippage_pct)
                commission = abs(position * sell_price * config.commission_pct)
                cash += position * sell_price - commission
                trades.append({
                    "ts": bar.ts,
                    "action": "sell",
                    "price": sell_price,
                    "size": position,
                    "commission": commission,
                    "reason": "MDD halt",
                })
                position = 0.0
            halted = True

        if not halted:
            history = ohlcv.iloc[:i+1]
            context: dict = {}
            if required_factors:
                factors: dict[str, pd.Series | pd.DataFrame] = {}
                for name in required_factors:
                    factors[name] = precomputed_factors[name].iloc[:i+1]
                context["factors"] = factors
            signal = strategy.on_bar(bar, history, context)

            if signal.action == "buy" and position == 0:
                buy_price = bar.close * (1 + config.slippage_pct)
                affordable = cash * signal.size / (buy_price * (1 + config.commission_pct))
                commission = affordable * buy_price * config.commission_pct
                cash -= affordable * buy_price + commission
                position = affordable
                trades.append({
                    "ts": bar.ts,
                    "action": "buy",
                    "price": buy_price,
                    "size": affordable,
                    "commission": commission,
                    "reason": signal.reason,
                })
            elif signal.action == "sell" and position > 0:
                sell_price = bar.close * (1 - config.slippage_pct)
                commission = position * sell_price * config.commission_pct
                cash += position * sell_price - commission
                trades.append({
                    "ts": bar.ts,
                    "action": "sell",
                    "price": sell_price,
                    "size": position,
                    "commission": commission,
                    "reason": signal.reason,
                })
                position = 0.0

        # Record equity after potential trades
        equity = cash + position * bar.close
        equity_values.append(equity)
        equity_timestamps.append(bar.ts)

    equity_curve = pd.Series(
        equity_values,
        index=pd.DatetimeIndex(equity_timestamps),
    )
    metrics = compute_all_metrics(equity_curve, trades)
    return BacktestResult(equity_curve=equity_curve, trades=trades, metrics=metrics)
