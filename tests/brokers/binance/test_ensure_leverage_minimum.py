"""PR #349 — AsyncBinanceFuturesAdapter.ensure_leverage_minimum 동작 검증.

Binance Futures -1109 "Invalid account" 거부의 root cause = *해당 종목
leverage 가 한 번도 설정 안 된* 계정의 첫 발주. ``ensure_leverage_minimum``
은 종목당 1회 (어댑터 인스턴스 캐시) get_position_risk 호출 → leverage=0
인 경우만 fallback (1x) set. 사용자 web 값 (>0) 은 보존.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter


class _StubClient:
    """get_position_risk + set_leverage 만 가진 가짜 client."""

    def __init__(self, lev_by_symbol: dict[str, int]) -> None:
        self._lev = lev_by_symbol
        self.set_calls: list[tuple[str, int]] = []
        self.get_calls: list[str] = []

    async def get_position_risk(self, symbol: str):
        self.get_calls.append(symbol)
        lev = self._lev.get(symbol, 0)
        return [type("PR", (), {"leverage": lev, "marginType": "CROSSED"})()]

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        self.set_calls.append((symbol, leverage))
        self._lev[symbol] = leverage


def _adapter(client: _StubClient) -> AsyncBinanceFuturesAdapter:
    a = object.__new__(AsyncBinanceFuturesAdapter)
    a._client = client
    return a


@pytest.mark.asyncio
async def test_unset_symbol_gets_fallback_leverage():
    """leverage=0 (미설정) 종목은 1x 로 set 되어야 함."""
    client = _StubClient({"BTCUSDT": 0})
    a = _adapter(client)
    await a.ensure_leverage_minimum("BTCUSDT")
    assert client.set_calls == [("BTCUSDT", 1)]


@pytest.mark.asyncio
async def test_user_configured_leverage_preserved():
    """사용자가 web 에서 10x 로 설정한 종목은 건드리지 않음 (no-op)."""
    client = _StubClient({"ETHUSDT": 10})
    a = _adapter(client)
    await a.ensure_leverage_minimum("ETHUSDT")
    assert client.set_calls == []  # never called


@pytest.mark.asyncio
async def test_custom_fallback_value():
    """fallback_leverage=5 명시 시 그 값 사용."""
    client = _StubClient({"FETUSDT": 0})
    a = _adapter(client)
    await a.ensure_leverage_minimum("FETUSDT", fallback_leverage=5)
    assert client.set_calls == [("FETUSDT", 5)]


@pytest.mark.asyncio
async def test_cache_prevents_repeated_rest():
    """같은 symbol 두 번째부터는 REST 호출 안 함."""
    client = _StubClient({"XPLUSDT": 0})
    a = _adapter(client)
    await a.ensure_leverage_minimum("XPLUSDT")
    await a.ensure_leverage_minimum("XPLUSDT")  # 두 번째
    await a.ensure_leverage_minimum("XPLUSDT")  # 세 번째
    assert len(client.get_calls) == 1, (
        "get_position_risk should be called only ONCE per symbol "
        f"(got {len(client.get_calls)})"
    )
    assert client.set_calls == [("XPLUSDT", 1)]


@pytest.mark.asyncio
async def test_cache_per_symbol():
    """캐시는 symbol 단위 — 다른 symbol 은 별도 호출."""
    client = _StubClient({"BTCUSDT": 0, "ETHUSDT": 10})
    a = _adapter(client)
    await a.ensure_leverage_minimum("BTCUSDT")
    await a.ensure_leverage_minimum("ETHUSDT")
    assert set(client.get_calls) == {"BTCUSDT", "ETHUSDT"}
    assert client.set_calls == [("BTCUSDT", 1)]  # ETHUSDT 는 user 값 보존


@pytest.mark.asyncio
async def test_already_set_symbol_cached_after_first_check():
    """이미 user 설정된 종목도 첫 호출에서 OK 확인 후 캐시."""
    client = _StubClient({"BTCUSDT": 10})
    a = _adapter(client)
    await a.ensure_leverage_minimum("BTCUSDT")
    await a.ensure_leverage_minimum("BTCUSDT")
    assert len(client.get_calls) == 1
    assert client.set_calls == []
