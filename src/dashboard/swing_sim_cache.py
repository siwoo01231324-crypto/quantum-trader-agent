"""Persistent simulation-trade cache for the swing strategy pair.

``/swing`` 페이지는 라이브 데몬이 없다 — airborne/ma-cross 처럼 docker logs 에서
이벤트를 줍는 게 아니라, 두 스윙 전략 객체(`LiveCapitulationBounce`,
`LiveDonchianBreakoutBtcGate`)를 과거 4h 봉(`data/cache/binance_1h/*.parquet`
→ 4h 리샘플)에 *직접 구동*해 거래를 합성한다(sim==live, 참조
`scripts/_swing_signal_returns_sim2.py`). 그 합성 결과(거래 1건 = entry/exit/
ret/reason)를 영속 저장하는 모듈이다 — ``ma_cross_sim_cache.MaCrossSimCache``
와 동일 패턴, dedup key 만 (strategy, symbol, entry_ts) 로 다르다.

설계 (ma_cross_sim_cache 와 동일):
  - JSONL ``logs/swing/sim_cache.jsonl``.
  - row 1건 = simulated trade 1건. key = (strategy, symbol, entry_ts).
  - 매 호출 시 in-memory dict 로 build → O(1) lookup.
  - 새 거래는 ``put_many`` 로 append (dedup).
  - 시뮬은 고정 과거 데이터 위에서 결정적이라, 한 번 채워지면 재계산 불필요.
    봉이 늘어나면 force-refresh 로 ``clear`` 후 재구동.

사용 패턴 (api_swing_metrics 안):
    cache = get_sim_cache()
    if cache.is_empty():
        trades = await asyncio.to_thread(_swing_compute_all_trades)
        cache.put_many(trades)
    all_trades = cache.load_all()
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class SwingSimCache:
    """Append-only JSONL — (strategy, symbol, entry_ts) dedup. Thread-safe."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # in-memory cache: trade_key (tuple) → trade dict
        self._cache: dict[tuple[str, str, str], dict] = {}
        self._loaded = False

    # ── Internal load ─────────────────────────────────────────────────────
    def _load(self) -> None:
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
            logger.warning("[swing_sim_cache] load failed: %s", err)
        self._loaded = True

    @staticmethod
    def _key(rec: dict) -> tuple[str, str, str] | None:
        strategy = str(rec.get("strategy", ""))
        symbol = str(rec.get("symbol", ""))
        entry_ts = str(rec.get("entry_ts", ""))
        if not (strategy and symbol and entry_ts):
            return None
        return (strategy, symbol, entry_ts)

    # ── Public API ────────────────────────────────────────────────────────
    def is_empty(self) -> bool:
        with self._lock:
            self._load()
            return len(self._cache) == 0

    def load_all(self) -> list[dict]:
        """모든 캐시된 거래를 list 로 반환 (entry_ts 오름차순)."""
        with self._lock:
            self._load()
            return sorted(
                self._cache.values(),
                key=lambda r: str(r.get("entry_ts", "")),
            )

    def put_many(self, trades: Iterable[dict]) -> int:
        """새 거래 append (dedup). 반환 = 새로 저장된 row 수."""
        with self._lock:
            self._load()
            new_lines: list[str] = []
            added = 0
            for trade in trades:
                key = self._key(trade)
                if key is None:
                    continue
                if key in self._cache:
                    continue
                self._cache[key] = trade
                new_lines.append(json.dumps(trade, ensure_ascii=False, default=str))
                added += 1
            if new_lines:
                try:
                    with self.path.open("a", encoding="utf-8") as f:
                        for line in new_lines:
                            f.write(line + "\n")
                except OSError as err:
                    logger.warning("[swing_sim_cache] append failed: %s", err)
                    return 0
            return added

    def clear(self) -> None:
        """캐시 비우기 (force-refresh 용) — 파일 truncate + 메모리 리셋."""
        with self._lock:
            self._cache.clear()
            self._loaded = True  # 빈 상태로 loaded 간주
            try:
                if self.path.exists():
                    self.path.unlink()
            except OSError as err:
                logger.warning("[swing_sim_cache] clear failed: %s", err)

    def count(self) -> int:
        with self._lock:
            self._load()
            return len(self._cache)
