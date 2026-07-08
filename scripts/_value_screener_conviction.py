"""확신 스택 백테스트 — 생존편향 보정 유니버스 + 턴어라운드/3년흑자 진입 필터 + top-5 집중.

목표: "싸고 건실한데 이미 돌기 시작한" 종목만 소수정예로 진입 → 밸류트랩 배제·승률 상향,
      단 대박(fat tail)은 안 죽이는지 검증.

비교 (동일 생존편향 보정 유니버스·point-in-time):
  BASE  : 4게이트 top-10 (현행)
  CONV  : 4게이트 + [3년연속흑자] + [턴어라운드(가격 or 실적 변곡)] → top-5

턴어라운드(둘 중 하나):
  가격 변곡: 6개월 수익률>0 (바닥탈출) OR 현재가>200일선 (추세전환)
  실적 변곡: 순이익 YoY 개선 가속(최근>직전) OR 매출 YoY (-)→(+) 전환

exit: 고정 1년 보유 (편향·필터 효과 비교엔 고정이 명확). 확정 후 밸류정상화 exit 적용.

실행: python scripts/_value_screener_conviction.py   (리서치)
결과: stdout + reports/value_screener_conviction.json
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
import _value_screener_backtest_sv as svbt  # member_at, fwd_return_sv

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REBAL_YEARS = list(range(2019, 2026))
MARCAP_FLOOR_BT = 100e8


def log(msg):
    print(f"[conv] {msg}", flush=True)


def _yoy(a, b, key):
    if a and b and a.get(key) is not None and b.get(key) not in (None, 0):
        return (a[key] - b[key]) / abs(b[key])
    return None


def conviction_signals(code, d, fins, prices, fy1) -> dict:
    """3년흑자 + 턴어라운드(가격/실적 변곡) 신호."""
    fy = fins.get(code, {})
    f1, f2, f3 = fy.get(fy1), fy.get(fy1 - 1), fy.get(fy1 - 2)
    # 3년 연속 흑자
    profit_3y = all(f and f.get("net") is not None and f["net"] > 0 for f in (f1, f2, f3))
    # 실적 변곡: 순이익 YoY 개선 가속 OR 매출 YoY (-)→(+)
    net_yoy1, net_yoy2 = _yoy(f1, f2, "net"), _yoy(f2, f3, "net")
    rev_yoy1, rev_yoy2 = _yoy(f1, f2, "revenue"), _yoy(f2, f3, "revenue")
    fund_turn = False
    if net_yoy1 is not None and net_yoy2 is not None and net_yoy1 > 0 and net_yoy1 > net_yoy2:
        fund_turn = True
    if rev_yoy1 is not None and rev_yoy2 is not None and rev_yoy1 > 0 and rev_yoy2 <= 0:
        fund_turn = True
    # 가격 변곡: 6M 수익률>0 OR 200일선 위
    price_turn = False
    s = prices.get(code)
    if s is not None:
        p_now = vb.price_asof(s, d)
        p_6m = vb.price_asof(s, d - pd.Timedelta(days=182))
        hist = s[s.index <= d]
        ma200 = hist.tail(200).mean() if len(hist) >= 100 else None
        if p_now and p_6m and p_6m > 0 and (p_now / p_6m - 1) > 0:
            price_turn = True
        if p_now and ma200 and p_now > ma200:
            price_turn = True
    return {"profit_3y": profit_3y, "turnaround": (fund_turn or price_turn),
            "fund_turn": fund_turn, "price_turn": price_turn}


def screen(pool, fins, prices, shares_map, name_map, sector_map, d, fy1, fy2,
           conviction: bool, top_n: int):
    df = bt.build_year_rows(pool, fins, shares_map, name_map, prices, d, fy1, fy2, sector_map)
    if df.empty:
        return df
    survivors, _ = vs.run_funnel(df)
    if survivors.empty:
        return survivors
    if conviction:
        keep = []
        for _, r in survivors.iterrows():
            sig = conviction_signals(r["code"], d, fins, prices, fy1)
            if sig["profit_3y"] and sig["turnaround"]:
                keep.append(r["code"])
        survivors = survivors[survivors["code"].isin(keep)]
        if survivors.empty:
            return survivors
    return vs.rank_survivors(survivors).head(top_n)


def run(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
        listed_map, delisted_meta, conviction, top_n):
    yearly, all_rets = [], []
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01")
        sell = pd.Timestamp(f"{y + 1}-05-01")
        pool = [c for c in all_codes if svbt.member_at(c, buy, listed_map, delisted_meta)]
        top = screen(pool, fins, prices, shares_map, name_map, sector_map,
                     buy, y - 1, y - 2, conviction, top_n)
        rets, picks = [], []
        for _, r in top.iterrows():
            fr = svbt.fwd_return_sv(prices, r["code"], buy, sell, delisted_meta)
            picks.append({"name": r["name"], "ret%": round(fr * 100, 1) if fr is not None else None})
            if fr is not None:
                rets.append(fr)
                all_rets.append(fr * 100)
        pr = float(np.mean(rets)) if rets else None
        br = vb.kospi_year_return(ks, buy, sell)
        w = sum(1 for r in rets if r > 0)
        yearly.append({"year": y, "n": len(rets),
                       "port%": round(pr * 100, 1) if pr is not None else None,
                       "kospi%": round(br * 100, 1) if br is not None else None,
                       "win": f"{w}/{len(rets)}", "picks": picks})
    yr = [r["port%"] / 100 for r in yearly if r["port%"] is not None]
    stats = vb.equity_curve_stats(yr)
    wins = sum(1 for r in all_rets if r > 0)
    return {"yearly": yearly, "stats": stats, "all_rets": all_rets,
            "hit_rate": f"{wins}/{len(all_rets)}",
            "avg_stock%": round(float(np.mean(all_rets)), 1) if all_rets else None,
            "median_stock%": round(float(np.median(all_rets)), 1) if all_rets else None,
            "max_stock%": round(max(all_rets), 1) if all_rets else None}


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    cur_codes = list(listing.index)
    delisted_meta = sv.load_delisted_meta()
    sector_map = vs.get_sector_map()
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in cur_codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in cur_codes}
    for c, v in delisted_meta.items():
        if v.get("shares"):
            shares_map[c] = v["shares"]; name_map[c] = v["name"]
    listed_map = _current_listing_dates()
    log(f"유니버스 현 {len(cur_codes)} + 상폐 {len(delisted_meta)}")

    fins = vb.fetch_all_financials(corp_map, cur_codes)
    prices = vb.load_all_prices(cur_codes)
    dfins, dprices = _load_delisted_cached(corp_map, delisted_meta)
    fins.update(dfins); prices.update(dprices)

    import FinanceDataReader as fdr
    ks_cache = vb.FDR_CACHE / "KS11.csv"
    ks = (pd.read_csv(ks_cache, index_col=0, parse_dates=True)["Close"] if ks_cache.exists()
          else fdr.DataReader("KS11", "2015-01-01", "2026-12-31")["Close"].dropna())

    all_codes = list(shares_map)
    log("BASE (4게이트 top-10)...")
    base = run(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
               listed_map, delisted_meta, conviction=False, top_n=10)
    log("CONV (확신스택 top-5)...")
    conv = run(all_codes, fins, prices, shares_map, name_map, sector_map, ks,
               listed_map, delisted_meta, conviction=True, top_n=5)

    result = {"generated_at": pd.Timestamp.now().isoformat(),
              "base": {k: base[k] for k in ("hit_rate", "avg_stock%", "median_stock%", "max_stock%")},
              "conv": {k: conv[k] for k in ("hit_rate", "avg_stock%", "median_stock%", "max_stock%")},
              "base_CAGR%": round(base["stats"]["CAGR"] * 100, 1) if base["stats"]["CAGR"] else None,
              "base_MDD%": round(base["stats"]["MDD"] * 100, 1) if base["stats"]["MDD"] else None,
              "conv_CAGR%": round(conv["stats"]["CAGR"] * 100, 1) if conv["stats"]["CAGR"] else None,
              "conv_MDD%": round(conv["stats"]["MDD"] * 100, 1) if conv["stats"]["MDD"] else None,
              "base_yearly": base["yearly"], "conv_yearly": conv["yearly"]}
    REPORTS.joinpath("value_screener_conviction.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 76)
    print("확신 스택 백테스트 — BASE(top10) vs CONV(top5 + 턴어라운드 + 3년흑자)")
    print("=" * 76)
    print("(생존편향 보정 유니버스, 고정 1년 보유, 2019~2025)\n")
    print(f"  {'연도':>6}{'BASE%':>9}{'BASE승':>8}{'CONV%':>9}{'CONV승':>8}{'KOSPI%':>9}")
    for a, c in zip(base["yearly"], conv["yearly"]):
        bp = f"{a['port%']:.1f}" if a["port%"] is not None else "n/a"
        cp = f"{c['port%']:.1f}" if c["port%"] is not None else "n/a"
        bk = f"{c['kospi%']:.1f}" if c["kospi%"] is not None else "n/a"
        print(f"  {c['year']:>6}{bp:>9}{a['win']:>8}{cp:>9}{c['win']:>8}{bk:>9}")
    print(f"\n  BASE : CAGR {result['base_CAGR%']}% | MDD {result['base_MDD%']}% "
          f"| 종목승률 {base['hit_rate']} | 종목평균 {base['avg_stock%']}% | 최대 {base['max_stock%']}%")
    print(f"  CONV : CAGR {result['conv_CAGR%']}% | MDD {result['conv_MDD%']}% "
          f"| 종목승률 {conv['hit_rate']} | 종목평균 {conv['avg_stock%']}% | 최대 {conv['max_stock%']}%")
    print(f"\n결과 저장: reports/value_screener_conviction.json")


def _current_listing_dates():
    import FinanceDataReader as fdr
    d = fdr.StockListing("KRX-DESC")
    d["Code"] = d["Code"].astype(str).str.zfill(6)
    return {r["Code"]: pd.to_datetime(r.get("ListingDate"), errors="coerce")
            for _, r in d.iterrows()}


def _load_delisted_cached(corp_map, delisted_meta):
    """상폐 재무·주가 캐시 로드 (sv 백테스트가 이미 페치함)."""
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
