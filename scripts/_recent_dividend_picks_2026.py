"""2026-05-01 배당B(dividend-value) 픽 + 근황 — BASE(딥밸류) 전진검증과 비교용.

BASE 딥밸류 픽이 2026-05-01→오늘 -27.7%(0/15) 전멸. 배당B(대형·배당 틸트)가
같은 국면에서 덜 다쳤는지 확인. FY2025 재무(캐시) + FY2025 배당(신규 페치).

실행: python scripts/_recent_dividend_picks_2026.py   (리서치)
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
import _dividend_value as dvm

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
ASOF = pd.Timestamp("2026-05-01")
FY = 2025                      # 2026-05-01 가용 최신 FY
DIV_YEARS = [2021, 2022, 2023, 2024, 2025]
TOP_N = 15
GATE = dvm.GATE


def log(m): print(f"[recdiv] {m}", flush=True)


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    codes = list(listing.index)
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    sector_map = vs.get_sector_map()
    fins = vb.fetch_all_financials(corp_map, codes)
    # FY2025 캐시 로드(_recent_picks_2026 이 이미 페치)
    for c in codes:
        corp = corp_map.get(c)
        if corp:
            cache = vb.FIN_CACHE / f"{corp}_2025.json"
            if cache.exists():
                p = vb.parse_fin(json.loads(cache.read_text(encoding="utf-8")))
                if p: fins.setdefault(c, {})[2025] = p
    prices = vb.load_all_prices(codes)
    log(f"유니버스 {len(codes)} — FY2025 배당 페치 + 스크린")

    rows = []
    n = 0
    for code in codes:
        fy = fins.get(code, {})
        f1, f2, f3 = fy.get(FY), fy.get(FY-1), fy.get(FY-2)
        if not f1:
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
        p0 = vb.price_asof(s, ASOF)
        if not p0 or p0 <= 0:
            continue
        mktcap = p0 * shares_map.get(code, 0)
        if mktcap < 100e8:
            continue
        corp = corp_map.get(code)
        # 배당 이력 (FY2025 신규 페치 포함)
        dps_by = {}
        payout = None
        for y in DIV_YEARS:
            cache = vb.DART_CACHE / "div" / f"{corp}_{y}.json"
            existed = cache.exists()
            dv = dvm.fetch_dividend(corp, y)
            if not existed:
                time.sleep(0.03)
            if dv and dv.get("dps") and dv["dps"] > 0:
                dps_by[y] = dv["dps"]
                if y == FY: payout = dv.get("payout")
        n += 1
        if n % 200 == 0: log(f"배당 {n}")
        dps_latest = dps_by.get(FY)
        if not dps_latest:
            continue
        div_yield = dps_latest / p0
        if div_yield > 0.20:
            continue
        pbr, per, roe = mktcap/eq, mktcap/net, net/eq
        debt = (liab/eq) if liab is not None else None
        # 게이트 (dividend-value)
        if len(dps_by) < GATE["min_div_years"]: continue
        if div_yield < GATE["min_div_yield"]: continue
        if payout is not None and (payout/100 if payout > 1 else payout) > GATE["max_payout"]: continue
        if pbr > GATE["max_pbr"]: continue
        if roe < GATE["min_roe"]: continue
        if debt is not None and debt > GATE["max_debt_ratio"]: continue
        sm = sector_map.get(code, {})
        rows.append({"code": code, "name": name_map.get(code, code), "sector": sm.get("sector", "기타"),
                     "PBR": pbr, "PER": per, "ROE": roe, "div_yield": div_yield,
                     "n_paid": len(dps_by), "p0": p0, "s": s})

    df = pd.DataFrame(rows)
    if df.empty:
        print("배당 스크린 통과 0"); return
    # B 퀄리티·배당 랭킹
    def hi(c): return df[c].rank(pct=True)
    def lo(c): return 1 - df[c].rank(pct=True)
    df["score"] = 0.20*lo("PBR") + 0.35*hi("div_yield") + 0.30*hi("ROE") + 0.15*lo("PER")
    top = df.sort_values("score", ascending=False).head(TOP_N)

    picks = []
    for _, r in top.iterrows():
        s = r["s"]; p1 = float(s.iloc[-1]); ret = p1/r["p0"] - 1
        picks.append({"name": r["name"], "sector": r["sector"],
                      "PBR": round(r["PBR"], 2), "ROE%": round(r["ROE"]*100, 1),
                      "배당%": round(r["div_yield"]*100, 2),
                      "매수가": round(r["p0"]), "현재가": round(p1), "수익률%": round(ret*100, 1)})
    rets = [p["수익률%"] for p in picks]
    avg = float(np.mean(rets)); wins = sum(1 for r in rets if r > 0)
    ksc = vb.FDR_CACHE / "KS11.csv"
    ks = pd.read_csv(ksc, index_col=0, parse_dates=True)["Close"] if ksc.exists() else None
    ks_ret = ((float(ks.iloc[-1])/vb.price_asof(ks, ASOF))-1)*100 if ks is not None else None
    lp = top.iloc[0]["s"].index[-1].strftime("%Y-%m-%d")

    result = {"asof": str(ASOF.date()), "current": lp, "avg_return%": round(avg, 1),
              "win": f"{wins}/{len(rets)}", "kospi%": round(ks_ret, 1) if ks_ret else None, "picks": picks}
    REPORTS.joinpath("recent_dividend_picks_2026.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 72)
    print("2026-05-01 배당B 픽 → 근황 (BASE 딥밸류 -27.7% 와 비교)")
    print("=" * 72)
    print(f"(FY2025 기준, 매수 2026-05-01 → 현재 {lp})\n")
    print(f"  {'종목':<14}{'섹터':<9}{'PBR':>6}{'ROE%':>6}{'배당%':>7}{'수익률':>8}")
    for p in picks:
        print(f"  {p['name'][:14]:<14}{str(p['sector'])[:8]:<9}{p['PBR']:>6.2f}"
              f"{p['ROE%']:>6.1f}{p['배당%']:>7.2f}{p['수익률%']:>+7.1f}%")
    print(f"\n  → 배당B 평균 {result['avg_return%']}% | 승 {result['win']} | KOSPI {result['kospi%']}%")
    print(f"     (BASE 딥밸류: -27.7% | 0/15)")
    print(f"\n결과 저장: reports/recent_dividend_picks_2026.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
