"""밸류 × 모멘텀 결합 테스트 — 마이너스 연도 방어 검증.

가설(딥리서치): 밸류와 모멘텀은 음의 상관(-0.60). 가치가 지는 국면(성장주 랠리, 2023)에
모멘텀이 이겨 → 반반 결합 시 마이너스 연도 축소·곡선 평활화. 배당슬리브(상관0.96)와 대조.

동일 조건: 생존편향 보정 유니버스, 5/1 연리밸 1년 보유, top-25.
  밸류    = value-quality 4게이트 + 랭킹 (재무 필요)
  모멘텀  = 12-1개월 모멘텀 = price[buy-21d]/price[buy-252d]-1 (직전 1개월 reversal skip, 주가만)
결합: w·밸류 + (1-w)·모멘텀 (연수익 가중, 매년 리밸 가정).

실행: python scripts/_value_momentum_blend.py   (리서치)
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
TOP_N = 25
MARCAP_FLOOR = 100e8


def log(m): print(f"[vm] {m}", flush=True)


def value_yearly(all_codes, fins, prices, shares_map, name_map, sector_map,
                 listed_map, delisted_meta):
    out = []
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01"); sell = pd.Timestamp(f"{y+1}-05-01")
        pool = [c for c in all_codes if svbt.member_at(c, buy, listed_map, delisted_meta)]
        df = bt.build_year_rows(pool, fins, shares_map, name_map, prices, buy, y-1, y-2, sector_map)
        if df.empty:
            out.append(None); continue
        survivors, _ = vs.run_funnel(df)
        top = vs.rank_survivors(survivors).head(TOP_N)
        rets = [svbt.fwd_return_sv(prices, r["code"], buy, sell, delisted_meta) for _, r in top.iterrows()]
        rets = [r for r in rets if r is not None]
        out.append(float(np.mean(rets)) if rets else None)
    return out


def momentum_yearly(all_codes, prices, shares_map, listed_map, delisted_meta):
    out = []
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01"); sell = pd.Timestamp(f"{y+1}-05-01")
        pool = [c for c in all_codes if svbt.member_at(c, buy, listed_map, delisted_meta)]
        rows = []
        for code in pool:
            s = prices.get(code)
            if s is None:
                continue
            p_now = vb.price_asof(s, buy - pd.Timedelta(days=21))     # 직전 1개월 skip
            p_old = vb.price_asof(s, buy - pd.Timedelta(days=252))    # 12개월 전
            p_buy = vb.price_asof(s, buy)
            if not (p_now and p_old and p_buy) or p_old <= 0 or p_buy <= 0:
                continue
            mktcap = p_buy * shares_map.get(code, 0)
            if mktcap < MARCAP_FLOOR:
                continue
            rows.append({"code": code, "mom": p_now / p_old - 1.0})
        if not rows:
            out.append(None); continue
        d = pd.DataFrame(rows).sort_values("mom", ascending=False)
        d = d[d["mom"] > 0].head(TOP_N)  # 양의 모멘텀만
        rets = [svbt.fwd_return_sv(prices, c, buy, sell, delisted_meta) for c in d["code"]]
        rets = [r for r in rets if r is not None]
        out.append(float(np.mean(rets)) if rets else None)
    return out


def stats(yearly):
    yr = [r for r in yearly if r is not None]
    eq = np.cumprod([1 + r for r in yr]); n = len(yr)
    cagr = eq[-1] ** (1 / n) - 1
    peak = np.maximum.accumulate(np.concatenate([[1], eq])); dd = np.concatenate([[1], eq]) / peak - 1
    return cagr, float(dd.min()), float(eq[-1])


def blend(a, b, w):
    return [None if (x is None and y is None) else
            (y if x is None else x if y is None else w * x + (1 - w) * y)
            for x, y in zip(a, b)]


def _cache(corp_map, dm):
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
    sector_map = vs.get_sector_map()
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in cur}
    name_map = {c: str(listing.loc[c, "Name"]) for c in cur}
    for c, v in dm.items():
        if v.get("shares"):
            shares_map[c] = v["shares"]; name_map[c] = v["name"]
    listed_map = svbt.load_current_listing_dates()
    fins = vb.fetch_all_financials(corp_map, cur)
    prices = vb.load_all_prices(cur)
    dfins, dprices = _cache(corp_map, dm)
    fins.update(dfins); prices.update(dprices)
    all_codes = list(shares_map)

    log("밸류 top25...")
    V = value_yearly(all_codes, fins, prices, shares_map, name_map, sector_map, listed_map, dm)
    log("모멘텀 top25...")
    M = momentum_yearly(all_codes, prices, shares_map, listed_map, dm)

    B50 = blend(V, M, 0.5)
    B70 = blend(V, M, 0.7)   # 밸류 heavy

    va = np.array([V[i] for i in range(len(V)) if V[i] is not None and M[i] is not None])
    ma = np.array([M[i] for i in range(len(M)) if V[i] is not None and M[i] is not None])
    corr = float(np.corrcoef(va, ma)[0, 1]) if len(va) > 1 else None

    def line(nm, s):
        c, m, f = stats(s)
        neg = sum(1 for x in s if x is not None and x < 0)
        return {"name": nm, "CAGR%": round(c*100, 1), "MDD%": round(m*100, 1),
                "final": round(f, 2), "neg_years": neg}
    summary = [line("밸류 top25", V), line("모멘텀 top25", M),
               line("결합 50:50", B50), line("결합 70:30(밸류heavy)", B70)]
    result = {"generated_at": pd.Timestamp.now().isoformat(), "corr_value_momentum": corr,
              "yearly": {"year": REBAL_YEARS, "value": V, "momentum": M, "blend50": B50, "blend70": B70},
              "summary": summary}
    REPORTS.joinpath("value_momentum_blend.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 64)
    print("밸류 × 모멘텀 결합 — 마이너스 연도 방어 검증")
    print("=" * 64)
    print(f"(생존편향 보정, 5/1 연리밸, top-25, 밸류·모멘텀 상관 {corr:.2f})\n")
    print(f"  {'연도':>6}{'밸류%':>9}{'모멘텀%':>9}{'50:50%':>9}{'70:30%':>9}")
    for i, y in enumerate(REBAL_YEARS):
        def f(x): return f"{x*100:.1f}" if x is not None else "n/a"
        print(f"  {y:>6}{f(V[i]):>9}{f(M[i]):>9}{f(B50[i]):>9}{f(B70[i]):>9}")
    print()
    for s in summary:
        print(f"  {s['name']:<20} CAGR {s['CAGR%']:>5}% | MDD {s['MDD%']:>6}% | "
              f"최종 {s['final']}배 | 마이너스 연도 {s['neg_years']}/7")
    print(f"\n결과 저장: reports/value_momentum_blend.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
