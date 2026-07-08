"""WAL → macross(데드크로스 숏) *실제 진입* 이력 파서.

/ma-cross 대시보드의 "실제 진입" 토글 뷰 데이터 소스. ma_cross 데몬(하시간 REST
폴링, 토큰화주식 오염, mid-hour 미집계)의 근사 신호와 달리, **실거래 WAL** 을 읽어
전략이 실제 체결한 진입만 데이터화한다. override·정시 무관 — 모든 실진입을 그때그때
기록하는 WAL 이 정본. 과거 WAL 이 이미 다 있어 backfill(역추적) 은 파싱만으로 완성.

레코드: 진입(전략·종목·방향·체결가·수량) + SL/TP(정적 2%/12%) + 청산·실현손익·상태.
macross 는 숏 전용(allow_long=False) — 진입 fill = SELL, 청산 fill = BUY.

WAL 이벤트(order_filled payload): strategy_id, symbol, side, fill_price, fill_qty,
trade_id, ts. 여러 run 디렉토리(재시작마다) 를 합쳐 trade_id 로 dedup.
"""
from __future__ import annotations

import glob
import json
from dataclasses import asdict, dataclass

_MACROSS_SID = "live-macross-regime-v1"
# 정적 손익비 (LiveMacrossRegime.stop_loss_pct / take_profit_pct, RR 1:6).
_SL_PCT = 0.02
_TP_PCT = 0.12
_WAL_GLOB = "logs/shadow-swing*/*/wal.jsonl"


@dataclass
class MacrossEntry:
    entry_ts: str
    symbol: str
    side: str            # "short" (macross 숏 전용)
    entry_price: float
    qty: float
    sl_price: float      # 숏: entry×(1+SL) — 진입가 위
    tp_price: float      # 숏: entry×(1-TP) — 진입가 아래
    status: str          # "open" | "closed"
    exit_ts: str | None = None
    exit_price: float | None = None
    pnl_pct: float | None = None      # 가격기준 실현손익% (숏: (entry-exit)/entry)
    realized_usdt: float | None = None  # 실현손익 USDT (숏: qty×(entry-exit), 수수료 前)
    outcome: str | None = None        # "tp" | "sl" | "manual"


def _iter_macross_fills(wal_glob: str = _WAL_GLOB):
    """macross-태그 fill + 태그없는(수동 청산) fill 을 trade_id dedup 후 ts 오름차순.

    거래소에서 직접 수동 청산하면 봇 client_order_id 가 없어 strategy_id=None 으로
    기록된다(태그없음). 이들도 포함해 parse 가 symbol+FIFO 로 열린 macross 숏에
    청산으로 귀속시킨다. 다른 전략(airborne 등) 으로 태그된 fill 은 그 전략 소유라
    제외 — 오귀속 방지.
    """
    seen: set[str] = set()
    fills: list[dict] = []
    for path in glob.glob(wal_glob):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or "order_filled" not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except (ValueError, json.JSONDecodeError):
                        continue
                    if rec.get("event_type") != "order_filled":
                        continue
                    p = rec.get("payload", {})
                    # macross 태그 또는 태그없음(수동)만. 타 전략 소유 fill 제외.
                    if p.get("strategy_id") not in (_MACROSS_SID, None, ""):
                        continue
                    tid = str(p.get("trade_id") or p.get("client_order_id") or "")
                    if tid and tid in seen:
                        continue
                    if tid:
                        seen.add(tid)
                    fills.append(p)
        except OSError:
            continue
    fills.sort(key=lambda p: str(p.get("ts", "")))
    return fills


def parse_macross_entries(
    wal_glob: str = _WAL_GLOB, sl_pct: float = _SL_PCT, tp_pct: float = _TP_PCT,
) -> list[dict]:
    """WAL → macross 진입 이력 (최신순). 숏 진입(SELL) ↔ 청산(BUY) FIFO 페어링.

    반환: MacrossEntry dict 리스트 (entry_ts desc). 미청산은 status="open".
    """
    fills = _iter_macross_fills(wal_glob)
    open_shorts: dict[str, list[MacrossEntry]] = {}   # symbol → 미청산 진입 큐
    entries: list[MacrossEntry] = []

    for p in fills:
        sym = p.get("symbol")
        side = str(p.get("side", "")).upper()
        try:
            price = float(p.get("fill_price"))
            qty = float(p.get("fill_qty") or p.get("qty") or 0.0)
        except (TypeError, ValueError):
            continue
        if not sym or price <= 0:
            continue
        ts = str(p.get("ts", ""))

        if side == "SELL":  # 숏 진입 (open) — macross 태그만 진입으로 인정
            if p.get("strategy_id") != _MACROSS_SID:
                continue  # 태그없는 SELL 은 macross 진입 아님 (수동 신규 숏 등)
            e = MacrossEntry(
                entry_ts=ts, symbol=sym, side="short", entry_price=price, qty=qty,
                sl_price=round(price * (1 + sl_pct), 8),
                tp_price=round(price * (1 - tp_pct), 8),
                status="open",
            )
            entries.append(e)
            open_shorts.setdefault(sym, []).append(e)
        elif side == "BUY":  # 숏 청산 (close) — 가장 오래된 open 부터 FIFO 매칭
            queue = open_shorts.get(sym)
            if not queue:
                continue  # 대응 진입 없는 청산 (외부 롱 등) — skip
            e = queue.pop(0)
            e.status = "closed"
            e.exit_ts = ts
            e.exit_price = price
            # 숏 실현손익%: (진입 - 청산) / 진입 × 100. USDT: qty × (진입 - 청산).
            e.pnl_pct = round((e.entry_price - price) / e.entry_price * 100, 4)
            e.realized_usdt = round(e.qty * (e.entry_price - price), 4)
            # outcome 판정. 봇 청산(태그 O)은 거래소 TP/SL plan order → 가장 가까운
            # 타깃으로 tp/sl 분류(슬리피지 오분류 방지, 2026-07-06). 태그없는 청산은
            # 사용자가 거래소에서 직접 수동 청산한 것 → "manual".
            if p.get("strategy_id") in (None, ""):
                e.outcome = "manual"
            else:
                e.outcome = "sl" if abs(price - e.sl_price) <= abs(price - e.tp_price) else "tp"

    entries.sort(key=lambda e: e.entry_ts, reverse=True)
    return [asdict(e) for e in entries]


def summarize_macross_entries(rows: list[dict]) -> dict:
    """진입 이력 → 집계 (라이브 실현 NET·승패·PF·gross%). /ma-cross 라이브 지표용.

    macross 는 거래소 실현손익 ledger 를 여기선 안 붙이고 가격기준(qty×이동) 근사.
    정확 실현손익(수수료·펀딩 포함)은 거래소 ledger 별도."""
    closed = [r for r in rows if r.get("status") == "closed"]
    open_n = sum(1 for r in rows if r.get("status") == "open")
    net_usdt = round(sum(float(r.get("realized_usdt") or 0.0) for r in closed), 4)
    gross_pct = round(sum(float(r.get("pnl_pct") or 0.0) for r in closed), 4)
    wins = [r for r in closed if (r.get("pnl_pct") or 0) > 0]
    losses = [r for r in closed if (r.get("pnl_pct") or 0) <= 0]
    gw = sum(float(r["pnl_pct"]) for r in wins)
    gl = -sum(float(r["pnl_pct"]) for r in losses)
    pf = round(gw / gl, 2) if gl > 0 else (None if gw == 0 else 9.99)
    n_tp = sum(1 for r in closed if r.get("outcome") == "tp")
    n_sl = sum(1 for r in closed if r.get("outcome") == "sl")
    return {
        "n_total": len(rows), "n_open": open_n, "n_closed": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100, 1) if closed else None,
        "net_usdt": net_usdt, "gross_pct": gross_pct, "net_pct": gross_pct, "pf": pf,
        "n_tp": n_tp, "n_sl": n_sl,
    }


if __name__ == "__main__":  # 수동 검증
    import sys
    rows = parse_macross_entries(sys.argv[1] if len(sys.argv) > 1 else _WAL_GLOB)
    print(f"macross 실진입 {len(rows)}건:")
    for r in rows:
        print(f"  {r['entry_ts'][:19]} {r['symbol']:<10} {r['side']} "
              f"진입={r['entry_price']} SL={r['sl_price']} TP={r['tp_price']} "
              f"[{r['status']}] "
              + (f"청산={r['exit_price']} PnL={r['pnl_pct']}% ({r['outcome']})"
                 if r['status'] == 'closed' else ""))
