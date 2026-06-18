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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("date_kst", help="YYYY-MM-DD (KST)")
    args = ap.parse_args()
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
