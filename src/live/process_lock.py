from __future__ import annotations
from pathlib import Path
from typing import Self

from filelock import FileLock, Timeout


class ProcessLockBusy(RuntimeError):
    """단일 인스턴스 락이 이미 획득된 상태에서 재획득 시도."""


class ProcessLock:
    """OS-level 파일 락 (filelock). 프로세스 종료 시 자동 해제.

    중복 실행 방지 (FMEA F9). Phase 1 의 라이브 루프는 단일 인스턴스만 허용.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = FileLock(str(self.path))

    def acquire(self) -> None:
        try:
            self._lock.acquire(timeout=0)
        except Timeout as err:
            raise ProcessLockBusy(f"Lock {self.path} already held") from err

    def release(self) -> None:
        if self._lock.is_locked:
            self._lock.release()

    def __enter__(self) -> "Self":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()
