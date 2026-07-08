"""저평가·우량주 스크리너 게이트 로직의 point-in-time 백테스트.

질문: "이 스크리너 로직으로 과거(예: 2024) 어떤 종목이 포착됐고, 사서 1년 뒤 수익률은?"

_value_screener.py 의 4단계 게이트 퍼널(안전→실적→저평가→안전마진) + 밸류×퀄리티 랭킹을
매 리밸 연도에 point-in-time 으로 적용해 top-N 픽 → 1년 보유 forward return 측정.

look-ahead 금지: 매년 5/1 매수, 그 시점엔 FY(Y-1) 사업보고서(11011) 공시완료(~3~4월).
  FY(Y-1) 재무 + 5/1 Y 시총(=5/1 종가 × 주식수) → 게이트/팩터. 보유 5/1 Y ~ 5/1 Y+1.

유니버스: _value_screener.UNIVERSE ("all"=KOSPI+KOSDAQ 972). 9년 재무 + 주가 필요.

⚠️ 생존편향 (반드시 감안, 낙관 편의):
  - 유니버스 = 오늘 상장·유동성 필터 통과 종목만. 과거 상장폐지·편출 종목 미포함 → 수익률 상방 편의.
  - 과거 시총 = 과거 종가 × 현재 주식수(고정). 액면분할/증자 오차.
  - 이 백테스트는 "게이트 로직이 살아남은 종목들 사이에서 유효한가" 를 볼 뿐,
    진짜 실현가능 수익률의 하한이 아님. 진정한 검증은 point-in-time 상장이력 필요(TODO #1).

실행: python scripts/_value_screener_backtest.py   (리서치 산출물)
결과: stdout + reports/value_screener_backtest.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _value_backtest as vb    # noqa: E402  DART/FDR 파이프라인 + forward_return
import _value_screener as vs    # noqa: E402  게이트 퍼널 + 랭킹

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)

REBAL_YEARS = list(range(2019, 2026))  # 5/1 매수 2019..2025, 1년 보유
TOP_N = 10
SPOTLIGHT_YEAR = 2024                  # 개별 종목 상세 표시 연도
MARCAP_FLOOR_BT = 100e8                # point-in-time 시총 하한 (오늘 거래대금 대신)


def log(msg: str) -> None:
    print(f"[bt] {msg}", flush=True)


def build_year_rows(codes, fins, shares_map, name_map, prices, buy, fy1, fy2, sector_map=None):
    """리밸 연도 팩터 테이블 (point-in-time). 스크리너 게이트용 extras + A/C 포함."""
    sector_map = sector_map or {}
    rows = []
    for code in codes:
        if code not in fins or code not in shares_map:
            continue
        f1 = fins[code].get(fy1)
        f2 = fins[code].get(fy2)
        if not f1:
            continue
        s = prices.get(code)
        if s is None:
            continue
        p = vb.price_asof(s, buy)
        if p is None or p <= 0:
            continue
        mktcap = p * shares_map[code]
        if mktcap < MARCAP_FLOOR_BT:   # point-in-time 시총 하한
            continue
        row = vb.build_factor_row(code, name_map.get(code, code), shares_map[code],
                                  f1, f2, mktcap)
        if not row:
            continue
        row.update(vs.extra_safety(f1))
        price = mktcap / shares_map[code]
        mos = None
        if row.get("graham") and price > 0:
            mos = row["graham"] / price - 1.0
        row["price"] = price
        row["margin_of_safety"] = mos
        sm = sector_map.get(code, {})
        row["sector"] = sm.get("sector", "기타")
        row["is_holdco"] = bool(sm.get("is_holdco", False))
        vs.apply_holdco_adjustment(row)  # C: 지주사 NAV 할인
        rows.append(row)
    return pd.DataFrame(rows)


def screen_top_n(df: pd.DataFrame) -> pd.DataFrame:
    """스크리너 4게이트 퍼널 + 밸류×퀄리티 랭킹 → top-N."""
    if df.empty:
        return df
    survivors, _ = vs.run_funnel(df)
    ranked = vs.rank_survivors(survivors)
    return ranked.head(TOP_N)


def main() -> None:
    # 유니버스 = 스크리너와 동일 (KOSPI+KOSDAQ 필터 통과 972)
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    codes = list(listing.index)
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    sector_map = vs.get_sector_map()  # A/C: 업종·지주사
    log(f"유니버스 {len(codes)} 종목 — 9년 재무 + 주가 페치 (첫 실행 시 수분) "
        f"| A={vs.SECTOR_RELATIVE} C={vs.HOLDCO_MODE}")

    # 9년 재무 (백테스트는 전 연도 필요 → fetch_all_financials, 캐시)
    fins = vb.fetch_all_financials(corp_map, codes)
    prices = vb.load_all_prices(codes)
    log(f"재무 {len(fins)} · 주가 {len(prices)} 확보")

    # KOSPI 벤치
    import FinanceDataReader as fdr
    ks_cache = vb.FDR_CACHE / "KS11.csv"
    if ks_cache.exists():
        ks = pd.read_csv(ks_cache, index_col=0, parse_dates=True)["Close"]
    else:
        ks = fdr.DataReader("KS11", "2015-01-01", "2026-12-31")["Close"].dropna()
        ks.to_frame("Close").to_csv(ks_cache)

    yearly = []
    spotlight = None
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01")
        sell = pd.Timestamp(f"{y + 1}-05-01")
        df = build_year_rows(codes, fins, shares_map, name_map, prices, buy, y - 1, y - 2,
                             sector_map)
        top = screen_top_n(df)
        picks = []
        for _, r in top.iterrows():
            fr = vb.forward_return(prices, r["code"], buy, sell)
            picks.append({
                "code": r["code"], "name": r["name"],
                "PER": round(r["PER"], 2) if pd.notna(r["PER"]) else None,
                "PBR": round(r["PBR"], 2) if pd.notna(r["PBR"]) else None,
                "ROE%": round(r["ROE"] * 100, 1) if pd.notna(r["ROE"]) else None,
                "fscore": int(r["fscore"]) if pd.notna(r["fscore"]) else None,
                "안전마진%": round(r["margin_of_safety"] * 100) if pd.notna(r.get("margin_of_safety")) else None,
                "fwd_return%": round(fr * 100, 1) if fr is not None else None,
            })
        rets = [p["fwd_return%"] / 100 for p in picks if p["fwd_return%"] is not None]
        port = float(np.mean(rets)) if rets else None
        br = vb.kospi_year_return(ks, buy, sell)
        wins = sum(1 for r in rets if r > 0)
        rec = {
            "year": y, "universe_valid": len(df), "n_picks": len(picks),
            "n_priced": len(rets),
            "port_return%": round(port * 100, 1) if port is not None else None,
            "kospi_return%": round(br * 100, 1) if br is not None else None,
            "excess%": round((port - br) * 100, 1) if (port is not None and br is not None) else None,
            "win_stocks": f"{wins}/{len(rets)}",
            "picks": picks,
        }
        yearly.append(rec)
        if y == SPOTLIGHT_YEAR:
            spotlight = rec

    # 요약 통계 (연수익률 시계열)
    yr = [r["port_return%"] / 100 for r in yearly if r["port_return%"] is not None]
    br = [r["kospi_return%"] / 100 for r in yearly if r["kospi_return%"] is not None]
    port_stats = vb.equity_curve_stats(yr)
    bench_stats = vb.equity_curve_stats(br)
    excesses = [r["excess%"] / 100 for r in yearly if r["excess%"] is not None]
    win_years = sum(1 for e in excesses if e > 0)
    all_stock_rets = [p["fwd_return%"] for r in yearly for p in r["picks"]
                      if p["fwd_return%"] is not None]

    result = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "universe_mode": vs.UNIVERSE,
        "universe_size": len(codes),
        "rebal_years": REBAL_YEARS,
        "top_n": TOP_N,
        "gates": vs.GATE,
        "yearly": yearly,
        "summary": {
            "port_CAGR%": round(port_stats["CAGR"] * 100, 1) if port_stats["CAGR"] is not None else None,
            "port_MDD%": round(port_stats["MDD"] * 100, 1) if port_stats["MDD"] is not None else None,
            "port_Sharpe": round(port_stats["Sharpe"], 2) if port_stats["Sharpe"] is not None else None,
            "kospi_CAGR%": round(bench_stats["CAGR"] * 100, 1) if bench_stats["CAGR"] is not None else None,
            "win_years": f"{win_years}/{len(excesses)}",
            "avg_excess%": round(float(np.mean(excesses)) * 100, 1) if excesses else None,
            "stock_hit_rate": f"{sum(1 for r in all_stock_rets if r > 0)}/{len(all_stock_rets)}",
            "avg_stock_return%": round(float(np.mean(all_stock_rets)), 1) if all_stock_rets else None,
            "median_stock_return%": round(float(np.median(all_stock_rets)), 1) if all_stock_rets else None,
        },
        "disclosures": [
            "생존편향: 유니버스=오늘 상장·유동성 통과 종목만. 과거 상폐·편출 미포함 → 수익률 상방 편의.",
            "과거 시총=과거종가×현재주식수(고정). EV≈시총+부채. F-Score 7/9.",
            "게이트 로직 유효성 검증용 — 실현가능 수익률 하한 아님. 진짜 검증은 point-in-time 상장이력 필요.",
        ],
    }
    REPORTS.joinpath("value_screener_backtest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---------- stdout ----------
    print("\n" + "=" * 78)
    print(f"저평가·우량주 스크리너 백테스트 — 4게이트 top-{TOP_N} 매년 5/1 리밸, 1년 보유")
    print("=" * 78)
    print(f"유니버스({vs.UNIVERSE}) {len(codes)} | {REBAL_YEARS[0]}~{REBAL_YEARS[-1]}\n")
    print(f"  {'연도':>6}{'유효':>6}{'픽':>4}{'포트%':>9}{'KOSPI%':>9}{'초과%':>9}{'승':>7}")
    for r in yearly:
        pr = f"{r['port_return%']:.1f}" if r["port_return%"] is not None else "n/a"
        bk = f"{r['kospi_return%']:.1f}" if r["kospi_return%"] is not None else "n/a"
        ex = f"{r['excess%']:.1f}" if r["excess%"] is not None else "n/a"
        print(f"  {r['year']:>6}{r['universe_valid']:>6}{r['n_picks']:>4}{pr:>9}{bk:>9}{ex:>9}{r['win_stocks']:>7}")

    s = result["summary"]
    print(f"\n  → 포트 CAGR {s['port_CAGR%']}% | MDD {s['port_MDD%']}% | Sharpe {s['port_Sharpe']} "
          f"| KOSPI CAGR {s['kospi_CAGR%']}%")
    print(f"  → 초과승 {s['win_years']} | 연평균초과 {s['avg_excess%']}% "
          f"| 종목 승률 {s['stock_hit_rate']} | 종목평균 {s['avg_stock_return%']}% (중앙값 {s['median_stock_return%']}%)")

    if spotlight:
        print(f"\n■ {SPOTLIGHT_YEAR}년 5/1 포착 종목 (→ {SPOTLIGHT_YEAR + 1}년 5/1 매도)")
        print(f"  {'종목':<14}{'PER':>6}{'PBR':>6}{'ROE%':>7}{'F':>3}{'안전마진':>8}{'수익률':>9}")
        for p in spotlight["picks"]:
            fr = f"{p['fwd_return%']:+.1f}" if p["fwd_return%"] is not None else "n/a"
            per = f"{p['PER']:.1f}" if p["PER"] is not None else "-"
            pbr = f"{p['PBR']:.2f}" if p["PBR"] is not None else "-"
            roe = f"{p['ROE%']:.1f}" if p["ROE%"] is not None else "-"
            mos = f"{p['안전마진%']}" if p["안전마진%"] is not None else "-"
            print(f"  {p['name'][:14]:<14}{per:>6}{pbr:>6}{roe:>7}{p['fscore']:>3}{mos:>7}%{fr:>9}%")

    print("\n■ 한계·정직")
    for d in result["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/value_screener_backtest.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 백테스트 예외 발생 !!!", flush=True)
        traceback.print_exc()
        sys.exit(1)
