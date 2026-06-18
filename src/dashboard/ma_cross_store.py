"""Persistent JSONL store for ma-cross daemon CROSS events — cumulative.

``qta-ma-cross-daemon`` docker logs 는 rotation 으로 옛 데이터 손실. 대시보드
``/ma-cross`` 페이지가 "어제 + 누적" 을 보려면 dashboard 서버가 직접 cross
history 를 영속 저장해야 한다. ``airborne_fire_store.AirborneFireStore`` 와 동일
패턴 — 단, dedup key 가 (ts, symbol, side) 가 아니라 (ts, symbol, cross) 이고
필드가 close/sma_fast/sma_slow 라 얇은 전용 store 로 분리한다.

설계 (airborne_fire_store 와 동일):
- 단일 JSONL ``logs/ma-cross/history.jsonl`` (project root 기준 상대 경로).
- 매 호출에서 docker logs --since 를 파싱한 cross 들 중 *기존에 없는 것만*
  append. dedup key = (ts_iso_utc, symbol, cross).
- 호출자가 since_utc 이후 cross 만 read.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class MaCrossStore:
    """Append-only JSONL store. Thread-safe via single ``_lock``.

    schema per line::

        {"ts": "<utc iso>", "symbol": "BTCUSDT", "cross": "golden",
         "close": 67000.0, "sma_fast": 66900.0, "sma_slow": 65000.0}

    Backward compat: extra fields ignored on read.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # dedup cache — (ts_iso, symbol, cross) tuples. 디스크 매번 안 읽도록.
        self._seen: set[tuple[str, str, str]] = set()
        self._loaded = False

    def _load_dedup_cache(self) -> None:
        """첫 read/write 시 디스크에서 dedup cache 초기화."""
        if self._loaded:
            return
        if not self.path.exists():
            self._loaded = True
            return
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    key = (
                        str(rec.get("ts", "")),
                        str(rec.get("symbol", "")),
                        str(rec.get("cross", "")),
                    )
                    self._seen.add(key)
        except OSError as err:
            logger.warning(
                "[ma_cross_store] dedup cache load failed: %s", err,
            )
        self._loaded = True

    def append_many(self, crosses: Iterable[dict]) -> int:
        """이미 본 cross 는 skip. 새로 append 한 개수 반환.

        crosses: ``[{ts, symbol, cross, close, sma_fast, sma_slow}, ...]``
        (UTC iso ts).
        """
        with self._lock:
            self._load_dedup_cache()
            added = 0
            new_lines: list[str] = []
            for rec in crosses:
                ts = str(rec.get("ts", ""))
                symbol = str(rec.get("symbol", ""))
                cross = str(rec.get("cross", ""))
                if not (ts and symbol and cross):
                    continue
                key = (ts, symbol, cross)
                if key in self._seen:
                    continue
                self._seen.add(key)
                clean = {
                    "ts": ts, "symbol": symbol, "cross": cross,
                    "close": float(rec.get("close", 0) or 0),
                    "sma_fast": float(rec.get("sma_fast", 0) or 0),
                    "sma_slow": float(rec.get("sma_slow", 0) or 0),
                }
                new_lines.append(json.dumps(clean, ensure_ascii=False))
                added += 1
            if new_lines:
                try:
                    with self.path.open("a", encoding="utf-8") as f:
                        for line in new_lines:
                            f.write(line + "\n")
                except OSError as err:
                    logger.warning(
                        "[ma_cross_store] append failed: %s", err,
                    )
                    return 0
            return added

    def load_since(self, since_utc: datetime) -> list[dict]:
        """since_utc 이후의 cross 들 list. 시각 오름차순. 파일 부재 → 빈 list."""
        if since_utc.tzinfo is None:
            raise ValueError("since_utc must be tz-aware")
        since_utc = since_utc.astimezone(timezone.utc)
        if not self.path.exists():
            return []
        out: list[dict] = []
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    try:
                        ts = datetime.fromisoformat(
                            str(rec.get("ts", "")).replace("Z", "+00:00"),
                        )
                    except (ValueError, TypeError):
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= since_utc:
                        out.append(rec)
        except OSError as err:
            logger.warning(
                "[ma_cross_store] load_since read failed: %s", err,
            )
            return []
        out.sort(key=lambda r: str(r.get("ts", "")))
        return out

    def count(self) -> int:
        """diagnostic — 전체 row 수 (dedup cache 길이)."""
        with self._lock:
            self._load_dedup_cache()
            return len(self._seen)

    def earliest_ts(self) -> str | None:
        """가장 옛 cross 의 ts (UTC iso). 없으면 None."""
        if not self.path.exists():
            return None
        earliest: str | None = None
        try:
            with self.path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = str(rec.get("ts", ""))
                    if not ts:
                        continue
                    if earliest is None or ts < earliest:
                        earliest = ts
        except OSError:
            return None
        return earliest
