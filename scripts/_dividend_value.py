"""배당·금융 저평가 스크리너 (A안, 새 전략) — 영상 투자자 3축 점수표 방식.

BASE 스크리너(_value_screener)가 '매출액 없음=금융업 제외'로 은행·보험·금융지주를
통째로 버리는 걸 보완. 저PBR·고배당·배당연속 금융/우량주를 별도 유니버스로 커버.

3축 점수 (오일전문가 인터뷰 SskCOZ0yi9g):
  ① 이익 창출력   : ROE, PER (저PER·고ROE = 이익 대비 싸다)
  ② 이익 지속성   : 다년 연속 흑자 + 배당 연속 실시(몇 년 끊김없이 배당)
  ③ 주주환원 의지 : 배당수익률(현재가 기준) + 배당성향 적정(과배당 아님) + 배당 성장

데이터: DART alotMatter(배당·DPS·성향) + fnlttSinglAcnt(순익·자본·부채) + FDR 시총.
  금융주도 자본총계·당기순이익 있어 PBR/PER/ROE 계산 가능(PSR/EV는 매출없어 생략).
  배당수익률 = 최근 DPS / 현재가 (DART 연말기준 아닌 현재가 기준으로 갱신).

Exit(정식화 시): BASE 와 동일한 밸류정상화. 본 스크립트는 현재 스냅샷 스크린.

실행: python scripts/_dividend_value.py   (리서치 산출물)
결과: stdout + reports/dividend_value.json
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _value_backtest as vb
import _value_screener as vs

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
DIV_CACHE = vb.DART_CACHE / "div"
DIV_CACHE.mkdir(parents=True, exist_ok=True)

LATEST_FY = max(vb.FIN_YEARS)          # 2024
DIV_YEARS = list(range(LATEST_FY - 4, LATEST_FY + 1))  # 배당연속성용 5년
TOP_N = 25

GATE = {
    "min_div_years": 3,       # 최근 5년 중 배당 실시 >=3년 (지속성)
    "max_payout": 0.80,       # 배당성향 80% 상한 (과배당·역성장 배제)
    "min_div_yield": 0.02,    # 배당수익률 2% 이상
    "max_pbr": 1.5,           # 저평가
    "min_roe": 0.05,          # 이익 창출력 최소
    "max_debt_ratio": 3.0,    # 금융주 고레버리지 감안 완화(300%)
}


def log(msg):
    print(f"[div] {msg}", flush=True)


def _num(s):
    if s in (None, "-", ""):
        return None
    try:
        return float(str(s).replace(",", ""))
    except ValueError:
        return None


def fetch_dividend(corp, year) -> dict | None:
    """DART alotMatter → {dps, div_yield_dart, payout, cash_div_total}. 캐시."""
    cache = DIV_CACHE / f"{corp}_{year}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        r = requests.get("https://opendart.fss.or.kr/api/alotMatter.json",
                         params={"crtfc_key": vb.KEY, "corp_code": corp,
                                 "bsns_year": str(year), "reprt_code": "11011"}, timeout=20)
        d = r.json()
    except Exception:  # noqa: BLE001
        return None
    out = {"dps": None, "div_yield_dart": None, "payout": None}
    if d.get("status") == "000":
        for it in d.get("list", []):
            se, knd, v = it.get("se", ""), it.get("stock_knd", ""), _num(it.get("thstrm"))
            if se == "주당 현금배당금(원)" and knd == "보통주":
                out["dps"] = v
            elif se == "현금배당수익률(%)" and knd == "보통주":
                out["div_yield_dart"] = v
            elif "현금배당성향" in se:
                out["payout"] = v
    cache.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return out


def div_history(corp) -> dict:
    """5년 배당 이력 → {n_years_paid, dps_latest, payout_latest, dps_growth}."""
    dps_by_year = {}
    payout_latest = None
    for y in DIV_YEARS:
        dv = fetch_dividend(corp, y)
        if not (DIV_CACHE / f"{corp}_{y}.json").exists():
            time.sleep(0.03)
        if dv and dv.get("dps") and dv["dps"] > 0:
            dps_by_year[y] = dv["dps"]
            if y == LATEST_FY:
                payout_latest = dv.get("payout")
    n_paid = len(dps_by_year)
    dps_latest = dps_by_year.get(LATEST_FY)
    # 배당 성장 (5년전 대비)
    growth = None
    old = dps_by_year.get(DIV_YEARS[0])
    if dps_latest and old and old > 0:
        growth = (dps_latest / old) ** (1 / 4) - 1
    return {"n_years_paid": n_paid, "dps_latest": dps_latest,
            "payout_latest": payout_latest, "dps_growth": growth}


def build_rows(codes, fins, corp_map, shares_map, name_map, marcap_map, close_map, sector_map):
    rows = []
    for code in codes:
        f = fins.get(code, {}).get(LATEST_FY)
        f2 = fins.get(code, {}).get(LATEST_FY - 1)
        f3 = fins.get(code, {}).get(LATEST_FY - 2)
        corp = corp_map.get(code)
        if not f or not corp:
            continue
        net, eq, liab = f.get("net"), f.get("equity"), f.get("liab")
        if net is None or eq is None or eq <= 0:
            continue
        # 3년 흑자 (이익 지속성)
        profit_3y = all(x and x.get("net") is not None and x["net"] > 0 for x in (f, f2, f3))
        mktcap = marcap_map.get(code) or (close_map.get(code, 0) * shares_map.get(code, 0))
        if not mktcap:
            continue
        price = mktcap / shares_map[code] if shares_map.get(code) else None
        pbr = mktcap / eq
        per = mktcap / net if net > 0 else None
        roe = net / eq
        debt_ratio = (liab / eq) if liab is not None else None
        dh = div_history(corp)
        # 배당수익률 = 최근 DPS / 현재가 (현재가 기준). 20% 초과는 DPS 파싱 글리치로 간주 배제.
        div_yield = (dh["dps_latest"] / price) if (dh["dps_latest"] and price and price > 0) else None
        if div_yield is not None and div_yield > 0.20:
            div_yield = None
        sm = sector_map.get(code, {})
        rows.append({
            "code": code, "name": name_map.get(code, code), "mktcap": mktcap, "price": price,
            "sector": sm.get("sector", "기타"), "is_holdco": bool(sm.get("is_holdco")),
            "PBR": round(pbr, 2), "PER": round(per, 2) if per else None,
            "ROE": roe, "debt_ratio": debt_ratio, "profit_3y": profit_3y,
            "div_yield": div_yield, "payout": dh["payout_latest"],
            "n_years_paid": dh["n_years_paid"], "dps_growth": dh["dps_growth"],
            "dps": dh["dps_latest"],
        })
    return pd.DataFrame(rows)


def gate_fail(r) -> list[str]:
    f = []
    if not r["profit_3y"]:
        f.append("3년흑자X")
    if not (r["n_years_paid"] >= GATE["min_div_years"]):
        f.append(f"배당<{GATE['min_div_years']}년")
    dy = r["div_yield"]
    if dy is None or dy < GATE["min_div_yield"]:
        f.append("배당수익률<2%")
    p = r["payout"]
    if p is not None and (p / 100 if p > 1 else p) > GATE["max_payout"]:
        f.append("과배당(성향>80%)")
    if r["PBR"] is None or r["PBR"] > GATE["max_pbr"]:
        f.append("PBR>1.5")
    if r["ROE"] is None or r["ROE"] < GATE["min_roe"]:
        f.append("ROE<5%")
    dr = r["debt_ratio"]
    if dr is not None and dr > GATE["max_debt_ratio"]:
        f.append("부채>300%")
    return f


def score(df):
    d = df.copy()
    def hi(c): return d[c].rank(pct=True)
    def lo(c): return 1 - d[c].rank(pct=True)
    # ① 이익창출력 ② 지속성(배당연속·성장) ③ 주주환원(배당수익률)
    earn = (hi("ROE") + lo("PER")) / 2
    persist = (d["n_years_paid"] / len(DIV_YEARS)).clip(0, 1) * 0.5 + hi("dps_growth").fillna(0.3) * 0.5
    yield_s = hi("div_yield")
    value = lo("PBR")
    d["score"] = 0.30 * value + 0.25 * earn + 0.20 * persist + 0.25 * yield_s
    return d.sort_values("score", ascending=False)


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    codes = list(listing.index)
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    marcap_map = {c: float(listing.loc[c, "Marcap"]) for c in codes}
    close_map = {c: float(listing.loc[c, "Close"]) for c in codes}
    sector_map = vs.get_sector_map()
    log(f"유니버스 {len(codes)} (금융 포함)")

    fins = vb.fetch_all_financials(corp_map, codes)
    # 흑자 종목만 배당 페치 (호출 절약)
    profitable = [c for c in codes if fins.get(c, {}).get(LATEST_FY, {}).get("net", 0) and
                  fins[c][LATEST_FY]["net"] > 0 and fins[c][LATEST_FY].get("equity", 0) > 0]
    log(f"흑자+자본>0 {len(profitable)} → 배당 이력 페치")
    df = build_rows(profitable, fins, corp_map, shares_map, name_map,
                    marcap_map, close_map, sector_map)
    log(f"배당·재무 완비 {len(df)}")

    passed = df[df.apply(lambda r: len(gate_fail(r)) == 0, axis=1)]
    ranked = score(passed).head(TOP_N)
    fin_ct = int((ranked["sector"] == "금융지주").sum())

    picks = []
    for _, r in ranked.iterrows():
        picks.append({
            "code": r["code"], "name": r["name"], "sector": r["sector"],
            "지주사": bool(r["is_holdco"]),
            "PBR": r["PBR"], "PER": r["PER"], "ROE%": round(r["ROE"] * 100, 1),
            "배당수익률%": round(r["div_yield"] * 100, 2) if pd.notna(r["div_yield"]) else None,
            "배당성향%": round(r["payout"], 0) if pd.notna(r["payout"]) else None,
            "배당연속": int(r["n_years_paid"]),
            "배당성장%": round(r["dps_growth"] * 100, 1) if pd.notna(r["dps_growth"]) else None,
            "score": round(r["score"], 3),
        })
    result = {"generated_at": pd.Timestamp.now().isoformat(),
              "strategy": "dividend_value (A안)", "gates": GATE,
              "universe": len(codes), "profitable": len(profitable),
              "gated": len(passed), "financial_in_top": fin_ct, "picks": picks,
              "disclosures": [
                  "금융주 포함(BASE 제외분 보완). 금융은 PSR/EV 생략, PBR/PER/ROE·배당만.",
                  "배당수익률=최근DPS/현재가. 배당성향>80% 과배당 배제. 3년흑자+5년중 배당3년+.",
                  "생존편향 미제거(현 상장). Exit는 정식화 시 밸류정상화.",
              ]}
    REPORTS.joinpath("dividend_value.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 82)
    print("배당·금융 저평가 스크리너 (A안) — 저PBR × 고배당 × 배당연속 × 흑자")
    print("=" * 82)
    print(f"유니버스 {len(codes)} | 흑자 {len(profitable)} | 게이트통과 {len(passed)} | top{TOP_N} 중 금융 {fin_ct}\n")
    print(f"  {'종목':<14}{'섹터':<9}{'PBR':>6}{'PER':>6}{'ROE%':>6}{'배당%':>7}{'성향%':>6}{'연속':>4}{'성장%':>7}{'score':>7}")
    for p in picks:
        print(f"  {p['name'][:14]:<14}{str(p['sector'])[:8]:<9}{p['PBR']:>6.2f}"
              f"{(p['PER'] or 0):>6.1f}{p['ROE%']:>6.1f}{(p['배당수익률%'] or 0):>7.2f}"
              f"{str(p['배당성향%'] or '-'):>6}{p['배당연속']:>4}{str(p['배당성장%'] or '-'):>7}{p['score']:>7.3f}")
    print("\n■ 한계·정직")
    for d in result["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/dividend_value.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True)
        traceback.print_exc()
        sys.exit(1)
