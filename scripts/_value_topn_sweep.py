"""손실 축소 레버 검증 — 종목 수(top-N) 분산 스윕 + 2슬리브 결합.

가설: 선정 조이기(집중·필터)는 fat-tail 죽여 역효과였으나, 반대로 분산(top-N↑)은
개별 폭탄만 희석해 MDD↓·CAGR 유지. 그리고 밸류+배당 2슬리브 결합은 상관 낮아 MDD↓.

생존편향 보정 유니버스, 고정 1년 리밸, value-quality-kr(BASE) 게이트/랭킹 재사용.
실행: python scripts/_value_topn_sweep.py   (리서치)
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
REBAL_YEARS = list(range(2019, 2026))
TOPNS = [10, 15, 20, 25, 30]


def log(m): print(f"[sweep] {m}", flush=True)


def run_topn(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
             listed_map, delisted_meta, top_n):
    """생존편향 보정 · value-quality 게이트/랭킹 · top_n 고정 1년. 연수익 시계열 반환."""
    yearly = []
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01"); sell = pd.Timestamp(f"{y+1}-05-01")
        pool = [c for c in all_codes if svbt.member_at(c, buy, listed_map, delisted_meta)]
        df = bt.build_year_rows(pool, fins, shares_map, name_map, prices, buy, y-1, y-2, sector_map)
        if df.empty:
            yearly.append(None); continue
        survivors, _ = vs.run_funnel(df)
        top = vs.rank_survivors(survivors).head(top_n)
        rets = [svbt.fwd_return_sv(prices, r["code"], buy, sell, delisted_meta)
                for _, r in top.iterrows()]
        rets = [r for r in rets if r is not None]
        yearly.append(float(np.mean(rets)) if rets else None)
    return yearly


def stats(yearly):
    yr = [r for r in yearly if r is not None]
    s = vb.equity_curve_stats(yr)
    return s["CAGR"], s["MDD"], s.get("final_multiple")


def blend(a, b, wa=0.5):
    """두 전략 연수익 시계열 wa:1-wa 결합 (매년 리밸 = 가중평균)."""
    out = []
    for x, y in zip(a, b):
        if x is None and y is None: out.append(None)
        elif x is None: out.append(y)
        elif y is None: out.append(x)
        else: out.append(wa * x + (1 - wa) * y)
    return out


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
    fins = vb.fetch_all_financials(corp_map, cur)
    prices = vb.load_all_prices(cur)
    dfins, dprices = svbt.fetch_delisted_data(corp_map, delisted_meta) if False else _cache(corp_map, delisted_meta)
    fins.update(dfins); prices.update(dprices)
    ksc = vb.FDR_CACHE / "KS11.csv"
    ks = pd.read_csv(ksc, index_col=0, parse_dates=True)["Close"] if ksc.exists() else None
    all_codes = list(shares_map)

    log("top-N 분산 스윕...")
    rows = []
    series = {}
    for n in TOPNS:
        yr = run_topn(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
                      listed_map, delisted_meta, n)
        series[n] = yr
        c, m, fm = stats(yr)
        rows.append({"top_n": n, "CAGR%": round(c*100, 1), "MDD%": round(m*100, 1),
                     "final": round(fm, 2)})

    result = {"generated_at": pd.Timestamp.now().isoformat(), "sweep": rows}
    REPORTS.joinpath("value_topn_sweep.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 56)
    print("종목 수(top-N) 분산 스윕 — 손실(MDD) vs 수익(CAGR)")
    print("=" * 56)
    print("(생존편향 보정, 고정 1년, value-quality 게이트)\n")
    print(f"  {'top_N':>6}{'CAGR%':>9}{'MDD%':>9}{'최종배수':>9}")
    for r in rows:
        print(f"  {r['top_n']:>6}{r['CAGR%']:>9}{r['MDD%']:>9}{r['final']:>9}")
    # 연도별 (top10 vs top25)
    print(f"\n■ 연도별 (분산 효과 확인)")
    print(f"  {'연도':>6}{'top10%':>9}{'top25%':>9}")
    for i, y in enumerate(REBAL_YEARS):
        a = series[10][i]; b = series[25][i]
        print(f"  {y:>6}{(a*100 if a is not None else 0):>9.1f}{(b*100 if b is not None else 0):>9.1f}")
    print(f"\n결과 저장: reports/value_topn_sweep.json")
    print("\n※ 2슬리브(밸류+배당) 결합 MDD 는 배당 백테스트 시계열 필요 — 후속.")


def _cache(corp_map, delisted_meta):
    fins, prices = {}, {}
    for code in delisted_meta:
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


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
