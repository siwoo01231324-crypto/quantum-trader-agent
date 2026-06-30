"""스윙 2전략 진입신호 영속 store — 포착 전수 수집 (체결 여부 무관).

``airborne_fire_store.AirborneFireStore`` 미러. 에어본이 FIRE 를 전수 저장해
``/airborne`` 에 보여주듯, 스윙 진입신호(``orchestrator._on_entry`` 시점 — 사이징
*전*이라 below_min_notional 드롭·미체결 신호도 포함)를 전수 저장해 ``/swing`` 에
"오늘 포착 신호" 로 보여준다. (2026-06-30 — 사용자: 텔레그램엔 오는데 대시보드엔
안 뜨던 문제 = 신호가 WAL signal_emitted 기록 전 사이징서 드롭된 탓.)

설계:
- 단일 JSONL ``logs/swing/signals.jsonl`` (project root 상대).
- dedup key = (strategy, symbol, bar_ts) — bar_ts = ts 를 4h 봉으로 floor.
  같은 4h 봉에서 매분 반복되는 같은 신호는 *1건* 만 저장(스팸 방지, 봉당 1신호).
- 호출자가 since_utc 이후만 read.

schema per line::
    {"ts": "<utc iso>", "strategy": "live-capitulation-bounce", "symbol": "MANAUSDT",
     "stop_loss_pct": 0.0178, "take_profit_pct": 0.0355, "bar_ts": "<4h floor utc iso>"}
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _floor_4h(ts: datetime) -> datetime:
    """UTC 4h 봉 경계로 floor (00/04/08/12/16/20)."""
    ts = ts.astimezone(timezone.utc)
    return ts.replace(hour=ts.hour - (ts.hour % 4), minute=0, second=0, microsecond=0)


class SwingSignalStore:
    """Append-only JSONL — (strategy, symbol, bar_ts) dedup. Thread-safe."""

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
                        str(rec.get("strategy", "")),
                        str(rec.get("symbol", "")),
                        str(rec.get("bar_ts", "")),
                    ))
        except OSError as err:
            logger.warning("[swing_signal_store] dedup cache load failed: %s", err)
        self._loaded = True

    def append(
        self, strategy: str, symbol: str, *,
        stop_loss_pct: float | None = None,
        take_profit_pct: float | None = None,
        ts: datetime | None = None,
    ) -> bool:
        """신호 1건 저장. 같은 (strategy, symbol, 4h봉) 중복이면 skip. 저장 여부 반환.

        fail-soft — 예외 안 냄(거래 hot-path 에서 호출되므로). ts 미지정 시 now(UTC).
        """
        with self._lock:
            try:
                self._load_dedup_cache()
                t = (ts or datetime.now(timezone.utc)).astimezone(timezone.utc)
                bar_ts = _floor_4h(t).isoformat()
                if not (strategy and symbol):
                    return False
                key = (strategy, symbol, bar_ts)
                if key in self._seen:
                    return False
                self._seen.add(key)
                rec = {
                    "ts": t.isoformat(), "strategy": strategy, "symbol": symbol,
                    "stop_loss_pct": (float(stop_loss_pct) if stop_loss_pct is not None else None),
                    "take_profit_pct": (float(take_profit_pct) if take_profit_pct is not None else None),
                    "bar_ts": bar_ts,
                }
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                return True
            except Exception as err:  # noqa: BLE001 — 신호저장 실패가 거래 막지 않음
                logger.warning("[swing_signal_store] append failed: %s", err)
                return False

    def load_since(self, since_utc: datetime) -> list[dict]:
        """since_utc 이후 신호 list (ts 오름차순). 파일부재 → 빈 list (never raise)."""
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
            logger.warning("[swing_signal_store] load_since read failed: %s", err)
            return []
        out.sort(key=lambda r: str(r.get("ts", "")))
        return out

    def count(self) -> int:
        with self._lock:
            self._load_dedup_cache()
            return len(self._seen)
