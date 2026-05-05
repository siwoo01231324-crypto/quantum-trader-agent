"""Paper trading adapter — bridges swing strategy functions to PaperBroker.

Converts bar-by-bar signals from s2_donchian_voltarget / s4_funding_carry
into OrderRequest objects and submits them to an AsyncBrokerAdapter.

Usage (async context):
    adapter = PaperAdapter(
        strategy="s2c-voltarget",
        symbol="BTCUSDT",
        broker=paper_broker,
        initial_balance=Decimal("100000"),
    )
    await adapter.on_bar(ohlcv_df)  # call each time a new 4h bar closes
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

import pandas as pd

from src.brokers.base import OrderAck, OrderRequest, OrderType
from src.execution.base import Side, TimeInForce

logger = logging.getLogger(__name__)

StrategyId = Literal["s2c-voltarget", "s4-funding", "r4-switch", "r6-switch"]


@dataclass
class AdapterConfig:
    """Configuration for PaperAdapter strategy parameters."""

    strategy: StrategyId
    symbol: str
    initial_balance: Decimal = Decimal("100000")

    # s2c-voltarget params (defaults are 4h-tuned; override for other timeframes)
    entry_lookback: int = 20
    exit_lookback: int = 10
    vol_target: float = 0.15
    vol_lookback: int = 60

    # s4-funding params
    funding_threshold: float = -0.005e-2

    # r4-switch / r6-switch params (threshold-based regime switch)
    # r4 (4h): return_lookback=180 (= 30 days)
    # r6 (1h): return_lookback=720 (= 30 days, same horizon, 4x bars)
    return_lookback: int = 180

    # sizing
    min_order_usdt: Decimal = Decimal("10")

    # strategy_id tag written to WAL payload
    strategy_tag: str = field(init=False)

    def __post_init__(self) -> None:
        self.strategy_tag = self.strategy


class PaperAdapter:
    """Stateful adapter: maintains position flag and submits orders to PaperBroker.

    Designed for async-friendly call pattern: await adapter.on_bar(df).

    Parameters
    ----------
    config  : AdapterConfig with strategy and symbol settings.
    broker  : Any object with async place_order(OrderRequest) -> OrderAck.
              Typically PaperBroker from src/execution/paper_broker.py.
    """

    def __init__(self, config: AdapterConfig, broker) -> None:
        self._cfg = config
        self._broker = broker
        self._in_position: bool = False
        self._entry_price: Decimal = Decimal("0")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def on_bar(self, df: pd.DataFrame) -> OrderAck | None:
        """Process a new closed bar. df must contain all history up to current bar.

        Returns OrderAck if an order was submitted, else None.
        Called once per closed 4h bar.
        """
        if len(df) < 2:
            return None

        signal, pos_size = self._compute_signal(df)
        current_close = Decimal(str(df["close"].iloc[-1]))

        if signal == 1 and not self._in_position:
            return await self._enter(current_close, pos_size)
        elif signal == 0 and self._in_position:
            return await self._exit(current_close)
        return None

    # ------------------------------------------------------------------
    # Signal computation
    # ------------------------------------------------------------------

    def _compute_signal(self, df: pd.DataFrame) -> tuple[int, float]:
        """Compute (signal 0/1, position_size 0.0-1.0) from full history df."""
        from src.backtest.swing.strategies import s2_donchian_voltarget, s4_funding_carry

        if self._cfg.strategy == "s2c-voltarget":
            sig_series, size_series = s2_donchian_voltarget(
                df,
                entry_lookback=self._cfg.entry_lookback,
                exit_lookback=self._cfg.exit_lookback,
                vol_target=self._cfg.vol_target,
                vol_lookback=self._cfg.vol_lookback,
            )
            signal = int(sig_series.iloc[-1])
            pos_size = float(size_series.iloc[-1])
        elif self._cfg.strategy == "s4-funding":
            sig_series = s4_funding_carry(df, threshold_neg=self._cfg.funding_threshold)
            signal = int(sig_series.iloc[-1])
            pos_size = 1.0
        elif self._cfg.strategy == "r4-switch":
            from src.backtest.swing.regime_switching import route_r4

            s2c_params = {
                "entry_lookback": self._cfg.entry_lookback,
                "exit_lookback": self._cfg.exit_lookback,
                "vol_target": self._cfg.vol_target,
                "vol_lookback": self._cfg.vol_lookback,
            }
            s4_params = {"threshold_neg": self._cfg.funding_threshold}
            sig_series, size_series = route_r4(
                df,
                return_lookback=self._cfg.return_lookback,
                s2c_params=s2c_params,
                s4_params=s4_params,
            )
            signal = int(sig_series.iloc[-1])
            pos_size = float(size_series.iloc[-1]) if size_series is not None else 1.0
        elif self._cfg.strategy == "r6-switch":
            from src.backtest.swing.regime_switching import route_r6

            s2c_params = {
                "entry_lookback": self._cfg.entry_lookback,
                "exit_lookback": self._cfg.exit_lookback,
                "vol_target": self._cfg.vol_target,
                "vol_lookback": self._cfg.vol_lookback,
            }
            s4_params = {"threshold_neg": self._cfg.funding_threshold}
            sig_series, size_series = route_r6(
                df,
                return_lookback=self._cfg.return_lookback,
                s2c_params=s2c_params,
                s4_params=s4_params,
            )
            signal = int(sig_series.iloc[-1])
            pos_size = float(size_series.iloc[-1]) if size_series is not None else 1.0
        else:
            raise ValueError(f"Unknown strategy: {self._cfg.strategy!r}")

        return signal, pos_size

    # ------------------------------------------------------------------
    # Order helpers
    # ------------------------------------------------------------------

    async def _enter(self, price: Decimal, pos_size: float) -> OrderAck | None:
        qty = self._size_qty(price, pos_size)
        if qty <= Decimal("0"):
            logger.debug("skip entry: qty=0 at price=%s pos_size=%s", price, pos_size)
            return None

        req = OrderRequest(
            client_order_id=self._make_client_id("entry"),
            symbol=self._cfg.symbol,
            side=Side.BUY,
            qty=qty,
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
        )
        ack = await self._broker.place_order(req)
        if ack.status == "FILLED":
            self._in_position = True
            self._entry_price = price
            logger.info(
                "ENTRY filled: symbol=%s qty=%s price=%s strategy=%s",
                self._cfg.symbol, qty, price, self._cfg.strategy_tag,
            )
        else:
            logger.warning("ENTRY rejected: %s reason=%s", req.client_order_id, ack.reject_reason)
        return ack

    async def _exit(self, price: Decimal) -> OrderAck | None:
        positions = await self._broker.get_positions(self._cfg.symbol)
        if not positions:
            self._in_position = False
            return None

        pos = positions[0]
        req = OrderRequest(
            client_order_id=self._make_client_id("exit"),
            symbol=self._cfg.symbol,
            side=Side.SELL,
            qty=pos.qty,
            order_type=OrderType.MARKET,
            price=None,
            tif=TimeInForce.IOC,
            reduce_only=True,
        )
        ack = await self._broker.place_order(req)
        if ack.status == "FILLED":
            self._in_position = False
            self._entry_price = Decimal("0")
            logger.info(
                "EXIT filled: symbol=%s qty=%s price=%s strategy=%s",
                self._cfg.symbol, pos.qty, price, self._cfg.strategy_tag,
            )
        else:
            logger.warning("EXIT rejected: %s reason=%s", req.client_order_id, ack.reject_reason)
        return ack

    # ------------------------------------------------------------------
    # Sizing
    # ------------------------------------------------------------------

    def _size_qty(self, price: Decimal, pos_size: float) -> Decimal:
        """Convert position size fraction to BTC quantity.

        qty = (initial_balance * pos_size) / price, rounded down to 6 decimals.
        Minimum order size enforced via min_order_usdt.
        """
        if price <= Decimal("0") or pos_size <= 0.0:
            return Decimal("0")
        notional = self._cfg.initial_balance * Decimal(str(pos_size))
        if notional < self._cfg.min_order_usdt:
            return Decimal("0")
        qty = (notional / price).quantize(Decimal("0.000001"))
        return qty

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _make_client_id(self, action: str) -> str:
        return f"{self._cfg.strategy_tag}-{action}-{uuid.uuid4().hex[:8]}"

    @property
    def in_position(self) -> bool:
        return self._in_position
