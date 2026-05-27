"""Persistent JSONL store for daemon FIRE events — cumulative across docker logs rotation.

qta-airborne-daemon 의 docker logs 는 rotation (10MB × 3 = 약 4일치) 으로 옛
데이터 손실. 대시보드 ``/airborne`` 페이지가 "어제 + 누적" 보려면 dashboard
서버가 직접 fire history 를 영속 저장해야 한다.

설계:
- 단일 JSONL ``logs/airborne_fires/history.jsonl`` (project root 기준 상대 경로).
- 매 호출에서 docker logs --since 4d 를 파싱한 fire 들 중 *기존에 없는 것만*
  append. dedup key = (ts_iso_utc, symbol, side).
- 호출자가 since_utc 이후 fire 만 read.

JSONL 선택 이유 (SQLite 대비):
- 단순 — append + 매번 풀 read (max ~10K row, 작음)
- dashboard 의 기존 ``manual_trade.jsonl`` 패턴과 동일
- 외부 도구 (grep/jq) 로 검사 쉬움
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class AirborneFireStore:
    """Append-only JSONL store. Thread-safe via single ``_lock``.

    schema per line::

        {"ts": "<utc iso>", "symbol": "BTCUSDT", "side": "long",
         "fire_close": 100.0, "trigger": 99.5}

    Backward compat: extra fields ignored on read.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # dedup cache — (ts_iso, symbol, side) tuples. 디스크 매번 안 읽도록.
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
                        str(rec.get("side", "")),
                    )
                    self._seen.add(key)
        except OSError as err:
            logger.warning(
                "[airborne_fire_store] dedup cache load failed: %s", err,
            )
        self._loaded = True

    def append_many(self, fires: Iterable[dict]) -> int:
        """이미 본 fire 는 skip. 새로 append 한 개수 반환.

        fires: ``[{ts, symbol, side, fire_close, trigger}, ...]`` (UTC iso ts).
        """
        with self._lock:
            self._load_dedup_cache()
            added = 0
            new_lines: list[str] = []
            for rec in fires:
                ts = str(rec.get("ts", ""))
                symbol = str(rec.get("symbol", ""))
                side = str(rec.get("side", ""))
                if not (ts and symbol and side):
                    continue
                key = (ts, symbol, side)
                if key in self._seen:
                    continue
                self._seen.add(key)
                clean = {
                    "ts": ts, "symbol": symbol, "side": side,
                    "fire_close": float(rec.get("fire_close", 0) or 0),
                    "trigger": float(rec.get("trigger", 0) or 0),
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
                        "[airborne_fire_store] append failed: %s", err,
                    )
                    return 0
            return added

    def load_since(self, since_utc: datetime) -> list[dict]:
        """since_utc 이후의 fire 들 list. 메모리 안에서만 — 파일 매번 풀 read.

        파일 부재 → 빈 list (never raise).
        """
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
                "[airborne_fire_store] load_since read failed: %s", err,
            )
            return []
        # 시각 기준 오름차순
        out.sort(key=lambda r: str(r.get("ts", "")))
        return out

    def count(self) -> int:
        """diagnostic — 전체 row 수 (dedup cache 길이)."""
        with self._lock:
            self._load_dedup_cache()
            return len(self._seen)

    def earliest_ts(self) -> str | None:
        """가장 옛 fire 의 ts (UTC iso). 없으면 None."""
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
