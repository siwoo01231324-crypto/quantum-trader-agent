"""생존편향 제거용 상폐종목 유니버스 로더.

FDR KRX-DELISTING → 보통주 상폐종목의 상장/상폐일·주식수·corp_code 를 제공.
백테스트에서 point-in-time 유니버스(날짜 D 에 거래되던 종목) 구성에 사용:
  종목이 D 에 존재 = ListingDate <= D < DelistingDate (상폐종목) 또는 현재상장(DelistingDate=None).

상폐 유형 필터: 영업회사만 (스팩·수익증권·신탁·신주인수권 제외 — 매출 없어 스크리너서 어차피 배제되나
사전 배제로 페치 절약). 피흡수합병·완전자회사화도 부도 아니므로 표시(가치전략엔 상폐=손실 처리 대상).

캐시: data/cache/fdr/delisted_meta.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
DELISTED_CACHE = ROOT / "data" / "cache" / "fdr" / "delisted_meta.json"

# 비영업(SPAC·펀드·권리) 제외 키워드 (이름 기반)
_EXCLUDE_NAME = ["스팩", "제1호", "제2호", "제3호", "제4호", "제5호", "호스팩",
                 "수익증권", "인수권", "리츠"]
# 상폐 사유 분류
_CAUSE_KEYWORDS = ["상장폐지기준", "투명성", "미해소", "감사의견", "의견거절",
                   "자본잠식", "부도", "파산", "회생", "횡령", "배임"]


def _is_operating(name: str) -> bool:
    n = str(name or "")
    return not any(k in n for k in _EXCLUDE_NAME)


def classify_reason(reason: str) -> str:
    r = str(reason or "")
    if any(k in r for k in _CAUSE_KEYWORDS):
        return "부도성"           # 경영악화 상폐 → 가치전략 최악 시나리오(대손)
    if "합병" in r or "자회사" in r:
        return "합병흡수"         # M&A/완전자회사화 → 통상 프리미엄(손실 아님)
    return "기타"


def load_delisted_meta(start="2016-01-01", end="2026-12-31") -> dict:
    """{code: {name, listing_date, delisting_date, shares, market, reason_class}}."""
    if DELISTED_CACHE.exists():
        return json.loads(DELISTED_CACHE.read_text(encoding="utf-8"))
    import FinanceDataReader as fdr
    d = fdr.StockListing("KRX-DELISTING")
    d["Symbol"] = d["Symbol"].astype(str).str.strip()
    d["DelistingDate"] = pd.to_datetime(d["DelistingDate"], errors="coerce")
    d["ListingDate"] = pd.to_datetime(d["ListingDate"], errors="coerce")
    d = d[(d["SecuGroup"] == "주권") & d["Symbol"].str.match(r"^\d{6}$") &
          d["Market"].isin(["KOSPI", "KOSDAQ"]) &
          (d["DelistingDate"] >= start) & (d["DelistingDate"] <= end)]
    out: dict[str, dict] = {}
    for _, r in d.iterrows():
        if not _is_operating(r["Name"]):
            continue
        shares = r.get("ListingShares")
        out[r["Symbol"]] = {
            "name": str(r["Name"]),
            "listing_date": str(r["ListingDate"].date()) if pd.notna(r["ListingDate"]) else None,
            "delisting_date": str(r["DelistingDate"].date()) if pd.notna(r["DelistingDate"]) else None,
            "shares": float(shares) if pd.notna(shares) else None,
            "market": str(r["Market"]),
            "reason_class": classify_reason(r.get("Reason")),
        }
    DELISTED_CACHE.parent.mkdir(parents=True, exist_ok=True)
    DELISTED_CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    m = load_delisted_meta()
    from collections import Counter
    ct = Counter(v["reason_class"] for v in m.values())
    n_shares = sum(1 for v in m.values() if v["shares"])
    print(f"영업회사 상폐종목 {len(m)}개 | 주식수 보유 {n_shares}")
    print("사유 분류:", dict(ct))
    # 샘플
    for c, v in list(m.items())[:5]:
        print(f"  {c} {v['name'][:14]:<14} 상장 {v['listing_date']} → 상폐 {v['delisting_date']} "
              f"({v['reason_class']})")
