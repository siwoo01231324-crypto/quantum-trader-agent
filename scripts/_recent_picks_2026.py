"""2026-05-01 리밸 시점 밸류 스크리너 픽 + 근황(오늘까지 수익률) — 전진검증.

2026-05-01 엔 FY2025 사업보고서(3월 공시) 사용 가능 → 그 시점 정확 스크린.
각 픽의 2026-05-01 → 오늘 수익률로 "최근 픽이 실제로 어떻게 됐나" 확인.

실행: python scripts/_recent_picks_2026.py   (리서치)
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
import _value_screener_backtest as bt

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
ASOF = pd.Timestamp("2026-05-01")   # 리밸 시점
FY1, FY2 = 2025, 2024               # 2026-05-01 가용 재무
TOP_N = 15
MARCAP_FLOOR = 100e8


def log(m): print(f"[recent] {m}", flush=True)


def fetch_fy2025(corp_map, codes):
    """FY2025 재무 페치(신규) — 기존 fins 에 추가."""
    add = {}
    for i, code in enumerate(codes):
        corp = corp_map.get(code)
        if not corp:
            continue
        cache = vb.FIN_CACHE / f"{corp}_2025.json"
        existed = cache.exists()
        parsed = vb.parse_fin(vb.fetch_fin_raw(corp, 2025))
        if parsed:
            add[code] = parsed
        if not existed:
            time.sleep(0.03)
        if (i + 1) % 200 == 0:
            log(f"FY2025 {i+1}/{len(codes)}")
    return add


def main():
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    codes = list(listing.index)
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    sector_map = vs.get_sector_map()
    log(f"유니버스 {len(codes)} — FY2025 페치 + FY2024 로드")

    # FY2024/2023 은 캐시, FY2025 신규
    fins = vb.fetch_all_financials(corp_map, codes)   # 2016~2024
    fy25 = fetch_fy2025(corp_map, codes)
    for c, f in fy25.items():
        fins.setdefault(c, {})[2025] = f
    log(f"FY2025 확보 {len(fy25)}")

    prices = vb.load_all_prices(codes)
    # 2026-05-01 시총 = 그날 종가 × 주식수
    df = bt.build_year_rows(codes, fins, shares_map, name_map, prices, ASOF, FY1, FY2, sector_map)
    if df.empty:
        print("FY2025 데이터 부족 — 스크린 불가"); return
    survivors, stages = vs.run_funnel(df)
    top = vs.rank_survivors(survivors).head(TOP_N)
    log(f"2026-05-01 스크린: 유효 {len(df)} → 통과 {len(survivors)} → top {len(top)}")

    # 근황: 2026-05-01 → 최신 종가
    picks = []
    for _, r in top.iterrows():
        s = prices.get(r["code"])
        p0 = vb.price_asof(s, ASOF) if s is not None else None
        p1 = float(s.iloc[-1]) if (s is not None and len(s)) else None
        last_dt = s.index[-1].strftime("%Y-%m-%d") if (s is not None and len(s)) else None
        ret = (p1 / p0 - 1) if (p0 and p1 and p0 > 0) else None
        picks.append({"code": r["code"], "name": r["name"], "sector": r.get("sector"),
                      "PER": round(r["PER"], 1) if pd.notna(r["PER"]) else None,
                      "PBR": round(r["PBR"], 2) if pd.notna(r["PBR"]) else None,
                      "ROE%": round(r["ROE"]*100, 1) if pd.notna(r["ROE"]) else None,
                      "안전마진%": round(r["margin_of_safety"]*100) if pd.notna(r.get("margin_of_safety")) else None,
                      "매수가": round(p0) if p0 else None, "현재가": round(p1) if p1 else None,
                      "수익률%": round(ret*100, 1) if ret is not None else None})
    rets = [p["수익률%"] for p in picks if p["수익률%"] is not None]
    avg = float(np.mean(rets)) if rets else None
    wins = sum(1 for r in rets if r > 0)
    # KOSPI 동기간
    ksc = vb.FDR_CACHE / "KS11.csv"
    ks = pd.read_csv(ksc, index_col=0, parse_dates=True)["Close"] if ksc.exists() else None
    ks_ret = None
    if ks is not None:
        k0, k1 = vb.price_asof(ks, ASOF), float(ks.iloc[-1])
        ks_ret = (k1/k0-1)*100 if k0 else None

    result = {"generated_at": pd.Timestamp.now().isoformat(), "asof": str(ASOF.date()),
              "latest_price_date": picks[0].get("code") and (prices[picks[0]["code"]].index[-1].strftime("%Y-%m-%d")),
              "avg_return%": round(avg, 1) if avg else None, "win": f"{wins}/{len(rets)}",
              "kospi_return%": round(ks_ret, 1) if ks_ret else None, "picks": picks}
    REPORTS.joinpath("recent_picks_2026.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 74)
    print(f"2026-05-01 밸류 스크리너 픽 → 근황 (오늘까지)")
    print("=" * 74)
    lp = prices[picks[0]["code"]].index[-1].strftime("%Y-%m-%d") if picks else "?"
    print(f"(FY2025 기준 스크린, 매수 2026-05-01 → 현재 {lp})\n")
    print(f"  {'종목':<14}{'섹터':<9}{'PER':>5}{'PBR':>6}{'ROE%':>6}{'안전마진':>7}{'수익률':>8}")
    for p in picks:
        print(f"  {p['name'][:14]:<14}{str(p['sector'])[:8]:<9}{(p['PER'] or 0):>5.1f}"
              f"{(p['PBR'] or 0):>6.2f}{(p['ROE%'] or 0):>6.1f}{str(p['안전마진%']):>6}%"
              f"{(p['수익률%'] if p['수익률%'] is not None else 0):>+7.1f}%")
    print(f"\n  → 평균 수익률 {result['avg_return%']}% | 승 {result['win']} | "
          f"KOSPI 동기간 {result['kospi_return%']}%")
    print(f"\n결과 저장: reports/recent_picks_2026.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
