"""밸류 × 모멘텀 결합 v2 — 모멘텀 월단위 리밸(제대로).

v1 오류: 모멘텀을 연1회 보유로 구현 → 추세 반전에 죽음(상관 +0.91 착시). 정정:
  모멘텀 = 매월 12-1 모멘텀 top-N 재선정·1개월 보유 (native 주기).
  밸류   = 연1회(5/1) top-N 선정 후 보유, 월단위 시가평가로 월수익 산출.
둘 다 월수익 시계열 → 월단위 결합 → 상관·MDD·마이너스연도 정확 비교.

동일: 생존편향 보정 유니버스, top-25, 2019-05~2026-05.
실행: python scripts/_value_momentum_blend_v2.py   (리서치)
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
START, END = pd.Timestamp("2019-05-01"), pd.Timestamp("2026-05-01")
TOP_N = 25
MARCAP_FLOOR = 100e8


def log(m): print(f"[vm2] {m}", flush=True)


def monthly_fwd(prices, code, m, nm, dm):
    """[m, nm] 월수익. 보유 중 상폐면 상폐직전가."""
    s = prices.get(code)
    if s is None:
        return None
    eff = nm
    d = dm.get(code)
    if d:
        dd = pd.to_datetime(d["delisting_date"], errors="coerce")
        if pd.notna(dd) and m < dd <= nm:
            eff = dd
    p0, p1 = vb.price_asof(s, m), vb.price_asof(s, eff)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def value_baskets(all_codes, fins, prices, shares_map, name_map, sector_map, listed_map, dm):
    """연도별(5/1) 밸류 top-25 코드 바스켓."""
    baskets = {}
    for y in range(2019, 2026):
        buy = pd.Timestamp(f"{y}-05-01")
        pool = [c for c in all_codes if svbt.member_at(c, buy, listed_map, dm)]
        df = bt.build_year_rows(pool, fins, shares_map, name_map, prices, buy, y - 1, y - 2, sector_map)
        if df.empty:
            baskets[y] = []; continue
        survivors, _ = vs.run_funnel(df)
        baskets[y] = vs.rank_survivors(survivors).head(TOP_N)["code"].tolist()
    return baskets


def momentum_basket(all_codes, prices, shares_map, m, dm, listed_map):
    """월 m 시점 12-1 모멘텀 top-25 (양의 모멘텀)."""
    rows = []
    for code in all_codes:
        if not svbt.member_at(code, m, listed_map, dm):
            continue
        s = prices.get(code)
        if s is None:
            continue
        p_r = vb.price_asof(s, m - pd.Timedelta(days=21))
        p_o = vb.price_asof(s, m - pd.Timedelta(days=252))
        p_now = vb.price_asof(s, m)
        if not (p_r and p_o and p_now) or p_o <= 0 or p_now <= 0:
            continue
        if p_now * shares_map.get(code, 0) < MARCAP_FLOOR:
            continue
        rows.append((code, p_r / p_o - 1.0))
    rows = [r for r in rows if r[1] > 0]
    rows.sort(key=lambda x: x[1], reverse=True)
    return [c for c, _ in rows[:TOP_N]]


def basket_ret(prices, codes, m, nm, dm):
    rets = [monthly_fwd(prices, c, m, nm, dm) for c in codes]
    rets = [r for r in rets if r is not None]
    return float(np.mean(rets)) if rets else 0.0


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


def yearly_from_monthly(months, series):
    """월수익 → 5/1~4/30 연도별 복리수익."""
    by = {}
    for m, r in zip(months, series):
        yr = m.year if m.month >= 5 else m.year - 1
        by.setdefault(yr, []).append(r)
    return {y: float(np.prod([1 + x for x in v]) - 1) for y, v in sorted(by.items())}


def stats(series):
    eq = np.cumprod([1 + r for r in series])
    n = len(series) / 12
    cagr = eq[-1] ** (1 / n) - 1
    peak = np.maximum.accumulate(np.concatenate([[1], eq])); dd = np.concatenate([[1], eq]) / peak - 1
    s = pd.Series(series)
    sharpe = float(s.mean() / s.std() * np.sqrt(12)) if s.std() > 0 else None
    return cagr, float(dd.min()), float(eq[-1]), sharpe


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

    log("밸류 바스켓(연1회)...")
    vbask = value_baskets(all_codes, fins, prices, shares_map, name_map, sector_map, listed_map, dm)

    months = pd.date_range(START, END, freq="MS")
    V, M = [], []
    log(f"월단위 {len(months)-1}개월 시뮬 (모멘텀 매월 재선정)...")
    for i in range(len(months) - 1):
        m, nm = months[i], months[i + 1]
        by = m.year if m.month >= 5 else m.year - 1   # 현재 밸류 바스켓 연도
        V.append(basket_ret(prices, vbask.get(by, []), m, nm, dm))
        mb = momentum_basket(all_codes, prices, shares_map, m, dm, listed_map)
        M.append(basket_ret(prices, mb, m, nm, dm))

    mo = list(months[:-1])
    B50 = [0.5 * v + 0.5 * mm for v, mm in zip(V, M)]
    B70 = [0.7 * v + 0.3 * mm for v, mm in zip(V, M)]
    corr = float(np.corrcoef(V, M)[0, 1])

    def summ(nm, s):
        c, mdd, f, sh = stats(s)
        yv = yearly_from_monthly(mo, s)
        neg = sum(1 for r in yv.values() if r < 0)
        return {"name": nm, "CAGR%": round(c*100, 1), "MDD%": round(mdd*100, 1),
                "Sharpe": round(sh, 2) if sh else None, "final": round(f, 2),
                "neg_years": neg, "yearly": {y: round(r*100, 1) for y, r in yv.items()}}
    S = [summ("밸류 top25", V), summ("모멘텀 top25(월)", M),
         summ("결합 50:50", B50), summ("결합 70:30", B70)]
    result = {"generated_at": pd.Timestamp.now().isoformat(),
              "corr_monthly": corr, "months": len(mo), "summary": S}
    REPORTS.joinpath("value_momentum_blend_v2.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 68)
    print("밸류 × 모멘텀 결합 v2 — 모멘텀 월단위 리밸(정정)")
    print("=" * 68)
    print(f"(생존편향 보정, top-25, {len(mo)}개월, 밸류·모멘텀 월수익 상관 {corr:.2f})\n")
    yrs = sorted(S[0]["yearly"])
    print(f"  {'연도':>6}{'밸류%':>9}{'모멘텀%':>9}{'50:50%':>9}{'70:30%':>9}")
    for y in yrs:
        print(f"  {y:>6}{S[0]['yearly'][y]:>9.1f}{S[1]['yearly'][y]:>9.1f}"
              f"{S[2]['yearly'][y]:>9.1f}{S[3]['yearly'][y]:>9.1f}")
    print()
    for s in S:
        print(f"  {s['name']:<15} CAGR {s['CAGR%']:>5}% | MDD {s['MDD%']:>6}% | "
              f"Sharpe {s['Sharpe']} | 마이너스 {s['neg_years']}/{len(yrs)}")
    print(f"\n결과 저장: reports/value_momentum_blend_v2.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
