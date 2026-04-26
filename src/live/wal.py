from __future__ import annotations
import json
import logging
import os
from dataclasses import asdict
from pathlib import Path

from src.live.types import WALCorruption, WALEvent

logger = logging.getLogger(__name__)


class WALWriteFailed(Exception):
    """WAL append 실패. 호출자는 catch 후 kill-switch trip + 주문 거부."""


class WAL:
    """Append-only JSONL Write-Ahead Log. fsync 즉시.

    실패 시 WALWriteFailed raise — 호출자가 kill-switch 와 주문 거부 책임.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: WALEvent) -> None:
        try:
            line = json.dumps(asdict(event), ensure_ascii=False, separators=(",", ":")) + "\n"
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except (OSError, IOError) as err:
            raise WALWriteFailed(f"WAL write failed at {self.path}: {err}") from err


def replay(path: Path | str) -> tuple[list[WALEvent], list[WALCorruption]]:
    """JSONL 파싱. 손상 행 skip + 정상 + 손상 메타 동시 반환.

    Graceful 복구:
    - 빈 행 → skip (corruption 아님)
    - 잘못된 JSON / 필드 누락 → corruption
    - schema_version > 1 → corruption (warning + skip)
    - 파일 없음 → 빈 리스트 2개
    - BOM (﻿) → corruption
    """
    events: list[WALEvent] = []
    corruptions: list[WALCorruption] = []
    p = Path(path)
    if not p.exists():
        return events, corruptions

    with open(p, "r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, start=1):
            stripped = raw.rstrip("\n").rstrip("\r")
            if not stripped:
                continue
            # BOM 검출
            if stripped.startswith("﻿"):
                logger.warning("WAL BOM at line %d", line_no)
                corruptions.append(WALCorruption(line_no=line_no, raw=stripped, error="unexpected BOM"))
                continue
            try:
                data = json.loads(stripped)
                schema_version = data.get("schema_version", 1)
                if schema_version > 1:
                    msg = f"unsupported schema_version={schema_version}"
                    logger.warning("WAL schema mismatch at line %d: %s", line_no, msg)
                    corruptions.append(WALCorruption(line_no=line_no, raw=stripped, error=msg))
                    continue
                events.append(WALEvent(**data))
            except (json.JSONDecodeError, TypeError, ValueError) as err:
                logger.warning("WAL corruption at line %d: %s", line_no, err)
                corruptions.append(WALCorruption(line_no=line_no, raw=stripped, error=str(err)))
    return events, corruptions
