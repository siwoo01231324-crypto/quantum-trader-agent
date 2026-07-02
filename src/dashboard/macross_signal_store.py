"""macross 전략 *실제 신호* store — 진입까지 안 간 "스킵 신호" 영속화.

배경: ma_cross 데몬(정시·마감봉·주식오염)은 실제 전략(intra-hour·형성봉·크립토)과
어긋나 폐기. 대신 전략 자신의 평가(strategy_evaluated) 를 수집한다.
  - 진입(entered): WAL order_filled → macross_entry_store (별도).
  - **스킵(skipped)**: 크로스를 감지했으나 필터(레짐/ADX/기울기/과확장/시간게이트)에
    걸려 진입 안 함 → 본 store. "포착했지만 왜 스킵했나" 역추적용.

수집 경로: orchestrator._emit_strategy_evaluated → live_run._wal_observer 가 매
(전략,종목) 평가마다 fan-out → 본 store.ingest() 가 macross 크로스-hold 만 필터.
봉당 dedup (같은 종목·시각·사유 1회) — no_cross 잡음은 제외.

이벤트 payload: {strategy_id, symbol, decision("hold"/"buy"/"sell"), reason}.
스킵 = decision=hold AND reason 이 크로스 감지 후 필터(아래 _PRE_CROSS 제외).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_MACROSS_SID = "live-macross-regime-v1"

# 크로스 감지 *전* 사유 — 스킵 신호 아님(크로스 자체가 없거나 warmup). 제외.
_PRE_CROSS_REASONS = frozenset({"warmup", "no_cross"})

# 사유 → 사람용 카테고리 (스킵 분류).
_REASON_CATEGORY = (
    ("slope", "SMA200 기울기"),
    ("adx", "ADX<20 (추세약)"),
    ("regime", "BTC 레짐 역행"),
    ("self_sma200", "자기 SMA200 위"),
    ("overext", "과확장(추격금지)"),
    ("kst", "시간게이트 밖"),
    ("hour", "시간게이트 밖"),
    ("long_disabled", "롱 비활성"),
    ("short_disabled", "숏 비활성"),
)


def _categorize(reason: str) -> str:
    r = (reason or "").lower()
    for key, label in _REASON_CATEGORY:
        if key in r:
            return label
    return reason or "기타"


class MacrossSignalStore:
    """macross 스킵 신호 append-only jsonl (봉당 dedup)."""

    def __init__(self, path: str | Path, dedup_window: int = 5000) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen: set[tuple] = set()
        self._seen_order: list[tuple] = []
        self._dedup_window = dedup_window

    def ingest(self, event_type: str, payload: dict) -> None:
        """WAL fan-out consumer — macross 크로스-hold 만 골라 기록. fail-soft."""
        try:
            if event_type != "strategy_evaluated":
                return
            if payload.get("strategy_id") != _MACROSS_SID:
                return
            if payload.get("decision") != "hold":
                return
            reason = str(payload.get("reason", ""))
            base = reason.split(":")[0].split("(")[0].strip()
            if base in _PRE_CROSS_REASONS:
                return  # 크로스 없음/warmup — 스킵 신호 아님
            symbol = payload.get("symbol")
            if not symbol:
                return
            # 봉당(1h) dedup — 같은 종목·시각버킷·카테고리 1회.
            now = datetime.now(timezone.utc)
            bar_ts = now.replace(minute=0, second=0, microsecond=0).isoformat()
            cat = _categorize(reason)
            key = (symbol, bar_ts, cat)
            with self._lock:
                if key in self._seen:
                    return
                self._seen.add(key)
                self._seen_order.append(key)
                if len(self._seen_order) > self._dedup_window:
                    old = self._seen_order.pop(0)
                    self._seen.discard(old)
                rec = {
                    "ts": now.isoformat(), "symbol": symbol,
                    "reason": reason, "category": cat, "bar_ts": bar_ts,
                }
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001 — 신호 기록 실패가 매매/평가 안 깬다
            return

    def recent(self, limit: int = 200) -> list[dict]:
        """최신 스킵 신호 (최신순). 파일 없으면 빈 list."""
        try:
            with open(self._path, encoding="utf-8") as f:
                rows = [json.loads(x) for x in f if x.strip()]
            rows.sort(key=lambda r: r.get("ts", ""), reverse=True)
            return rows[:limit]
        except (OSError, ValueError):
            return []
