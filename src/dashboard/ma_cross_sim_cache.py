"""Persistent simulation outcome cache for ma-cross CROSS events.

``ma_cross_store.MaCrossStore`` 는 *CROSS 이벤트 자체* 만 영속 저장. 본 모듈은
그 cross 에 대한 *시뮬레이션 결과* (outcome / pct / bar_idx) 를 영속 저장한다.
``airborne_sim_cache.AirborneSimCache`` 와 동일 패턴 — dedup key 만 (ts, symbol,
cross) 로 다르다 (airborne 는 side, ma-cross 는 golden/death 방향).

설계 (airborne_sim_cache 와 동일):
  - JSONL ``logs/ma-cross/sim_cache.jsonl`` (store 와 같은 디렉토리)
  - row 1건 = simulated cross 1건. key = (ts_iso, symbol, cross).
  - 매 cache 호출 시 in-memory dict 로 build → O(1) lookup.
  - 새 sim 결과는 ``put_many`` 로 append (dedup).

사용 패턴 (api_ma_cross_metrics 안):
    cache = get_sim_cache()
    crosses = store.load_since(since_utc)
    cached, missing = cache.split(crosses)
    if missing:
        new_sims = await _simulate_many(missing)
        cache.put_many(new_sims)
    all_sims = cached + new_sims
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class MaCrossSimCache:
    """Append-only JSONL — cross_key 기반 dedup. Thread-safe."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # in-memory cache: cross_key (tuple) → sim dict
        self._cache: dict[tuple[str, str, str], dict] = {}
        self._loaded = False

    # ── Internal load ─────────────────────────────────────────────────────
    def _load(self) -> None:
        """First call 시 JSONL → memory dict."""
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
                    key = self._key(rec)
                    if key is None:
                        continue
                    self._cache[key] = rec
        except OSError as err:
            logger.warning(
                "[ma_cross_sim_cache] load failed: %s", err,
            )
        self._loaded = True

    @staticmethod
    def _key(rec: dict) -> tuple[str, str, str] | None:
        ts = str(rec.get("ts", ""))
        symbol = str(rec.get("symbol", ""))
        cross = str(rec.get("cross", ""))
        if not (ts and symbol and cross):
            return None
        return (ts, symbol, cross)

    # ── Public API ────────────────────────────────────────────────────────
    def split(
        self, crosses: Iterable[dict],
    ) -> tuple[list[dict], list[dict]]:
        """crosses 를 (cached_sims, missing_crosses) 로 분리.

        Returns:
          - cached_sims: 캐시 hit (완전체 sim dict).
          - missing_crosses: cache miss. caller 가 _simulate 후 ``put_many`` 호출.
        """
        with self._lock:
            self._load()
            cached: list[dict] = []
            missing: list[dict] = []
            for cross in crosses:
                key = self._key(cross)
                if key is None:
                    continue
                if key in self._cache:
                    cached.append(self._cache[key])
                else:
                    missing.append(cross)
            return cached, missing

    def put_many(self, sims: Iterable[dict]) -> int:
        """새 sim 결과 append (dedup). 반환 = 새로 저장된 row 수."""
        with self._lock:
            self._load()
            new_lines: list[str] = []
            added = 0
            for sim in sims:
                key = self._key(sim)
                if key is None:
                    continue
                if key in self._cache:
                    continue
                self._cache[key] = sim
                new_lines.append(json.dumps(sim, ensure_ascii=False, default=str))
                added += 1
            if new_lines:
                try:
                    with self.path.open("a", encoding="utf-8") as f:
                        for line in new_lines:
                            f.write(line + "\n")
                except OSError as err:
                    logger.warning(
                        "[ma_cross_sim_cache] append failed: %s", err,
                    )
                    return 0
            return added

    def count(self) -> int:
        with self._lock:
            self._load()
            return len(self._cache)
