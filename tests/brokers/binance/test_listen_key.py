"""Unit tests for Binance ListenKeyManager.

Covers:
- key property raises before issue
- delete() success + exception swallow
- keepalive loop: success, retry on failures, expiry after 3 failures, cancellation
- keepalive exits cleanly if key was deleted
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.brokers.binance.listen_key import ListenKeyManager


class _FakeClient:
    """Minimal stub of AsyncBinanceFuturesClient for listen_key tests."""

    def __init__(self) -> None:
        self.issue_listen_key = AsyncMock(return_value="abc12345xyz")
        self.extend_listen_key = AsyncMock(return_value=None)
        self.delete_listen_key = AsyncMock(return_value=None)


@pytest.mark.asyncio
async def test_key_property_raises_before_issue():
    mgr = ListenKeyManager(_FakeClient())
    with pytest.raises(RuntimeError, match="not yet issued"):
        _ = mgr.key


@pytest.mark.asyncio
async def test_issue_sets_key_and_returns():
    client = _FakeClient()
    mgr = ListenKeyManager(client)
    key = await mgr.issue()
    assert key == "abc12345xyz"
    assert mgr.key == "abc12345xyz"
    client.issue_listen_key.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_success_clears_key():
    client = _FakeClient()
    mgr = ListenKeyManager(client)
    await mgr.issue()
    await mgr.delete()
    client.delete_listen_key.assert_awaited_once_with("abc12345xyz")
    assert mgr._key is None


@pytest.mark.asyncio
async def test_delete_swallows_exception():
    client = _FakeClient()
    client.delete_listen_key = AsyncMock(side_effect=RuntimeError("network down"))
    mgr = ListenKeyManager(client)
    await mgr.issue()
    await mgr.delete()  # must not raise
    assert mgr._key is None


@pytest.mark.asyncio
async def test_delete_noop_when_no_key():
    client = _FakeClient()
    mgr = ListenKeyManager(client)
    await mgr.delete()  # no issue() called — noop
    client.delete_listen_key.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_keepalive_when_never_started():
    mgr = ListenKeyManager(_FakeClient())
    await mgr.stop_keepalive()  # no-op, no exception


@pytest.mark.asyncio
async def test_keepalive_exits_when_key_deleted():
    client = _FakeClient()
    mgr = ListenKeyManager(client)
    await mgr.issue()
    expiry = asyncio.Event()

    # Patch interval to 0 so the loop tick is immediate
    with patch("src.brokers.binance.listen_key._KEEPALIVE_INTERVAL_S", 0):
        # After one tick, delete the key → loop should exit without firing expiry
        async def deleter():
            await asyncio.sleep(0)
            mgr._key = None

        mgr.start_keepalive(expiry)
        await asyncio.gather(deleter(), asyncio.sleep(0.05))

    await mgr.stop_keepalive()
    assert not expiry.is_set()


@pytest.mark.asyncio
async def test_keepalive_extends_on_success():
    client = _FakeClient()
    mgr = ListenKeyManager(client)
    await mgr.issue()
    expiry = asyncio.Event()

    with patch("src.brokers.binance.listen_key._KEEPALIVE_INTERVAL_S", 0):
        mgr.start_keepalive(expiry)
        await asyncio.sleep(0.05)
        await mgr.stop_keepalive()

    assert client.extend_listen_key.await_count >= 1
    assert not expiry.is_set()


@pytest.mark.asyncio
async def test_keepalive_retries_then_fires_expiry_after_3_failures():
    client = _FakeClient()
    # First 3 attempts fail, then keep failing (should fire expiry after 3rd)
    client.extend_listen_key = AsyncMock(side_effect=RuntimeError("boom"))
    mgr = ListenKeyManager(client)
    await mgr.issue()
    expiry = asyncio.Event()

    with patch("src.brokers.binance.listen_key._KEEPALIVE_INTERVAL_S", 0):
        mgr.start_keepalive(expiry)
        await asyncio.wait_for(expiry.wait(), timeout=1.0)

    assert expiry.is_set()
    assert client.extend_listen_key.await_count >= 3
    await mgr.stop_keepalive()


@pytest.mark.asyncio
async def test_keepalive_resets_failure_counter_on_success():
    """Ensure the failures counter resets to 0 after a successful extend."""
    client = _FakeClient()
    call_count = 0

    async def flaky_extend(key: str) -> None:
        nonlocal call_count
        call_count += 1
        # Pattern: fail, fail, succeed, then stop (we'll cancel)
        if call_count in (1, 2):
            raise RuntimeError(f"transient-{call_count}")
        return None  # success on 3rd and onwards

    client.extend_listen_key = flaky_extend
    mgr = ListenKeyManager(client)
    await mgr.issue()
    expiry = asyncio.Event()

    with patch("src.brokers.binance.listen_key._KEEPALIVE_INTERVAL_S", 0):
        mgr.start_keepalive(expiry)
        # Let several ticks run: 1 fail, 2 fail, 3 succeed (resets to 0)
        await asyncio.sleep(0.05)
        await mgr.stop_keepalive()

    # 2 transient failures then recovery — must not fire expiry
    assert not expiry.is_set()
    assert call_count >= 3


@pytest.mark.asyncio
async def test_stop_keepalive_cancels_running_task():
    client = _FakeClient()
    mgr = ListenKeyManager(client)
    await mgr.issue()
    expiry = asyncio.Event()

    # Long interval so task parks in asyncio.sleep
    with patch("src.brokers.binance.listen_key._KEEPALIVE_INTERVAL_S", 10):
        mgr.start_keepalive(expiry)
        await asyncio.sleep(0.01)  # let task start
        assert mgr._keepalive_task is not None
        assert not mgr._keepalive_task.done()
        await mgr.stop_keepalive()

    assert mgr._keepalive_task is None
