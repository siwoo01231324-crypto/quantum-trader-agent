"""Read-only LIVE (testnet/demo) aggregation for the two swing strategies.

``/swing`` 페이지의 *백테스트 sim (5y)* 섹션 옆에 붙는 **라이브 뷰** 데이터 소스다.
두 스윙 전략(``live-capitulation-bounce`` · ``live-donchian-breakout-btcgate``)이
binance-testnet / bitget-demo 페이퍼로 라이브 구동되며 WAL 을 적재한다:

  - ``logs/shadow-swing-binance/<run_id>/wal.jsonl`` (binance-testnet)
  - ``logs/shadow-swing/<run_id>/wal.jsonl``         (bitget-demo)

본 모듈은 그 WAL 들을 읽어(``src.live.trade_history.reconstruct_trades`` 의
롱/숏 + flip 페어링을 그대로 재사용) 선택 윈도우(KST 자정 경계)의 신호·라운드트립
거래·실현 NET 을 집계한다. **READ-ONLY** — 주문/리스크/전략 코드 미수정. 아직
거래가 없는 윈도우(테스트넷 막 시작 / 4h 신호 희소)는 graceful 빈 집계(n=0) 를
반환해 페이지가 "아직 거래 없음" 빈 상태를 그릴 수 있게 한다.

핵심 함수:
  - ``discover_swing_wal_files(repo_root)`` — 두 디렉토리의 모든 run WAL glob.
  - ``aggregate_swing_live(wal_paths, since_utc, until_utc)`` — 순수 집계(테스트 대상).
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from src.live.trade_history import reconstruct_trades
from src.live.wal import replay as _wal_replay

# 라이브 스윙 데몬 WAL 루트 (binance-testnet + bitget-demo 둘 다 스캔).
SWING_LIVE_LOG_DIRS: tuple[str, ...] = ("logs/shadow-swing", "logs/shadow-swing-binance")

# 두 스윙 전략 id (다른 전략 fill 이 같은 WAL 에 섞여도 걸러낸다).
SWING_LIVE_STRATEGY_IDS: frozenset[str] = frozenset(
    {"live-capitulation-bounce", "live-donchian-breakout-btcgate"}
)

# 전략 id → 사람이 읽는 라벨 (app.SWING_STRATEGIES 와 동일하지만 모듈 독립성 위해 복제).
SWING_LIVE_LABELS: dict[str, str] = {
    "live-capitulation-bounce": "투매반등 (평균회귀)",
    "live-donchian-breakout-btcgate": "돌파/터틀 (추세추종)",
}


def discover_swing_wal_files(repo_root: Path | str) -> list[Path]:
    """두 스윙 라이브 로그 디렉토리의 모든 ``<run_id>/wal.jsonl`` 을 glob.

    ``logs/shadow-swing*`` (binance-testnet + bitget-demo) 아래 모든 run 의 WAL 을
    결정적으로 정렬해 반환한다. 디렉토리 부재(아직 한 번도 안 돌린 부팅 직후)는
    빈 list — 정상. 경로 정렬이라 cross-run replay 가 재현 가능하다.
    """
    root = Path(repo_root)
    paths: list[Path] = []
    for sub in SWING_LIVE_LOG_DIRS:
        d = root / sub
        if not d.is_dir():
            continue
        paths.extend(d.glob("*/wal.jsonl"))
    return sorted(set(paths))


# 청산 사유 reason → 표시 라벨. signal_emitted 의 exit intent reason 은
# `src/portfolio/live_position_risk.py` 가 `live_stop_loss` / `live_take_profit` /
# `live_trailing_stop` prefix 를, 채널청산은 `channel_exit` 를 단다(전략별 상이).
def exit_reason_label(reason: str | None) -> str:
    r = str(reason or "")
    rl = r.lower()
    if "take_profit" in rl or rl.startswith("tp") or "live_take_profit" in rl:
        return "익절 (tp)"
    if "stop_loss" in rl or rl.startswith("stop") or "live_stop_loss" in rl:
        return "손절 (stop)"
    if "trailing" in rl:
        return "트레일링 청산"
    if "channel" in rl:
        return "채널청산"
    if "max_hold" in rl or "timeout" in rl or "time_stop" in rl:
        return "시간청산"
    return r or "청산"


def _parse_ts(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t


def _in_window(iso: str | None, since: datetime, until: datetime) -> bool:
    t = _parse_ts(iso)
    return t is not None and since <= t < until


def _pct(side: str, entry: float | None, exit_: float | None) -> float | None:
    """진입·청산가에서 부호 인지 % 수익 (롱=(exit-entry), 숏=(entry-exit))."""
    if entry is None or exit_ is None or entry == 0:
        return None
    if side == "short":
        return (entry - exit_) / entry * 100.0
    return (exit_ - entry) / entry * 100.0


def _collect_signals(
    wal_paths: Iterable[Path | str], strategy_ids: frozenset[str],
    since: datetime, until: datetime,
) -> tuple[list[dict], dict[tuple[str, str], list[tuple[datetime, str]]]]:
    """signal_emitted 이벤트 수집.

    반환:
      - entry_signals: 윈도우 내 진입(side=buy, exit reason 아님) signal 리스트.
      - exit_reason_map: (strategy, symbol) → [(ts, reason)] (side=sell) — 라운드트립
        거래의 청산 사유를 exit_ts 근접 매칭으로 붙이기 위함.
    """
    entry_signals: list[dict] = []
    exit_reason_map: dict[tuple[str, str], list[tuple[datetime, str]]] = {}
    for path in wal_paths:
        events, _ = _wal_replay(Path(path))
        for ev in events:
            if ev.event_type != "signal_emitted":
                continue
            pl = ev.payload or {}
            sid = pl.get("strategy_id")
            if sid not in strategy_ids:
                continue
            symbol = pl.get("symbol", "")
            side = str(pl.get("side", "")).lower()
            reason = pl.get("reason", "")
            ts = ev.ts
            if side == "buy":
                if _in_window(ts, since, until):
                    entry_signals.append({
                        "ts": ts, "symbol": symbol, "side": "buy",
                        "strategy": sid, "reason": reason,
                    })
            else:  # sell → 청산 사유 후보
                t = _parse_ts(ts)
                if t is not None:
                    exit_reason_map.setdefault((sid, symbol), []).append((t, str(reason)))
    return entry_signals, exit_reason_map


def _match_exit_reason(
    exit_reason_map: dict[tuple[str, str], list[tuple[datetime, str]]],
    strategy: str, symbol: str, exit_ts: str | None,
) -> str | None:
    """청산 시각에 가장 가까운 sell signal reason 을 best-effort 매칭."""
    cands = exit_reason_map.get((strategy, symbol))
    et = _parse_ts(exit_ts)
    if not cands or et is None:
        return None
    best: tuple[float, str] | None = None
    for t, reason in cands:
        delta = abs((t - et).total_seconds())
        if delta <= 86400 and (best is None or delta < best[0]):
            best = (delta, reason)
    return best[1] if best else None


def aggregate_swing_live(
    wal_paths: list[Path] | list[str],
    since_utc: datetime,
    until_utc: datetime,
    strategy_ids: frozenset[str] = SWING_LIVE_STRATEGY_IDS,
) -> dict:
    """라이브 스윙 WAL → 윈도우 집계 (순수·결정적, 테스트 대상).

    페어링은 ``reconstruct_trades`` 의 롱/숏+flip 회계를 그대로 재사용 — 전체 WAL 을
    replay 해야 cross-run 포지션이 올바로 닫히므로 *모든* path 를 넘긴다. 그 후
    윈도우(KST 자정 경계, UTC 로 환산해 전달됨) 로 거래/신호를 필터한다.

    Window 귀속:
      - 청산거래(status=closed): exit_ts ∈ [since, until) — 실현 NET·승패 집계.
      - 미청산(status=open): entry_ts < until — 현재 보유로 표에 노출.

    Returns dict (graceful — 데이터 없으면 n=0):
      n_signals / n_trades_closed / wins / losses / net_pnl / net_currency /
      open_positions / trades[] / signals[].
    """
    all_trades = reconstruct_trades([Path(p) for p in wal_paths])
    all_trades = [t for t in all_trades if t.strategy_id in strategy_ids]

    entry_signals, exit_reason_map = _collect_signals(
        wal_paths, strategy_ids, since_utc, until_utc,
    )

    rows: list[dict] = []
    net_pnl = 0.0
    wins = losses = n_closed = open_positions = 0

    for t in all_trades:
        label = SWING_LIVE_LABELS.get(t.strategy_id, t.strategy_id)
        if t.status == "closed":
            if not _in_window(t.exit_ts, since_utc, until_utc):
                continue
            n_closed += 1
            pnl = t.realized_pnl or 0.0
            net_pnl += pnl
            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1
            reason = _match_exit_reason(
                exit_reason_map, t.strategy_id, t.symbol, t.exit_ts,
            )
            rows.append({
                "entry_ts": t.entry_ts, "exit_ts": t.exit_ts,
                "symbol": t.symbol, "side": t.side,
                "strategy": t.strategy_id, "strategy_label": label,
                "venue": t.venue,
                "entry_price": t.entry_price, "exit_price": t.exit_price,
                "qty": t.qty,
                "status": "closed",
                "reason": reason,
                "status_label": exit_reason_label(reason),
                "pct": _pct(t.side, t.entry_price, t.exit_price),
                "pnl": pnl,
            })
        else:  # open
            if _parse_ts(t.entry_ts) is not None and _parse_ts(t.entry_ts) < until_utc:
                open_positions += 1
                rows.append({
                    "entry_ts": t.entry_ts, "exit_ts": None,
                    "symbol": t.symbol, "side": t.side,
                    "strategy": t.strategy_id, "strategy_label": label,
                    "venue": t.venue,
                    "entry_price": t.entry_price, "exit_price": None,
                    "qty": t.qty,
                    "status": "open",
                    "reason": None,
                    "status_label": "보유중",
                    "pct": None,
                    "pnl": None,
                })

    # 표는 최신순(청산거래는 exit_ts, 보유는 entry_ts 기준).
    rows.sort(key=lambda r: str(r.get("exit_ts") or r.get("entry_ts") or ""), reverse=True)
    entry_signals.sort(key=lambda s: str(s.get("ts") or ""), reverse=True)

    return {
        "n_signals": len(entry_signals),
        "n_trades_closed": n_closed,
        "wins": wins,
        "losses": losses,
        "net_pnl": net_pnl,
        "net_currency": "USDT",
        "open_positions": open_positions,
        "trades": rows,
        "signals": entry_signals,
    }
