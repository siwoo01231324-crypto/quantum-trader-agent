"""생존편향 보정 백테스트 — 상폐종목 포함 point-in-time 유니버스.

기존 백테스트(_value_screener_backtest.py)는 오늘 살아남은 종목만으로 과거를 돌려 낙관 편향.
본 스크립트: 상폐종목(277개, _survivorship)을 유니버스에 추가하고, 각 리밸일 D 에
  "그 시점 거래 중이던 종목"(상장<=D<상폐)만 후보로 → 진짜 point-in-time.
  보유 중 상폐되면 상폐 직전가로 청산(부도성이면 대개 급락가 = 대손 반영).

비교: 보정 전(현 유니버스만) vs 보정 후(상폐 포함) CAGR 격차 = 생존편향 크기.

전략: 스크리너 4게이트 + A/C, 고정 top-N 매년 5/1 리밸 1년 보유 (exit 방식 무관하게
      편향 크기 측정엔 고정 리밸이 명확).

실행: python scripts/_value_screener_backtest_sv.py   (리서치)
결과: stdout + reports/value_screener_backtest_sv.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _value_backtest as vb
import _value_screener as vs
import _value_screener_backtest as bt
import _survivorship as sv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REBAL_YEARS = list(range(2019, 2026))
TOP_N = 10
MARCAP_FLOOR_BT = 100e8


def log(msg: str) -> None:
    print(f"[sv] {msg}", flush=True)


def load_current_listing_dates() -> dict:
    """현재 상장종목의 상장일 (KRX-DESC). IPO 이전 제외용."""
    import FinanceDataReader as fdr
    d = fdr.StockListing("KRX-DESC")
    d["Code"] = d["Code"].astype(str).str.zfill(6)
    out = {}
    for _, r in d.iterrows():
        ld = r.get("ListingDate")
        out[r["Code"]] = pd.to_datetime(ld, errors="coerce")
    return out


def member_at(code, d, listed_map, delisted_meta) -> bool:
    """날짜 d 에 code 가 거래 중이었나 (상장<=d<상폐)."""
    ld = listed_map.get(code)
    if ld is not None and pd.notna(ld) and ld > d:
        return False   # IPO 이전
    dm = delisted_meta.get(code)
    if dm:
        dd = pd.to_datetime(dm["delisting_date"], errors="coerce")
        if pd.notna(dd) and dd <= d:
            return False  # 이미 상폐
    return True


def fetch_delisted_data(corp_map, delisted_meta):
    """상폐종목 재무(9년) + 주가 페치."""
    codes = list(delisted_meta)
    fins = {}
    for i, code in enumerate(codes):
        corp = corp_map.get(code)
        if not corp:
            continue
        per_year = {}
        for y in vb.FIN_YEARS:
            cache = vb.FIN_CACHE / f"{corp}_{y}.json"
            existed = cache.exists()
            parsed = vb.parse_fin(vb.fetch_fin_raw(corp, y))
            if parsed:
                per_year[y] = parsed
            if not existed:
                time.sleep(0.03)
        if per_year:
            fins[code] = per_year
        if (i + 1) % 50 == 0:
            log(f"상폐 재무 {i+1}/{len(codes)}")
    prices = {}
    for i, code in enumerate(codes):
        s = vb.load_price(code)
        if s is not None and len(s):
            prices[code] = s
        if (i + 1) % 50 == 0:
            log(f"상폐 주가 {i+1}/{len(codes)}")
    log(f"상폐 재무 {len(fins)} · 주가 {len(prices)}")
    return fins, prices


def fwd_return_sv(prices, code, buy, sell, delisted_meta):
    """상폐 반영 forward return. 보유 중 상폐 시 상폐 직전가로 청산."""
    s = prices.get(code)
    if s is None:
        return None
    dm = delisted_meta.get(code)
    eff_sell = sell
    if dm:
        dd = pd.to_datetime(dm["delisting_date"], errors="coerce")
        if pd.notna(dd) and buy < dd <= sell:
            eff_sell = dd  # 상폐일 직전가로 청산
    p0 = vb.price_asof(s, buy)
    p1 = vb.price_asof(s, eff_sell)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def run(codes, fins, prices, shares_map, name_map, sector_map, ks,
        listed_map, delisted_meta, corrected: bool):
    """corrected=True 면 상폐 포함 유니버스 + point-in-time 멤버십 + 상폐청산."""
    yearly = []
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01")
        sell = pd.Timestamp(f"{y + 1}-05-01")
        if corrected:
            pool = [c for c in codes if member_at(c, buy, listed_map, delisted_meta)]
        else:
            pool = [c for c in codes if c not in delisted_meta]  # 현 유니버스만
        df = bt.build_year_rows(pool, fins, shares_map, name_map, prices,
                                buy, y - 1, y - 2, sector_map)
        top = bt.screen_top_n(df) if hasattr(bt, "screen_top_n") else _top(df)
        rets, picks = [], []
        for _, r in top.iterrows():
            fr = (fwd_return_sv(prices, r["code"], buy, sell, delisted_meta)
                  if corrected else vb.forward_return(prices, r["code"], buy, sell))
            picks.append({"name": r["name"], "delisted": r["code"] in delisted_meta,
                          "ret%": round(fr * 100, 1) if fr is not None else None})
            if fr is not None:
                rets.append(fr)
        pr = float(np.mean(rets)) if rets else None
        br = vb.kospi_year_return(ks, buy, sell)
        n_del = sum(1 for p in picks if p["delisted"])
        yearly.append({"year": y, "n_pool": len(pool), "n_priced": len(rets),
                       "port%": round(pr * 100, 1) if pr is not None else None,
                       "kospi%": round(br * 100, 1) if br is not None else None,
                       "n_delisted_picks": n_del, "picks": picks})
    yr = [r["port%"] / 100 for r in yearly if r["port%"] is not None]
    stats = vb.equity_curve_stats(yr)
    return yearly, stats


def _top(df):
    if df.empty:
        return df
    survivors, _ = vs.run_funnel(df)
    return vs.rank_survivors(survivors).head(TOP_N)


def main() -> None:
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    cur_codes = list(listing.index)
    delisted_meta = sv.load_delisted_meta()
    sector_map = vs.get_sector_map()

    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in cur_codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in cur_codes}
    for c, v in delisted_meta.items():
        if v.get("shares"):
            shares_map[c] = v["shares"]
            name_map[c] = v["name"]

    log(f"현 유니버스 {len(cur_codes)} + 상폐 {len(delisted_meta)} = {len(shares_map)}")
    listed_map = load_current_listing_dates()

    # 현 유니버스 재무·주가 (캐시)
    fins = vb.fetch_all_financials(corp_map, cur_codes)
    prices = vb.load_all_prices(cur_codes)
    # 상폐종목 재무·주가 추가
    dfins, dprices = fetch_delisted_data(corp_map, delisted_meta)
    fins.update(dfins)
    prices.update(dprices)

    import FinanceDataReader as fdr
    ks_cache = vb.FDR_CACHE / "KS11.csv"
    ks = (pd.read_csv(ks_cache, index_col=0, parse_dates=True)["Close"] if ks_cache.exists()
          else fdr.DataReader("KS11", "2015-01-01", "2026-12-31")["Close"].dropna())

    all_codes = list(shares_map)
    log("보정 전(현 유니버스만) 백테스트...")
    y_un, s_un = run(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
                     listed_map, delisted_meta, corrected=False)
    log("보정 후(상폐 포함 point-in-time) 백테스트...")
    y_co, s_co = run(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
                     listed_map, delisted_meta, corrected=True)

    result = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "rebal_years": REBAL_YEARS, "top_n": TOP_N,
        "n_current": len(cur_codes), "n_delisted": len(delisted_meta),
        "uncorrected": {"CAGR%": round(s_un["CAGR"] * 100, 1) if s_un["CAGR"] is not None else None,
                        "MDD%": round(s_un["MDD"] * 100, 1) if s_un["MDD"] is not None else None,
                        "yearly": y_un},
        "corrected": {"CAGR%": round(s_co["CAGR"] * 100, 1) if s_co["CAGR"] is not None else None,
                      "MDD%": round(s_co["MDD"] * 100, 1) if s_co["MDD"] is not None else None,
                      "yearly": y_co},
        "disclosures": [
            "상폐종목 상폐직전가로 청산(부도성=급락가 반영). 정리매매 -100% 아닌 마지막가 → 약간 낙관.",
            "현 유니버스 상장일=KRX-DESC. 상폐=KRX-DELISTING(보통주·영업회사 277).",
            "여전히 근사: 액면분할·무상증자 주식수 고정, EV≈시총+부채, F-Score 7/9.",
        ],
    }
    REPORTS.joinpath("value_screener_backtest_sv.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- stdout ----------
    print("\n" + "=" * 74)
    print("생존편향 보정 백테스트 — 상폐종목 포함 vs 미포함")
    print("=" * 74)
    print(f"현 유니버스 {len(cur_codes)} + 상폐 {len(delisted_meta)} | {REBAL_YEARS[0]}~{REBAL_YEARS[-1]} top-{TOP_N}\n")
    print(f"  {'연도':>6}{'보정전%':>9}{'보정후%':>9}{'상폐픽':>7}{'KOSPI%':>9}")
    for a, b in zip(y_un, y_co):
        up = f"{a['port%']:.1f}" if a["port%"] is not None else "n/a"
        cp = f"{b['port%']:.1f}" if b["port%"] is not None else "n/a"
        bk = f"{b['kospi%']:.1f}" if b["kospi%"] is not None else "n/a"
        print(f"  {b['year']:>6}{up:>9}{cp:>9}{b['n_delisted_picks']:>7}{bk:>9}")
    print(f"\n  보정 전 CAGR {result['uncorrected']['CAGR%']}% | MDD {result['uncorrected']['MDD%']}%")
    print(f"  보정 후 CAGR {result['corrected']['CAGR%']}% | MDD {result['corrected']['MDD%']}%")
    diff = (result['uncorrected']['CAGR%'] or 0) - (result['corrected']['CAGR%'] or 0)
    print(f"  → 생존편향 크기 = {diff:.1f}%p (보정 후가 진짜에 가까움)")

    # 상폐 종목이 실제로 포착돼 손실 낸 사례
    del_hits = [(b["year"], p) for b in y_co for p in b["picks"]
                if p["delisted"] and p["ret%"] is not None]
    if del_hits:
        print(f"\n■ 백테스트가 포착한 상폐 종목 {len(del_hits)}건 (생존편향이 숨겼던 손실)")
        for yr, p in sorted(del_hits, key=lambda x: x[1]["ret%"])[:10]:
            print(f"  {yr} {p['name'][:16]:<16} {p['ret%']:+.1f}%")

    print("\n■ 한계·정직")
    for d in result["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/value_screener_backtest_sv.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True)
        traceback.print_exc()
        sys.exit(1)
