"""공정 head-to-head — F-Score 대형 vs 배당B 대형. 동일 엄격 틀.

둘 다: 생존편향 보정(상폐 포함) + 대형(시총≥5천억) + top20 + 연5/1 리밸 + 월단위 평가
       + 50% 레짐 오버레이(KOSPI<200MA). 유일 차이 = 종목 선정 방식.
  F-Score : 4게이트 fscore≥5 → 저PBR 순 top20 (처음 백테스트 방식)
  배당B    : 배당·퀄리티 게이트 → B랭킹 top20 (최종 전략)

실행: python scripts/_fscore_vs_dividend.py   (리서치)
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
import _value_screener_backtest as bt
import _survivorship as sv
import _value_screener_backtest_sv as svbt
import _dividend_value as dvm
import _final_strategy_report as fsr

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
START, END = pd.Timestamp("2019-05-01"), pd.Timestamp("2026-05-01")
TOP_N = 20
bt.MARCAP_FLOOR_BT = 5000e8


def log(m): print(f"[h2h] {m}", flush=True)


def fscore_basket(codes, fins, shares_map, name_map, prices, buy, fy1, fy2, sector_map, listed_map, dm):
    """F-Score≥5 → 저PBR top20 (대형·생존편향 멤버십)."""
    pool = [c for c in codes if svbt.member_at(c, buy, listed_map, dm)]
    df = bt.build_year_rows(pool, fins, shares_map, name_map, prices, buy, fy1, fy2, sector_map)
    if df.empty:
        return []
    d = df[df["fscore"] >= 5].copy()
    d = d[d["PBR"].notna() & (d["PBR"] > 0)].sort_values("PBR")
    return d.head(TOP_N)["code"].tolist()


def series_for(baskets, prices, ks, ks_ma, dm):
    months = pd.date_range(START, END, freq="MS")
    base, regime = [], []
    for i in range(len(months)-1):
        m, nm = months[i], months[i+1]
        by = m.year if m.month >= 5 else m.year-1
        rets = [fsr.monthly_fwd(prices, c, m, nm, dm) for c in baskets.get(by, [])]
        rets = [r for r in rets if r is not None]
        base.append(float(np.mean(rets)) if rets else 0.0)
        k = ks[ks.index <= m]; km = ks_ma[ks_ma.index <= m].dropna()
        regime.append(bool(len(k) and len(km) and k.iloc[-1] > km.iloc[-1]))
    mo = list(months[:-1])
    final = [v if on else 0.5*v for v, on in zip(base, regime)]
    return mo, final


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    cur = list(listing.index)
    dm = sv.load_delisted_meta()
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in cur}
    name_map = {c: str(listing.loc[c, "Name"]) for c in cur}
    for c, v in dm.items():
        if v.get("shares"): shares_map[c] = v["shares"]; name_map[c] = v["name"]
    sector_map = vs.get_sector_map()
    listed_map = svbt.load_current_listing_dates()
    fins = vb.fetch_all_financials(corp_map, cur)
    dfins, dprices = fsr._cache_delisted(corp_map, dm)
    fins.update(dfins)
    prices = vb.load_all_prices(cur); prices.update(dprices)
    ksc = vb.FDR_CACHE / "KS11.csv"
    ks = pd.read_csv(ksc, index_col=0, parse_dates=True)["Close"]; ks_ma = ks.rolling(200).mean()
    all_codes = list(shares_map)

    log("F-Score 대형 바스켓...")
    fs_bask = {y: fscore_basket(all_codes, fins, shares_map, name_map, prices,
               pd.Timestamp(f"{y}-05-01"), y-1, y-2, sector_map, listed_map, dm) for y in range(2019, 2026)}
    log("배당B 대형 바스켓...")
    dv_bask = {y: [r["code"] for r in fsr.build_div_basket(all_codes, fins, corp_map, shares_map,
               name_map, prices, pd.Timestamp(f"{y}-05-01"), y-1, listed_map, dm)] for y in range(2019, 2026)}

    mo, fs = series_for(fs_bask, prices, ks, ks_ma, dm)
    _, dv = series_for(dv_bask, prices, ks, ks_ma, dm)

    def summ(nm, s):
        c, mdd, f, sh = fsr.stats(s); yv = fsr.yearly(mo, s)
        return {"name": nm, "CAGR%": round(c*100,1), "MDD%": round(mdd*100,1),
                "Sharpe": round(sh,2), "neg": sum(1 for r in yv.values() if r<0),
                "yearly": {y: round(r*100,1) for y,r in yv.items()}}
    A, B = summ("F-Score 대형", fs), summ("배당B 대형(최종)", dv)
    json.dump({"fscore": A, "dividendB": B}, open(REPORTS/"fscore_vs_dividend.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)

    print("\n" + "="*62)
    print("공정 head-to-head — F-Score 대형 vs 배당B 대형 (최종)")
    print("="*62)
    print("(둘다 생존편향보정·대형·top20·50%레짐·월단위 — 선정방식만 차이)\n")
    yrs = sorted(A["yearly"])
    print(f"  {'연도':>6}{'F-Score%':>11}{'배당B%':>10}")
    for y in yrs:
        print(f"  {y:>6}{A['yearly'][y]:>11.1f}{B['yearly'][y]:>10.1f}")
    print()
    for s in (A, B):
        print(f"  {s['name']:<16} CAGR {s['CAGR%']:>5}% | MDD {s['MDD%']:>6}% | Sharpe {s['Sharpe']} | 마이너스 {s['neg']}/7")
    print(f"\n결과 저장: reports/fscore_vs_dividend.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
