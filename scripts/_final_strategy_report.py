"""최종 전략 종합 리포트 — 대형 배당·가치(B) + 생존편향 보정 + 50% 레짐 오버레이.

지금까지 검증한 최선 조합을 하나의 일관된 백테스트로:
  · 유니버스: KOSPI+KOSDAQ 대형(시총≥5천억), 금융 포함, 생존편향 보정(상폐 포함)
  · 선정: 3년흑자+배당실시+배당수익률≥2%+PBR≤1.5+ROE≥5% 게이트 → B 퀄리티·배당 랭킹 top-20
  · 청산: 연 5/1 리밸(1년 보유), 월단위 시가평가
  · 레짐 오버레이: 월초 KOSPI < 200일선이면 그 달 50% 현금(약세장 방어)

산출: 연도별 수익률 · CAGR · MDD · Sharpe · 마이너스연도 · 현재(2026-05-01) 보유 종목.
실행: python scripts/_final_strategy_report.py   (리서치)
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
import _survivorship as sv
import _value_screener_backtest_sv as svbt
import _dividend_value as dvm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
START, END = pd.Timestamp("2019-05-01"), pd.Timestamp("2026-05-01")
TOP_N = 20
MARCAP_FLOOR = 5000e8
GATE = dvm.GATE
REGIME_OFF_EXPOSURE = 0.5   # 200일선 아래면 50% 현금


def log(m): print(f"[final] {m}", flush=True)


def build_div_basket(codes, fins, corp_map, shares_map, name_map, prices, buy, fy1, listed_map, dm):
    """대형 배당·B 랭킹 top-N (point-in-time). 반환: [(code,name,pbr,roe,div_yield)]."""
    rows = []
    for code in codes:
        if not svbt.member_at(code, buy, listed_map, dm):
            continue
        fy = fins.get(code, {})
        f1, f2, f3 = fy.get(fy1), fy.get(fy1-1), fy.get(fy1-2)
        corp = corp_map.get(code)
        if not f1 or not corp:
            continue
        net, eq, liab = f1.get("net"), f1.get("equity"), f1.get("liab")
        if net is None or eq is None or eq <= 0 or net <= 0:
            continue
        if not all(x and x.get("net") is not None and x["net"] > 0 for x in (f1, f2, f3)):
            continue
        s = prices.get(code)
        if s is None:
            continue
        p = vb.price_asof(s, buy)
        if not p or p <= 0:
            continue
        mktcap = p * shares_map.get(code, 0)
        if mktcap < MARCAP_FLOOR:
            continue
        dv = dvm.fetch_dividend(corp, fy1)
        dps = dv.get("dps") if dv else None
        payout = dv.get("payout") if dv else None
        if not dps or dps <= 0:
            continue
        dy = dps / p
        if dy > 0.20 or dy < GATE["min_div_yield"]:
            continue
        pbr, per, roe = mktcap/eq, mktcap/net, net/eq
        debt = (liab/eq) if liab is not None else None
        if pbr > GATE["max_pbr"] or roe < GATE["min_roe"]:
            continue
        if payout is not None and (payout/100 if payout > 1 else payout) > GATE["max_payout"]:
            continue
        if debt is not None and debt > GATE["max_debt_ratio"]:
            continue
        rows.append({"code": code, "name": name_map.get(code, code), "PBR": pbr,
                     "PER": per, "ROE": roe, "dy": dy, "sector": (vs.get_sector_map().get(code, {}) or {}).get("sector")})
    if not rows:
        return []
    df = pd.DataFrame(rows)
    def hi(c): return df[c].rank(pct=True)
    def lo(c): return 1 - df[c].rank(pct=True)
    df["score"] = 0.20*lo("PBR") + 0.35*hi("dy") + 0.30*hi("ROE") + 0.15*lo("PER")
    return df.sort_values("score", ascending=False).head(TOP_N).to_dict("records")


def monthly_fwd(prices, code, m, nm, dm):
    s = prices.get(code)
    if s is None: return None
    eff = nm
    d = dm.get(code)
    if d:
        dd = pd.to_datetime(d["delisting_date"], errors="coerce")
        if pd.notna(dd) and m < dd <= nm: eff = dd
    p0, p1 = vb.price_asof(s, m), vb.price_asof(s, eff)
    if p0 is None or p1 is None or p0 <= 0: return None
    return p1/p0 - 1.0


def stats(series):
    eq = np.cumprod([1+r for r in series]); n = len(series)/12
    cagr = eq[-1]**(1/n)-1
    peak = np.maximum.accumulate(np.concatenate([[1], eq])); dd = np.concatenate([[1], eq])/peak-1
    s = pd.Series(series); sh = float(s.mean()/s.std()*np.sqrt(12)) if s.std() > 0 else None
    return cagr, float(dd.min()), float(eq[-1]), sh


def yearly(months, series):
    by = {}
    for m, r in zip(months, series):
        y = m.year if m.month >= 5 else m.year-1
        by.setdefault(y, []).append(r)
    return {y: float(np.prod([1+x for x in v])-1) for y, v in sorted(by.items())}


def _cache_delisted(corp_map, dm):
    fins, prices = {}, {}
    for code in dm:
        corp = corp_map.get(code)
        if corp:
            per = {}
            for y in vb.FIN_YEARS:
                c = vb.FIN_CACHE / f"{corp}_{y}.json"
                if c.exists():
                    p = vb.parse_fin(json.loads(c.read_text(encoding="utf-8")))
                    if p: per[y] = p
            if per: fins[code] = per
        s = vb.load_price(code)
        if s is not None and len(s): prices[code] = s
    return fins, prices


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    cur = list(listing.index)
    dm = sv.load_delisted_meta()
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in cur}
    name_map = {c: str(listing.loc[c, "Name"]) for c in cur}
    for c, v in dm.items():
        if v.get("shares"): shares_map[c] = v["shares"]; name_map[c] = v["name"]
    listed_map = svbt.load_current_listing_dates()
    fins = vb.fetch_all_financials(corp_map, cur)
    # FY2025 캐시 로드(현재 픽용)
    for c in cur:
        corp = corp_map.get(c)
        if corp and (vb.FIN_CACHE / f"{corp}_2025.json").exists():
            p = vb.parse_fin(json.loads((vb.FIN_CACHE / f"{corp}_2025.json").read_text(encoding="utf-8")))
            if p: fins.setdefault(c, {})[2025] = p
    dfins, dprices = _cache_delisted(corp_map, dm)
    fins.update(dfins)
    prices = vb.load_all_prices(cur); prices.update(dprices)
    ksc = vb.FDR_CACHE / "KS11.csv"
    ks = pd.read_csv(ksc, index_col=0, parse_dates=True)["Close"]
    ks_ma = ks.rolling(200).mean()
    all_codes = list(shares_map)
    log("대형 배당·B 바스켓(연 5/1)...")

    baskets = {y: [r["code"] for r in build_div_basket(all_codes, fins, corp_map, shares_map,
               name_map, prices, pd.Timestamp(f"{y}-05-01"), y-1, listed_map, dm)] for y in range(2019, 2026)}

    months = pd.date_range(START, END, freq="MS")
    base, regime = [], []
    for i in range(len(months)-1):
        m, nm = months[i], months[i+1]
        by = m.year if m.month >= 5 else m.year-1
        rets = [monthly_fwd(prices, c, m, nm, dm) for c in baskets.get(by, [])]
        rets = [r for r in rets if r is not None]
        base.append(float(np.mean(rets)) if rets else 0.0)
        k = ks[ks.index <= m]; km = ks_ma[ks_ma.index <= m].dropna()
        regime.append(bool(len(k) and len(km) and k.iloc[-1] > km.iloc[-1]))
    mo = list(months[:-1])
    final = [v if on else REGIME_OFF_EXPOSURE*v for v, on in zip(base, regime)]

    c0, m0, f0, s0 = stats(base)
    c1, m1, f1, s1 = stats(final)
    yb, yf = yearly(mo, base), yearly(mo, final)
    off = sum(1 for on in regime if not on)

    # 현재 보유 종목 (2026-05-01, FY2025)
    cur_picks = build_div_basket(all_codes, fins, corp_map, shares_map, name_map, prices,
                                 pd.Timestamp("2026-05-01"), 2025, listed_map, dm)
    now_on = bool(ks.iloc[-1] > ks_ma.dropna().iloc[-1])

    result = {"generated_at": pd.Timestamp.now().isoformat(),
              "config": {"universe": "KOSPI+KOSDAQ 대형(시총≥5천억)·금융포함·생존편향보정",
                         "selection": "3년흑자+배당+저PBR+ROE 게이트 → B 퀄리티·배당 top20",
                         "exit": "연 5/1 리밸·1년보유·월평가",
                         "regime": "월초 KOSPI<200MA면 50% 현금"},
              "base": {"CAGR%": round(c0*100,1), "MDD%": round(m0*100,1), "Sharpe": round(s0,2), "neg": sum(1 for r in yb.values() if r<0)},
              "final": {"CAGR%": round(c1*100,1), "MDD%": round(m1*100,1), "Sharpe": round(s1,2), "neg": sum(1 for r in yf.values() if r<0)},
              "yearly": {y: {"base%": round(yb[y]*100,1), "final%": round(yf.get(y,0)*100,1)} for y in sorted(yb)},
              "off_months": off, "total_months": len(mo),
              "regime_now": "risk-on(전액투자)" if now_on else "risk-off(50%현금)",
              "current_picks": [{"name": r["name"], "sector": r.get("sector"),
                                 "PBR": round(r["PBR"],2), "ROE%": round(r["ROE"]*100,1),
                                 "배당%": round(r["dy"]*100,2)} for r in cur_picks]}
    REPORTS.joinpath("final_strategy_report.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 70)
    print("최종 전략 — 대형 배당·가치(B) + 생존편향보정 + 50% 레짐 오버레이")
    print("=" * 70)
    print("유니버스: KOSPI+KOSDAQ 대형(시총≥5천억)·금융포함·상폐보정")
    print(f"선정: 배당·퀄리티 게이트 → B랭킹 top{TOP_N} | 리밸 연1회 | 레짐: KOSPI<200MA면 50%현금\n")
    print(f"  {'연도':>6}{'레짐無%':>10}{'최종(레짐)%':>13}")
    for y in sorted(yb):
        print(f"  {y:>6}{yb[y]*100:>10.1f}{yf.get(y,0)*100:>13.1f}")
    print()
    print(f"  레짐 無  : CAGR {result['base']['CAGR%']}% | MDD {result['base']['MDD%']}% | Sharpe {result['base']['Sharpe']} | 마이너스 {result['base']['neg']}/7")
    print(f"  최종(레짐): CAGR {result['final']['CAGR%']}% | MDD {result['final']['MDD%']}% | Sharpe {result['final']['Sharpe']} | 마이너스 {result['final']['neg']}/7")
    print(f"  (risk-off {off}/{len(mo)}개월, 현재 레짐: {result['regime_now']})")
    print(f"\n■ 현재 보유 종목 (2026-05-01 기준, FY2025) — {len(cur_picks)}개")
    print(f"  {'종목':<14}{'섹터':<9}{'PBR':>6}{'ROE%':>6}{'배당%':>7}")
    for r in result["current_picks"]:
        print(f"  {r['name'][:14]:<14}{str(r['sector'])[:8]:<9}{r['PBR']:>6.2f}{r['ROE%']:>6.1f}{r['배당%']:>7.2f}")
    print(f"\n결과 저장: reports/final_strategy_report.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
