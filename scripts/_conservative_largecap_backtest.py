"""배당·금융 저평가 전략(A안) 백테스트 — 틸트 A(딥밸류) vs B(퀄리티·배당) 비교.

사용자 결정용: 초저PBR 마이크로캡 위주(A)가 나은지, 배당·ROE·대형 우량 위주(B)가 나은지
동일 조건에서 백테스트로 비교. 생존편향 보정(상폐 포함) + 금융주 포함 유니버스.

두 틸트 (게이트는 동일, 랭킹 가중만 다름):
  A 딥밸류   : 0.55·저PBR + 0.20·저PER + 0.15·배당수익률 + 0.10·ROE  → 가장 싼 것 우선
  B 퀄리티배당: 0.20·저PBR + 0.35·배당수익률 + 0.30·ROE + 0.15·저PER → 우량·고배당 우선

게이트: 3년흑자 + 배당실시(DPS>0) + 배당수익률≥2% + PBR≤1.5 + ROE≥5% + 배당성향≤80%.
데이터: DART fnlttSinglAcnt(순익·자본) + alotMatter(배당) + FDR 주가. 상폐 포함(svbt).
exit: 고정 1년 (틸트 비교엔 명확). look-ahead 금지: FY(Y-1) 재무·배당은 5월 공시완료.

실행: python scripts/_dividend_value_backtest.py   (리서치)
결과: stdout + reports/conservative_largecap_backtest.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _value_backtest as vb
import _value_screener as vs
import _survivorship as sv
import _value_screener_backtest_sv as svbt
import _dividend_value as dvm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REBAL_YEARS = list(range(2019, 2026))
TOP_N = 10
MARCAP_FLOOR = 5000e8

GATE = {"min_div_yield": 0.02, "max_pbr": 1.5, "min_roe": 0.05, "max_payout": 80.0}


def log(msg):
    print(f"[cons-bt] {msg}", flush=True)


def build_rows(pool, fins, corp_map, shares_map, name_map, prices, buy, fy1, sector_map):
    """point-in-time 배당·밸류 팩터 (FY1=Y-1 재무·배당, buy=매수일 주가)."""
    rows = []
    for code in pool:
        fy = fins.get(code, {})
        f1, f2, f3 = fy.get(fy1), fy.get(fy1 - 1), fy.get(fy1 - 2)
        corp = corp_map.get(code)
        if not f1 or not corp:
            continue
        net, eq, liab = f1.get("net"), f1.get("equity"), f1.get("liab")
        if net is None or eq is None or eq <= 0 or net <= 0:
            continue
        profit_3y = all(x and x.get("net") is not None and x["net"] > 0 for x in (f1, f2, f3))
        if not profit_3y:
            continue
        s = prices.get(code)
        if s is None:
            continue
        p = vb.price_asof(s, buy)
        if p is None or p <= 0:
            continue
        mktcap = p * shares_map.get(code, 0)
        if mktcap < MARCAP_FLOOR:
            continue
        dv = dvm.fetch_dividend(corp, fy1)
        dps = dv.get("dps") if dv else None
        payout = dv.get("payout") if dv else None
        if not dps or dps <= 0:
            continue
        div_yield = dps / p
        if div_yield > 0.20:   # 파싱 글리치
            continue
        pbr = mktcap / eq
        per = mktcap / net
        roe = net / eq
        sm = sector_map.get(code, {})
        rows.append({"code": code, "name": name_map.get(code, code), "mktcap": mktcap,
                     "sector": sm.get("sector", "기타"), "PBR": pbr, "PER": per, "ROE": roe,
                     "div_yield": div_yield, "payout": payout})
    return pd.DataFrame(rows)


def gated(df):
    if df.empty:
        return df
    m = ((df["div_yield"] >= GATE["min_div_yield"]) & (df["PBR"] <= GATE["max_pbr"]) &
         (df["ROE"] >= GATE["min_roe"]) &
         ((df["payout"].isna()) | (df["payout"] <= GATE["max_payout"])))
    return df[m]


def rank(df, tilt):
    d = df.copy()
    def hi(c): return d[c].rank(pct=True)
    def lo(c): return 1 - d[c].rank(pct=True)
    if tilt == "A":   # 딥밸류
        d["score"] = 0.55 * lo("PBR") + 0.20 * lo("PER") + 0.15 * hi("div_yield") + 0.10 * hi("ROE")
    else:             # B 퀄리티·배당
        d["score"] = 0.20 * lo("PBR") + 0.35 * hi("div_yield") + 0.30 * hi("ROE") + 0.15 * lo("PER")
    return d.sort_values("score", ascending=False)


def run(all_codes, fins, corp_map, prices, shares_map, name_map, sector_map, ks,
        listed_map, delisted_meta, tilt):
    yearly, all_rets, sample = [], [], {}
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01")
        sell = pd.Timestamp(f"{y + 1}-05-01")
        pool = [c for c in all_codes if svbt.member_at(c, buy, listed_map, delisted_meta)]
        df = build_rows(pool, fins, corp_map, shares_map, name_map, prices, buy, y - 1, sector_map)
        top = rank(gated(df), tilt).head(TOP_N)
        rets = []
        for _, r in top.iterrows():
            fr = svbt.fwd_return_sv(prices, r["code"], buy, sell, delisted_meta)
            if fr is not None:
                rets.append(fr); all_rets.append(fr * 100)
        if y == 2024:
            sample = [{"name": r["name"], "sector": r["sector"], "PBR": round(r["PBR"], 2),
                       "배당%": round(r["div_yield"] * 100, 1),
                       "mktcap억": round(r["mktcap"] / 1e8)} for _, r in top.iterrows()]
        pr = float(np.mean(rets)) if rets else None
        br = vb.kospi_year_return(ks, buy, sell)
        w = sum(1 for r in rets if r > 0)
        yearly.append({"year": y, "n": len(rets),
                       "port%": round(pr * 100, 1) if pr is not None else None,
                       "kospi%": round(br * 100, 1) if br is not None else None,
                       "win": f"{w}/{len(rets)}"})
    yr = [r["port%"] / 100 for r in yearly if r["port%"] is not None]
    stats = vb.equity_curve_stats(yr)
    wins = sum(1 for r in all_rets if r > 0)
    return {"yearly": yearly, "stats": stats,
            "CAGR%": round(stats["CAGR"] * 100, 1) if stats["CAGR"] is not None else None,
            "MDD%": round(stats["MDD"] * 100, 1) if stats["MDD"] is not None else None,
            "hit": f"{wins}/{len(all_rets)}",
            "avg%": round(float(np.mean(all_rets)), 1) if all_rets else None,
            "max%": round(max(all_rets), 1) if all_rets else None,
            "sample2024": sample}


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    cur = list(listing.index)
    delisted_meta = sv.load_delisted_meta()
    sector_map = vs.get_sector_map()
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in cur}
    name_map = {c: str(listing.loc[c, "Name"]) for c in cur}
    for c, v in delisted_meta.items():
        if v.get("shares"):
            shares_map[c] = v["shares"]; name_map[c] = v["name"]
    listed_map = svbt.load_current_listing_dates()
    log(f"유니버스 현 {len(cur)} + 상폐 {len(delisted_meta)} (금융 포함)")

    fins = vb.fetch_all_financials(corp_map, cur)
    prices = vb.load_all_prices(cur)
    dfins, dprices = _load_delisted(corp_map, delisted_meta)  # 상폐 재무·주가(캐시)
    fins.update(dfins); prices.update(dprices)

    import FinanceDataReader as fdr
    ks_cache = vb.FDR_CACHE / "KS11.csv"
    ks = (pd.read_csv(ks_cache, index_col=0, parse_dates=True)["Close"] if ks_cache.exists()
          else fdr.DataReader("KS11", "2015-01-01", "2026-12-31")["Close"].dropna())

    all_codes = list(shares_map)
    log("배당 데이터 페치하며 틸트 A(딥밸류) 백테스트...")
    A = run(all_codes, fins, corp_map, prices, shares_map, name_map, sector_map, ks,
            listed_map, delisted_meta, "A")
    log("틸트 B(퀄리티·배당) 백테스트...")
    B = run(all_codes, fins, corp_map, prices, shares_map, name_map, sector_map, ks,
            listed_map, delisted_meta, "B")

    result = {"generated_at": pd.Timestamp.now().isoformat(), "top_n": TOP_N,
              "A_deepvalue": {k: A[k] for k in ("CAGR%", "MDD%", "hit", "avg%", "max%")},
              "B_quality": {k: B[k] for k in ("CAGR%", "MDD%", "hit", "avg%", "max%")},
              "A_yearly": A["yearly"], "B_yearly": B["yearly"],
              "A_2024picks": A["sample2024"], "B_2024picks": B["sample2024"],
              "disclosures": ["생존편향 보정(상폐 포함). 금융주 포함. 고정 1년 보유.",
                              "배당수익률>20% 글리치 배제. 배당성향 결측 허용."]}
    REPORTS.joinpath("dividend_value_backtest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print("배당·금융 저평가 백테스트 — 틸트 A(딥밸류) vs B(퀄리티·배당)")
    print("=" * 74)
    print("(생존편향 보정·금융 포함·고정 1년·2019~2025)\n")
    print(f"  {'연도':>6}{'A딥밸류%':>10}{'A승':>7}{'B퀄리티%':>10}{'B승':>7}{'KOSPI%':>9}")
    for a, b in zip(A["yearly"], B["yearly"]):
        ap = f"{a['port%']:.1f}" if a["port%"] is not None else "n/a"
        bp = f"{b['port%']:.1f}" if b["port%"] is not None else "n/a"
        bk = f"{b['kospi%']:.1f}" if b["kospi%"] is not None else "n/a"
        print(f"  {b['year']:>6}{ap:>10}{a['win']:>7}{bp:>10}{b['win']:>7}{bk:>9}")
    print(f"\n  A 딥밸류  : CAGR {A['CAGR%']}% | MDD {A['MDD%']}% | 승률 {A['hit']} | 평균 {A['avg%']}% | 최대 {A['max%']}%")
    print(f"  B 퀄리티배당: CAGR {B['CAGR%']}% | MDD {B['MDD%']}% | 승률 {B['hit']} | 평균 {B['avg%']}% | 최대 {B['max%']}%")
    for tag, pk in (("A 딥밸류", A["sample2024"]), ("B 퀄리티배당", B["sample2024"])):
        print(f"\n■ {tag} — 2024 진입 종목 (시총억·PBR·배당%)")
        for p in pk[:8]:
            print(f"  {p['name'][:14]:<14} {str(p['sector'])[:8]:<9} {p['mktcap억']:>7}억  PBR {p['PBR']:.2f}  배당 {p['배당%']}%")
    print(f"\n결과 저장: reports/conservative_largecap_backtest.json")


def _load_delisted(corp_map, delisted_meta):
    fins, prices = {}, {}
    for code in delisted_meta:
        corp = corp_map.get(code)
        if corp:
            per = {}
            for y in vb.FIN_YEARS:
                c = vb.FIN_CACHE / f"{corp}_{y}.json"
                if c.exists():
                    parsed = vb.parse_fin(json.loads(c.read_text(encoding="utf-8")))
                    if parsed:
                        per[y] = parsed
            if per:
                fins[code] = per
        s = vb.load_price(code)
        if s is not None and len(s):
            prices[code] = s
    return fins, prices


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True)
        traceback.print_exc()
        sys.exit(1)
