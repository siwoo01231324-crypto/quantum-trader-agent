"""대형 가치 + KOSPI 200일선 레짐 오버레이 — 약세장 손실 축소 검증 (한국 데이터).

딥리서치 결론: 추세/레짐 오버레이는 raw 수익 증대가 아니라 대형 약세장 회피로 MDD 반토막.
단 근거가 전부 미국/글로벌 → 한국 KOSPI 직접 검증. 우리 대형 보수 가치에
"KOSPI 200일선 아래면 현금(또는 반현금)" 레짐 필터를 얹어 2022 약세장 손실·MDD가 주는지.

3가지 비교: 가치_원본(항상투자) / 가치_레짐(off시 현금) / 가치_반레짐(off시 50%현금).
결정론적: 월초 KOSPI 종가 > 200일 이동평균이면 risk-on(가치 보유), 아니면 risk-off.

실행: python scripts/_value_regime_overlay.py   (리서치)
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
START, END = pd.Timestamp("2019-05-01"), pd.Timestamp("2026-05-01")
TOP_N = 20
bt.MARCAP_FLOOR_BT = 5000e8   # 대형주 (보수)


def log(m): print(f"[regime] {m}", flush=True)


def monthly_fwd(prices, code, m, nm):
    s = prices.get(code)
    if s is None: return None
    p0, p1 = vb.price_asof(s, m), vb.price_asof(s, nm)
    if p0 is None or p1 is None or p0 <= 0: return None
    return p1 / p0 - 1.0


def stats(series):
    eq = np.cumprod([1 + r for r in series]); n = len(series) / 12
    cagr = eq[-1] ** (1 / n) - 1
    peak = np.maximum.accumulate(np.concatenate([[1], eq])); dd = np.concatenate([[1], eq]) / peak - 1
    s = pd.Series(series); sh = float(s.mean()/s.std()*np.sqrt(12)) if s.std() > 0 else None
    return cagr, float(dd.min()), float(eq[-1]), sh


def yearly(months, series):
    by = {}
    for m, r in zip(months, series):
        y = m.year if m.month >= 5 else m.year - 1
        by.setdefault(y, []).append(r)
    return {y: float(np.prod([1+x for x in v])-1) for y, v in sorted(by.items())}


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    codes = list(listing.index)
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    sector_map = vs.get_sector_map()
    fins = vb.fetch_all_financials(corp_map, codes)
    prices = vb.load_all_prices(codes)
    ksc = vb.FDR_CACHE / "KS11.csv"
    ks = pd.read_csv(ksc, index_col=0, parse_dates=True)["Close"]
    ks_ma200 = ks.rolling(200).mean()

    # 대형 가치 바스켓 (연 5/1, 대형floor)
    baskets = {}
    for y in range(2019, 2026):
        buy = pd.Timestamp(f"{y}-05-01")
        df = bt.build_year_rows(codes, fins, shares_map, name_map, prices, buy, y-1, y-2, sector_map)
        if df.empty:
            baskets[y] = []; continue
        surv, _ = vs.run_funnel(df)
        baskets[y] = vs.rank_survivors(surv).head(TOP_N)["code"].tolist()
    log(f"대형 가치 바스켓 준비 (top{TOP_N}, 시총≥5천억)")

    months = pd.date_range(START, END, freq="MS")
    V, regime = [], []
    for i in range(len(months) - 1):
        m, nm = months[i], months[i+1]
        by = m.year if m.month >= 5 else m.year - 1
        rets = [monthly_fwd(prices, c, m, nm) for c in baskets.get(by, [])]
        rets = [r for r in rets if r is not None]
        V.append(float(np.mean(rets)) if rets else 0.0)
        # 레짐: 월초 직전 거래일 KOSPI > 200MA?
        k = ks[ks.index <= m]
        km = ks_ma200[ks_ma200.index <= m]
        on = (len(k) and len(km.dropna()) and k.iloc[-1] > km.dropna().iloc[-1])
        regime.append(bool(on))

    mo = list(months[:-1])
    V_reg = [v if on else 0.0 for v, on in zip(V, regime)]          # off=현금
    V_half = [v if on else 0.5*v for v, on in zip(V, regime)]        # off=50%
    off_months = sum(1 for on in regime if not on)

    out = []
    for nm_, s in (("가치 원본", V), ("가치+레짐(현금)", V_reg), ("가치+반레짐(50%)", V_half)):
        c, mdd, f, sh = stats(s); yv = yearly(mo, s)
        neg = sum(1 for r in yv.values() if r < 0)
        out.append({"name": nm_, "CAGR%": round(c*100,1), "MDD%": round(mdd*100,1),
                    "Sharpe": round(sh,2) if sh else None, "final": round(f,2),
                    "neg_years": neg, "yearly": {y: round(r*100,1) for y,r in yv.items()}})
    result = {"generated_at": pd.Timestamp.now().isoformat(), "top_n": TOP_N,
              "off_months": off_months, "total_months": len(mo), "summary": out}
    REPORTS.joinpath("value_regime_overlay.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "="*66)
    print("대형 가치 + KOSPI 200일선 레짐 오버레이 (한국 검증)")
    print("="*66)
    print(f"(생존편향 미보정·대형 top{TOP_N}, risk-off {off_months}/{len(mo)}개월)\n")
    yrs = sorted(out[0]["yearly"])
    print(f"  {'연도':>6}{'원본%':>9}{'레짐현금%':>11}{'반레짐%':>10}")
    for y in yrs:
        print(f"  {y:>6}{out[0]['yearly'][y]:>9.1f}{out[1]['yearly'][y]:>11.1f}{out[2]['yearly'][y]:>10.1f}")
    print()
    for s in out:
        print(f"  {s['name']:<16} CAGR {s['CAGR%']:>5}% | MDD {s['MDD%']:>6}% | Sharpe {s['Sharpe']} | 마이너스 {s['neg_years']}/{len(yrs)}")
    print(f"\n결과 저장: reports/value_regime_overlay.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
