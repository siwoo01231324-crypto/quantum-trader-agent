"""Persistent simulation outcome cache for airborne FIRE events.

기존 ``airborne_fire_store.py`` 는 *FIRE 이벤트 자체* 만 영속 저장. 본 모듈은
그 fire 에 대한 *시뮬레이션 결과* (outcome / pct / bar_idx) 를 영속 저장한다.

병목 분석 (v0.6.10):
  - load_since(N일) → N봉 fire (수십~수천) 마다 매번 Binance fapi 15m 봉 4개
    REST fetch + 시뮬 → 100 fire = ~30초+
  - 같은 fire 의 outcome 은 한 번 결정되면 안 바뀜 → 캐싱 가능

설계:
  - JSONL ``logs/airborne_fires/sim_cache.jsonl`` (fire_store 와 같은 디렉토리)
  - row 1건 = simulated fire 1건. key = (ts_iso, symbol, side).
  - 매 cache 호출 시 in-memory dict 로 build → O(1) lookup.
  - 새 sim 결과는 ``put_many`` 로 append (dedup).

사용 패턴 (api_airborne_metrics 안):
    cache = get_sim_cache()
    fires = store.load_since(since_utc)
    cached, missing = cache.split(fires)        # 한 번에 분리
    if missing:
        new_sims = await _simulate_many(missing)  # asyncio.gather
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


class AirborneSimCache:
    """Append-only JSONL — fire_key 기반 dedup. Thread-safe."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # in-memory cache: fire_key (tuple) → sim dict
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
                    # 최신 한 줄로 덮어쓰기 (dedup, 최신 sim 결과 신뢰)
                    self._cache[key] = rec
        except OSError as err:
            logger.warning(
                "[airborne_sim_cache] load failed: %s", err,
            )
        self._loaded = True

    @staticmethod
    def _key(rec: dict) -> tuple[str, str, str] | None:
        ts = str(rec.get("ts", ""))
        symbol = str(rec.get("symbol", ""))
        side = str(rec.get("side", ""))
        if not (ts and symbol and side):
            return None
        return (ts, symbol, side)

    # ── Public API ────────────────────────────────────────────────────────
    def split(
        self, fires: Iterable[dict],
    ) -> tuple[list[dict], list[dict]]:
        """fires 를 (cached_sims, missing_fires) 로 분리.

        Returns:
          - cached_sims: 캐시 hit. ``{ts, symbol, side, fire_close, trigger,
            outcome, pct, bar_idx}`` 완전체.
          - missing_fires: cache miss. caller 가 _simulate 후 ``put_many`` 호출.
        """
        with self._lock:
            self._load()
            cached: list[dict] = []
            missing: list[dict] = []
            for fire in fires:
                key = self._key(fire)
                if key is None:
                    continue
                if key in self._cache:
                    cached.append(self._cache[key])
                else:
                    missing.append(fire)
            return cached, missing

    def put_many(self, sims: Iterable[dict]) -> int:
        """새 sim 결과 append (dedup). 반환 = 새로 저장된 row 수.

        ``sims`` 각 row 는 fire fields + sim outcome 필드 (outcome/pct/bar_idx)
        포함해야 함. caller (api_airborne_metrics) 가 만들어 넘김.
        """
        with self._lock:
            self._load()
            new_lines: list[str] = []
            added = 0
            for sim in sims:
                key = self._key(sim)
                if key is None:
                    continue
                # 이미 같은 key 있으면 skip (dedup)
                if key in self._cache:
                    continue
                self._cache[key] = sim
                # only essential fields — caller-provided dict 그대로 영속
                new_lines.append(json.dumps(sim, ensure_ascii=False, default=str))
                added += 1
            if new_lines:
                try:
                    with self.path.open("a", encoding="utf-8") as f:
                        for line in new_lines:
                            f.write(line + "\n")
                except OSError as err:
                    logger.warning(
                        "[airborne_sim_cache] append failed: %s", err,
                    )
                    return 0
            return added

    def count(self) -> int:
        with self._lock:
            self._load()
            return len(self._cache)
