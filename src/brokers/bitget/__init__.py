"""Bitget USDT-M Futures async adapter (#feat/bitget-adapter).

Mirrors src/brokers/binance/ layout. Demo vs mainnet routing is single-flag
(`paper=True` → `paptrading: 1` header + ``SUSDT-FUTURES`` productType).

Spec: docs/specs/bitget-migration-plan.md (TBD).
"""
from __future__ import annotations

from src.brokers.bitget.async_adapter import AsyncBitgetFuturesAdapter
from src.brokers.bitget.async_http import AsyncBitgetFuturesClient

__all__ = ["AsyncBitgetFuturesAdapter", "AsyncBitgetFuturesClient"]
