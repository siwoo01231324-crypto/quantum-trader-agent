"""Persistent JSONL store for PPP reversion-scalping signals — cumulative.

``scripts/ppp_signal_daemon.py`` 가 고변동 알트에 PPP 반전(과매수/과매도 극단 +
QPP 크로스 + 횡보레짐) 시그널을 평가해 이 store 에 누적한다. 대시보드 ``/ppp``
페이지가 "어제 + 누적" 을 보려면 영속 저장이 필요 — ``ma_cross_store.MaCrossStore``
와 동일 패턴. dedup key = (ts, symbol, side).

status NOTE: live-ppp-scalping-v1 은 5y 백테스트에서 OOS 과적합(라이브 미활성).
본 store 는 **페이퍼 시그널 수집(라이브 OOS 데이터 축적)** 전용 — 실발주 아님.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class PppSignalStore:
    """Append-only JSONL store. Thread-safe via single ``_lock``.

    schema per line::

        {"ts": "<utc iso>", "symbol": "WLDUSDT", "side": "long",
         "close": 0.65, "qpp_main": 22.1, "qpp_sig": 25.0,
         "choppiness": 63.4, "regime": "range"}

    dedup key = (ts, symbol, side). Backward compat: extra fields ignored.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen: set[tuple[str, str, str]] = set()
        self._loaded = False

    def _load_dedup_cache(self) -> None:
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
                    self._seen.add((
                        str(rec.get("ts", "")), str(rec.get("symbol", "")),
                        str(rec.get("side", "")),
                    ))
        except OSError as err:
            logger.warning("[ppp_signal_store] dedup load failed: %s", err)
        self._loaded = True

    def append_many(self, signals: Iterable[dict]) -> int:
        """이미 본 시그널 skip. 새로 append 한 개수 반환."""
        with self._lock:
            self._load_dedup_cache()
            added = 0
            new_lines: list[str] = []
            for rec in signals:
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
                    "close": float(rec.get("close", 0) or 0),
                    "qpp_main": float(rec.get("qpp_main", 0) or 0),
                    "qpp_sig": float(rec.get("qpp_sig", 0) or 0),
                    "choppiness": float(rec.get("choppiness", 0) or 0),
                    "regime": str(rec.get("regime", "")),
                }
                new_lines.append(json.dumps(clean, ensure_ascii=False))
                added += 1
            if new_lines:
                try:
                    with self.path.open("a", encoding="utf-8") as f:
                        for line in new_lines:
                            f.write(line + "\n")
                except OSError as err:
                    logger.warning("[ppp_signal_store] append failed: %s", err)
                    return 0
            return added

    def load_since(self, since_utc: datetime) -> list[dict]:
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
                            str(rec.get("ts", "")).replace("Z", "+00:00"))
                    except (ValueError, TypeError):
                        continue
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if ts >= since_utc:
                        out.append(rec)
        except OSError as err:
            logger.warning("[ppp_signal_store] load_since failed: %s", err)
            return []
        out.sort(key=lambda r: str(r.get("ts", "")))
        return out

    def count(self) -> int:
        with self._lock:
            self._load_dedup_cache()
            return len(self._seen)

    def earliest_ts(self) -> str | None:
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
                    if ts and (earliest is None or ts < earliest):
                        earliest = ts
        except OSError:
            return None
        return earliest
