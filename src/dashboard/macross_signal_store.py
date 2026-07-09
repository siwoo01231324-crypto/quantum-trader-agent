"""macross 전략 *진입 신호* store — "실제로 진입했을 타이밍" 영속화.

배경: ma_cross 데몬(정시·마감봉·주식오염)은 실제 전략(intra-hour·형성봉·크립토)과
어긋나 폐기. 대신 전략 자신의 평가(strategy_evaluated) 중 **진입 신호(buy/sell)** 만
수집한다. observe_only(관찰) 모드에선 실주문 없이 이 진입 신호만 남으므로 "실제로
진입했을 타이밍" 데이터가 여기로 모인다. hold/스킵은 노이즈라 기록하지 않는다.

수집 경로: orchestrator._emit_strategy_evaluated → live_run._wal_observer 가 매
(전략,종목) 평가마다 fan-out → 본 store.ingest() 가 macross 진입(buy/sell) 만 필터.
봉당 dedup (같은 종목·시각·방향 1회).

이벤트 payload: {strategy_id, symbol, decision("buy"/"sell"), reason}.
관찰 모드는 reason 에 "|observe" 표식(orchestrator HARD GUARD).
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

_MACROSS_SID = "live-macross-regime-v1"
# macross 정적 손익비 (LiveMacrossRegime kwargs: stop_loss_pct 0.02 / take_profit_pct 0.12).
# 대시보드 "실제 진입" 뷰와 동일 — SL/TP 라인 계산용.
_SL_PCT = 0.02
_TP_PCT = 0.12


class MacrossSignalStore:
    """macross 진입 신호 append-only jsonl (봉당 dedup)."""

    def __init__(self, path: str | Path, dedup_window: int = 5000) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seen: set[tuple] = set()
        self._seen_order: list[tuple] = []
        self._dedup_window = dedup_window

    def ingest(self, event_type: str, payload: dict) -> None:
        """WAL fan-out consumer — macross 진입(buy/sell) 신호만 골라 기록. fail-soft.

        observe_only(관찰) 모드에선 실주문 없이 이 진입 신호만 남으므로 "실제로
        진입했을 타이밍" 데이터가 여기로 수집된다. hold/스킵은 노이즈라 제외.
        """
        try:
            if event_type != "strategy_evaluated":
                return
            if payload.get("strategy_id") != _MACROSS_SID:
                return
            decision = payload.get("decision")
            symbol = payload.get("symbol")
            if not symbol:
                return
            reason = str(payload.get("reason", ""))
            # 진입(buy/sell) 신호만 수집 — "실제로 진입했을 타이밍". hold/스킵은
            # 노이즈라 기록 안 함 (orchestrator 가 hold reason 을 action_hold 로
            # 덮어써 분류도 불가). observe_only 모드에선 실주문 없이 이 진입 신호만 남는다.
            if decision in ("buy", "sell"):
                kind = "entry"
                observe = "|observe" in reason
                is_short = decision == "sell"
                side = "숏" if is_short else "롱"
                cat = f"🔻 진입포착({'관찰' if observe else '실'}·{side})"
            else:
                return
            # 진입가 + SL/TP 라인 (대시보드 "실제 진입" 뷰와 동일 형식).
            #   숏: SL=진입×(1+2%) 위, TP=진입×(1−12%) 아래. 롱: 반대.
            entry_price = payload.get("price")
            sl_price = tp_price = None
            try:
                if entry_price is not None:
                    entry_price = float(entry_price)
                    if is_short:
                        sl_price = round(entry_price * (1 + _SL_PCT), 8)
                        tp_price = round(entry_price * (1 - _TP_PCT), 8)
                    else:
                        sl_price = round(entry_price * (1 - _SL_PCT), 8)
                        tp_price = round(entry_price * (1 + _TP_PCT), 8)
            except (TypeError, ValueError):
                entry_price = None
            # 봉당(1h) dedup — 같은 종목·시각버킷·카테고리 1회.
            now = datetime.now(timezone.utc)
            bar_ts = now.replace(minute=0, second=0, microsecond=0).isoformat()
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
                    "ts": now.isoformat(), "symbol": symbol, "kind": kind,
                    "decision": decision, "reason": reason,
                    "category": cat, "bar_ts": bar_ts, "side": side,
                    "entry_price": entry_price,
                    "sl_price": sl_price, "tp_price": tp_price,
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
