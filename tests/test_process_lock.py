from __future__ import annotations

import pytest

from src.live.process_lock import ProcessLock, ProcessLockBusy


def test_process_lock_acquire_release(tmp_path):
    lock_path = tmp_path / "proc.lock"
    lock = ProcessLock(lock_path)

    lock.acquire()
    lock.release()

    # Re-acquire should succeed after release
    lock.acquire()
    lock.release()


def test_process_lock_double_acquire_raises(tmp_path):
    lock_path = tmp_path / "proc.lock"
    lock1 = ProcessLock(lock_path)
    lock2 = ProcessLock(lock_path)

    lock1.acquire()
    try:
        with pytest.raises(ProcessLockBusy):
            lock2.acquire()
    finally:
        lock1.release()


def test_process_lock_context_manager(tmp_path):
    lock_path = tmp_path / "proc.lock"

    with ProcessLock(lock_path) as lock:
        assert lock._lock.is_locked

    # After context exit, lock is released — re-acquire should succeed
    lock2 = ProcessLock(lock_path)
    lock2.acquire()
    lock2.release()


def test_process_lock_release_idempotent(tmp_path):
    lock_path = tmp_path / "proc.lock"
    lock = ProcessLock(lock_path)

    lock.acquire()
    lock.release()
    lock.release()  # Second release must not raise
