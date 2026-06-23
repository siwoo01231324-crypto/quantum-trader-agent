"""Bitget 계좌 잔고 검산 — bill 원장 + history-position 교차검증 (read-only).

일별 거래 리포트(`docs/journal/`)의 "잔고 검산" 섹션 생성용. 저널의 일일손익
(history-position netProfit)이 **실제 계좌 잔고 흐름**과 앞뒤로 맞는지 확인한다.

검증 3종:
  1. running balance 연속성 — `/api/v2/mix/account/bill` 의 `balance` 필드로
     일별 종료잔고. 전일 종료 → 당일 종료 Δ 가 당일 흐름과 일치하는지.
  2. 입출금/이체 격리 — businessType 에 trans/deposit/withdraw 가 있으면 잔고
     변동이 트레이딩 외 요인 포함 → 분리 표기 (없으면 "트레이딩 100%").
  3. tie-out — 당일 잔고 Δ ↔ history-position netProfit(저널값). 자정 걸친
     포지션의 open-leg/close-leg 시점차로 ~0.1~0.3 차이는 정상.

사용:
    python scripts/bitget_account_reconcile.py 2026-06-18

자격증명은 `.env`(BITGET_API_KEY/SECRET/PASSPHRASE, mainnet) autoload. 실패 시
graceful (빈 결과 + 사유). 읽기 전용 — 주문/상태 변경 없음 (불변식 #6).
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sys
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

_ROOT = Path(__file__).resolve().parents[1]
_KST = ZoneInfo("Asia/Seoul")


def _autoload_env() -> None:
    env = _ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _creds() -> tuple[str, str, str] | None:
    def s(x: str | None) -> str:
        return (x or "").strip().strip('"').strip("'")
    k = s(os.environ.get("BITGET_API_KEY"))
    sec = s(os.environ.get("BITGET_API_SECRET"))
    pp = s(os.environ.get("BITGET_API_PASSPHRASE"))
    return (k, sec, pp) if (k and sec and pp) else None


def _signed_get(creds: tuple[str, str, str], path: str, params: dict) -> dict:
    key, sec, pp = creds
    ts = str(int(time.time() * 1000))
    qs = "?" + urllib.parse.urlencode(params)
    sig = base64.b64encode(
        hmac.new(sec.encode(), f"{ts}GET{path}{qs}".encode(), hashlib.sha256).digest()
    ).decode()
    headers = {
        "ACCESS-KEY": key, "ACCESS-SIGN": sig, "ACCESS-TIMESTAMP": ts,
        "ACCESS-PASSPHRASE": pp, "Content-Type": "application/json",
    }
    with httpx.Client(timeout=15.0) as c:
        return c.get(f"https://api.bitget.com{path}{qs}", headers=headers).json()


def _kst(ms: int | str) -> datetime:
    import pandas as pd  # lazy
    return pd.Timestamp(int(ms), unit="ms", tz="UTC").tz_convert(_KST)


def fetch_bills(creds, start_ms: int) -> list[dict]:
    """``/api/v2/mix/account/bill`` 전체(페이지네이션, start_ms 까지)."""
    out: list[dict] = []
    end = int(time.time() * 1000)
    for _ in range(20):
        r = _signed_get(creds, "/api/v2/mix/account/bill", {
            "productType": "USDT-FUTURES", "startTime": str(start_ms),
            "endTime": str(end), "limit": "100",
        })
        rows = (r.get("data") or {}).get("bills") or []
        if not rows:
            break
        out += rows
        oldest = min(int(b["cTime"]) for b in rows)
        if oldest <= start_ms or len(rows) < 100:
            break
        end = oldest - 1
    return out


def reconcile(date_kst: str) -> dict:
    _autoload_env()
    creds = _creds()
    if creds is None:
        return {"ok": False, "error": "Bitget 자격증명 누락(.env)"}
    target = datetime.strptime(date_kst, "%Y-%m-%d").replace(tzinfo=_KST)
    # bill 은 전일까지 받아 종료잔고 비교 (시작잔고 = 전일 종료).
    start_ms = int((target - timedelta(days=1)).timestamp() * 1000)
    bills = fetch_bills(creds, start_ms)
    if not bills:
        return {"ok": False, "error": "bill 없음 (code/권한 확인)"}
    rows = []
    for b in bills:
        amt = float(b.get("amount") or 0)
        fee = float(b.get("fee") or 0)
        rows.append({
            "bt": b.get("businessType", ""), "amt": amt, "fee": fee,
            "flow": amt + fee, "bal": float(b.get("balance") or 0),
            "t": int(b["cTime"]), "date": _kst(b["cTime"]).strftime("%Y-%m-%d"),
        })
    rows.sort(key=lambda r: r["t"])
    day = [r for r in rows if r["date"] == date_kst]
    prev = [r for r in rows if r["date"] < date_kst]
    if not day:
        return {"ok": False, "error": f"{date_kst} bill 없음"}
    open_bal = prev[-1]["bal"] if prev else None
    close_bal = day[-1]["bal"]
    fees = sum(r["fee"] for r in day)
    xfer = sum(r["flow"] for r in day
               if any(k in r["bt"].lower() for k in ("trans", "deposit", "withdraw")))
    trade_flow = sum(r["flow"] for r in day) - xfer
    bts = {}
    for r in day:
        bts[r["bt"]] = bts.get(r["bt"], 0) + 1
    return {
        "ok": True, "date_kst": date_kst,
        "open_balance": open_bal, "close_balance": close_bal,
        "balance_delta": (close_bal - open_bal) if open_bal is not None else None,
        "trade_flow": trade_flow, "fees": fees, "transfers_deposits": xfer,
        "business_types": bts, "n_bills": len(day),
        "no_external_flow": abs(xfer) < 1e-9,
    }


def fetch_position_history_pnl(date_kst: str) -> dict:
    """거래소 청산이력(`history-position`) 의 포지션별 netProfit → 일일손익 집계.

    **저널 일일손익의 단일 진실** (규칙 4 — WAL round-trip 금지). 클라우드 routine
    은 Bitget API 직접 접근 불가하므로, 이 *로컬* 함수가 결과를 JSON 의
    ``auto_pnl_ledger`` 필드로 굳혀 routine 이 읽게 한다.

    반환 dict:
      ok / date_kst / source / total_net / wins / losses / gross_win /
      gross_loss / profit_factor / n_positions /
      positions: [{symbol, side, net, open_kst, close_kst}]  (open 시각 오름차순)

    creds 없거나 API 실패 시 ``{"ok": False, "error": ...}`` (graceful — export
    전체를 막지 않음).
    """
    _autoload_env()
    creds = _creds()
    if creds is None:
        return {"ok": False, "error": "Bitget 자격증명 누락(.env)"}
    target = datetime.strptime(date_kst, "%Y-%m-%d").replace(tzinfo=_KST)
    start_ms = int(target.timestamp() * 1000)
    end_ms = int((target + timedelta(days=1)).timestamp() * 1000)
    # 2026-06-24 — 페이지네이션. limit=100 단일 호출은 고거래량일(예: 6/23 108청산)에
    # 가장 최근 100건만 받아 *오래된* 청산을 누락 → 일일손익이 통째로 틀림(6/23
    # -25.63 잘림 vs 실제 -14.45). endTime 을 가장 오래된 ctime 직전으로 되감으며
    # 윈도우 전체를 수집, positionId 로 dedup, day 윈도우로 필터.
    def _ck(p) -> int:
        return int(p.get("ctime") or p.get("cTime") or 0)
    raw: list[dict] = []
    cur_end = end_ms
    for _ in range(40):  # 4000건 상한 (40×100) — 하루 청산수 한참 위
        try:
            r = _signed_get(creds, "/api/v2/mix/position/history-position", {
                "productType": "USDT-FUTURES", "startTime": str(start_ms),
                "endTime": str(cur_end), "limit": "100",
            })
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        if str(r.get("code")) not in ("00000", "0", "None", "none"):
            return {"ok": False, "error": f"code={r.get('code')} msg={r.get('msg')}"}
        page = (r.get("data") or {}).get("list") or []
        if not page:
            break
        raw += page
        oldest = min(_ck(p) for p in page)
        if oldest <= start_ms or len(page) < 100:
            break
        cur_end = oldest - 1  # 다음 페이지는 더 과거로
        time.sleep(0.15)  # rate-limit 여유
    # positionId(없으면 sym+ctime+utime) 로 dedup. 윈도우는 API 의 startTime/endTime
    # 파라미터가 이미 보장(모든 호출이 startTime=start_ms 고정) → 추가 필터 불필요.
    seen: set = set()
    rows: list[dict] = []
    for p in raw:
        pid = p.get("positionId") or (p.get("symbol"), _ck(p), p.get("utime") or p.get("uTime"))
        if pid in seen:
            continue
        seen.add(pid)
        rows.append(p)

    def _hk(ms) -> str:
        try:
            return _kst(ms).strftime("%H:%M")
        except Exception:  # noqa: BLE001
            return ""

    positions = []
    for p in sorted(rows, key=lambda x: int(x.get("ctime") or x.get("cTime") or 0)):
        net = float(p.get("netProfit") or 0)
        positions.append({
            "symbol": p.get("symbol"),
            "side": p.get("holdSide") or p.get("posSide"),
            "net": round(net, 4),
            "open_kst": _hk(p.get("ctime") or p.get("cTime") or 0),
            "close_kst": _hk(p.get("utime") or p.get("uTime") or 0),
        })
    nets = [pp["net"] for pp in positions]
    gross_win = round(sum(n for n in nets if n > 0), 4)
    gross_loss = round(-sum(n for n in nets if n < 0), 4)
    return {
        "ok": True, "date_kst": date_kst,
        "source": "bitget-exchange-history-position",
        "total_net": round(sum(nets), 4),
        "wins": sum(1 for n in nets if n > 0),
        "losses": sum(1 for n in nets if n < 0),
        "gross_win": gross_win, "gross_loss": gross_loss,
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else None,
        "n_positions": len(positions),
        "positions": positions,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date_kst", help="YYYY-MM-DD (KST)")
    ap.add_argument("--pnl", action="store_true",
                    help="잔고검산 대신 history-position 일일손익(auto_pnl_ledger) 출력")
    args = ap.parse_args()
    if args.pnl:
        r = fetch_position_history_pnl(args.date_kst)
        if not r.get("ok"):
            print(f"손익 조회 실패: {r.get('error')}")
            return 1
        print(f"=== {r['date_kst']} 일일손익 (history-position) ===")
        print(f"net {r['total_net']:+.3f} USDT  승{r['wins']}/패{r['losses']}  "
              f"PF {r['profit_factor']}  ({r['n_positions']} 청산)")
        return 0
    r = reconcile(args.date_kst)
    if not r.get("ok"):
        print(f"검산 실패: {r.get('error')}")
        return 1
    print(f"=== {r['date_kst']} 잔고 검산 (bill 원장) ===")
    ob = r["open_balance"]
    print(f"시작잔고(전일종료): {ob:.2f}" if ob is not None else "시작잔고: (이전 bill 없음)")
    print(f"종료잔고          : {r['close_balance']:.2f}")
    if r["balance_delta"] is not None:
        print(f"잔고 Δ            : {r['balance_delta']:+.2f}")
    print(f"  거래 flow        : {r['trade_flow']:+.2f} (수수료 {r['fees']:+.2f} 포함)")
    print(f"  입출금/이체      : {r['transfers_deposits']:+.2f}"
          + ("  ← 트레이딩 100% (외부 유입 없음)" if r["no_external_flow"] else "  ⚠️ 외부 유입 있음"))
    print(f"  businessType     : {r['business_types']}  (bill {r['n_bills']}건)")
    print("→ 저널의 history-position netProfit 과 잔고 Δ 가 ~0.1~0.3 내 일치하면 정합"
          " (차이=자정 걸친 포지션 open/close-leg 시점차).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
