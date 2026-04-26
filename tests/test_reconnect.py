from __future__ import annotations
import pytest
from src.live.reconnect import backoff_delay, with_reconnect


# ---------------------------------------------------------------------------
# backoff_delay tests
# ---------------------------------------------------------------------------

def test_backoff_delay_exponential():
    # jitter_frac=0 → deterministic: base * 2^attempt
    assert backoff_delay(0, base=1.0, cap=1000.0, jitter_frac=0) == 1.0
    assert backoff_delay(1, base=1.0, cap=1000.0, jitter_frac=0) == 2.0
    assert backoff_delay(2, base=1.0, cap=1000.0, jitter_frac=0) == 4.0
    assert backoff_delay(3, base=1.0, cap=1000.0, jitter_frac=0) == 8.0


def test_backoff_delay_capped():
    # attempt 10 → raw = min(1 * 2^10, 10) = min(1024, 10) = 10
    assert backoff_delay(10, base=1.0, cap=10.0, jitter_frac=0) == 10.0


def test_backoff_delay_jitter_range():
    raw = 1.0 * (2 ** 2)  # attempt=2, base=1, cap=1000 → raw=4
    for _ in range(50):
        d = backoff_delay(2, base=1.0, cap=1000.0, jitter_frac=0.25)
        assert raw <= d <= raw * 1.25 + 1e-9  # small float tolerance


# ---------------------------------------------------------------------------
# with_reconnect tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_with_reconnect_succeeds_first_try():
    calls = []

    async def factory():
        calls.append(1)

    async def fake_sleep(d):
        pass

    await with_reconnect(factory, max_attempts=5, sleep=fake_sleep)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_with_reconnect_retries_on_exception():
    call_count = 0
    disconnect_calls = []

    async def factory():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient error")
        # second call succeeds

    def on_disconnect(attempt, err):
        disconnect_calls.append((attempt, err))

    sleep_calls = []

    async def fake_sleep(d):
        sleep_calls.append(d)

    await with_reconnect(
        factory,
        max_attempts=5,
        on_disconnect=on_disconnect,
        sleep=fake_sleep,
        jitter_frac=0,
    )
    assert call_count == 2
    assert len(disconnect_calls) == 1
    assert isinstance(disconnect_calls[0][1], RuntimeError)
    assert len(sleep_calls) == 1  # slept once between attempts


@pytest.mark.asyncio
async def test_with_reconnect_exhausts_max_attempts():
    async def always_fail():
        raise RuntimeError("always fails")

    async def fake_sleep(d):
        pass

    with pytest.raises(RuntimeError, match="exhausted"):
        await with_reconnect(
            always_fail,
            max_attempts=3,
            sleep=fake_sleep,
            jitter_frac=0,
        )
