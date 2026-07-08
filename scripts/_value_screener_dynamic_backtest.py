"""저평가·우량주 스크리너 — 동적 익절·로테이션 백테스트.

고정 1년 보유가 아니라: 스크리너 상위 종목을 N슬롯 보유 → 각 종목이 익절선(+TP%)
도달하면 팔고, 그 시점 스크리너 최상위 후보로 교체(로테이션). 죽은 돈 방지용 최대보유 상한.

규칙:
  - N_SLOTS 슬롯, 각 슬롯 동일가중.
  - 익절: 보유 중 일봉 고가가 진입가×(1+TP_PCT) 도달 → 그 가격에 매도(지정가 체결 가정).
  - 최대보유: MAX_HOLD_DAYS 초과 시 당일 종가 매도(스태그넌트 강제 회전).
  - 손절: STOP_PCT (None 이면 미적용 — 가치투자는 펀더가 유효한 한 보유).
  - 월 1회(월초) 스크린 재계산 → 빈 슬롯을 그 시점 최상위 미보유 후보로 채움.
  - 스크리너 게이트/랭킹/A(업종상대)/C(지주사) 그대로 적용.

look-ahead 금지: 날짜 D 의 가용 재무 = FY(D.year-1) if D.month>=5 else FY(D.year-2).

⚠️ 생존편향: 유니버스=오늘 상장·유동성 통과 종목만. 과거 상폐 미포함 → 낙관 편의(스크리너 백테스트와 동일).

실행: python scripts/_value_screener_dynamic_backtest.py   (리서치)
결과: stdout + reports/value_screener_dynamic_backtest.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _value_backtest as vb              # noqa: E402
import _value_screener as vs              # noqa: E402
import _value_screener_backtest as bt     # noqa: E402  build_year_rows 재사용

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

# ---- 동적 전략 파라미터 ------------------------------------------------------
N_SLOTS = 10
EXIT_MODE = "value_normalization"  # "value_normalization"(밸류정상화) | "take_profit"
TP_PCT = 0.35              # take_profit 모드 익절선 (value_normalization 에선 미사용)
MAX_HOLD_DAYS = 1095       # 최대 보유 백스톱 ~3년 (밸류정상화가 1차 exit, 죽은돈 방지)
STOP_PCT = None            # 손절 미적용 (밸류트랩은 exit 패치가 아닌 entry 필터로 해결 — 아래 설계)
START = pd.Timestamp("2019-05-01")
END = pd.Timestamp("2026-05-01")


def log(msg: str) -> None:
    print(f"[dyn] {msg}", flush=True)


def fy_for_date(d: pd.Timestamp) -> tuple[int, int]:
    """날짜 D 가용 FY(직전 사업보고서 공시 ~5월). (fy1, fy2)."""
    fy1 = d.year - 1 if d.month >= 5 else d.year - 2
    return fy1, fy1 - 1


def screen_at(codes, fins, shares_map, name_map, prices, d, sector_map) -> tuple[set, list[str]]:
    """날짜 D 시점 스크리너 → (통과종목 집합, 랭킹순 코드리스트).

    통과집합 = 4게이트 모두 통과(=보유 조건). 랭킹리스트 = 신규 진입 후보(상위순).
    밸류정상화 exit: 보유 종목이 이 집합에서 빠지면(재평가로 저평가 아님 OR 펀더 악화) 매도.
    """
    fy1, fy2 = fy_for_date(d)
    df = bt.build_year_rows(codes, fins, shares_map, name_map, prices, d, fy1, fy2, sector_map)
    if df.empty:
        return set(), []
    survivors, _ = vs.run_funnel(df)
    if survivors.empty:
        return set(), []
    ranked = vs.rank_survivors(survivors)
    return set(survivors["code"]), ranked["code"].tolist()


def main() -> None:
    corp_map = vb.build_corp_map()
    listing = vs.load_full_listing()
    codes = list(listing.index)
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    sector_map = vs.get_sector_map()
    log(f"유니버스 {len(codes)} | TP {TP_PCT:.0%} · 최대보유 {MAX_HOLD_DAYS}일 · {N_SLOTS}슬롯")

    fins = vb.fetch_all_financials(corp_map, codes)
    prices = vb.load_all_prices(codes)
    log(f"재무 {len(fins)} · 주가 {len(prices)} 확보 — 시뮬 시작")

    import FinanceDataReader as fdr
    ks_cache = vb.FDR_CACHE / "KS11.csv"
    if ks_cache.exists():
        ks = pd.read_csv(ks_cache, index_col=0, parse_dates=True)["Close"]
    else:
        ks = fdr.DataReader("KS11", "2015-01-01", "2026-12-31")["Close"].dropna()

    months = pd.date_range(START, END, freq="MS")
    positions: list[dict] = []   # {code, entry_price, entry_date, capital}
    cash = 1.0
    trades: list[dict] = []
    equity_curve: list[dict] = []
    prev_m = START

    for m in months:
        # 0) 그 시점 스크린 (통과집합 = 보유조건, 랭킹 = 진입후보)
        survivor_set, ranked = screen_at(codes, fins, shares_map, name_map,
                                         prices, m, sector_map)

        # 1) 청산 판정
        still = []
        for pos in positions:
            s = prices.get(pos["code"])
            if s is None:
                still.append(pos)
                continue
            window = s[(s.index > prev_m) & (s.index <= m)]
            px = vb.price_asof(s, m)
            exited = False
            if EXIT_MODE == "take_profit":
                tp_price = pos["entry_price"] * (1 + TP_PCT)
                if len(window) and float(window.max()) >= tp_price:
                    cash += pos["capital"] * (1 + TP_PCT)
                    trades.append({**_trade(pos, m, TP_PCT, "익절"),
                                   "exit_price": round(tp_price)})
                    exited = True
            else:  # value_normalization: 통과집합에서 빠지면(재평가/펀더악화) 매도
                if pos["code"] not in survivor_set and px:
                    r = px / pos["entry_price"] - 1
                    cash += pos["capital"] * (1 + r)
                    trades.append({**_trade(pos, m, r, "밸류정상화"), "exit_price": round(px)})
                    exited = True
            if not exited and (m - pos["entry_date"]).days >= MAX_HOLD_DAYS and px:
                r = px / pos["entry_price"] - 1
                cash += pos["capital"] * (1 + r)
                trades.append({**_trade(pos, m, r, "최대보유"), "exit_price": round(px)})
                exited = True
            if not exited and STOP_PCT is not None and len(window) and \
                    float(window.min()) <= pos["entry_price"] * (1 + STOP_PCT):
                cash += pos["capital"] * (1 + STOP_PCT)
                trades.append({**_trade(pos, m, STOP_PCT, "손절"),
                               "exit_price": round(pos["entry_price"] * (1 + STOP_PCT))})
                exited = True
            if not exited:
                still.append(pos)
        positions = still

        # 2) 빈 슬롯 채우기 (그 시점 스크리너 최상위 미보유 후보)
        n_free = N_SLOTS - len(positions)
        if n_free > 0 and cash > 1e-9:
            held = {p["code"] for p in positions}
            cands = [c for c in ranked if c not in held][:n_free]
            if cands:
                buy_cap = cash / len(cands)
                for c in cands:
                    px = vb.price_asof(prices[c], m)
                    if px and px > 0:
                        positions.append({"code": c, "name": name_map.get(c, c),
                                          "entry_price": px, "entry_date": m,
                                          "capital": buy_cap})
                        cash -= buy_cap

        # 3) 시가평가 (mark-to-market)
        mtm = 0.0
        for pos in positions:
            px = vb.price_asof(prices[pos["code"]], m)
            if px:
                mtm += pos["capital"] * (px / pos["entry_price"])
            else:
                mtm += pos["capital"]
        equity = cash + mtm
        equity_curve.append({"date": m.strftime("%Y-%m"), "equity": round(equity, 4),
                             "n_pos": len(positions), "cash": round(cash, 3)})
        prev_m = m

    _report(equity_curve, trades, ks, months, name_map)


def _trade(pos, exit_date, r, reason) -> dict:
    return {"code": pos["code"], "name": pos.get("name", pos["code"]),
            "entry": pos["entry_date"].strftime("%Y-%m-%d"),
            "exit": exit_date.strftime("%Y-%m-%d"),
            "hold_days": (exit_date - pos["entry_date"]).days,
            "return%": round(r * 100, 1), "reason": reason,
            "entry_price": round(pos["entry_price"])}


def _report(equity_curve, trades, ks, months, name_map) -> None:
    eq = np.array([e["equity"] for e in equity_curve])
    n_yr = (END - START).days / 365.25
    cagr = eq[-1] ** (1 / n_yr) - 1
    peak = np.maximum.accumulate(eq)
    mdd = float((eq / peak - 1).min())
    mret = pd.Series(eq).pct_change().dropna()
    sharpe = float(mret.mean() / mret.std() * np.sqrt(12)) if mret.std() > 0 else None

    ks0, ks1 = vb.price_asof(ks, START), vb.price_asof(ks, END)
    ks_ret = (ks1 / ks0 - 1) if (ks0 and ks1) else None
    ks_cagr = ((1 + ks_ret) ** (1 / n_yr) - 1) if ks_ret is not None else None

    wins = [t for t in trades if t["return%"] > 0]
    hold_avg = np.mean([t["hold_days"] for t in trades]) if trades else 0
    ret_avg = np.mean([t["return%"] for t in trades]) if trades else 0

    # 청산 사유 분포
    from collections import Counter
    reason_ct = Counter(t["reason"] for t in trades)

    # 연도별 진입(=매수 연도) 승률·수익률 breakdown
    yearly_entries = {}
    for t in trades:
        yr = t["entry"][:4]
        yearly_entries.setdefault(yr, []).append(t)
    yearly_summary = []
    for yr in sorted(yearly_entries):
        ts = yearly_entries[yr]
        rets = [t["return%"] for t in ts]
        w = sum(1 for r in rets if r > 0)
        yearly_summary.append({
            "entry_year": yr, "n": len(ts),
            "win_rate": f"{w}/{len(ts)}",
            "avg_return%": round(float(np.mean(rets)), 1),
            "median_return%": round(float(np.median(rets)), 1),
            "best": max(ts, key=lambda t: t["return%"])["name"][:12] + f" {max(rets):+.0f}%",
            "worst": min(ts, key=lambda t: t["return%"])["name"][:12] + f" {min(rets):+.0f}%",
        })

    result = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "params": {"N_SLOTS": N_SLOTS, "TP_PCT": TP_PCT, "MAX_HOLD_DAYS": MAX_HOLD_DAYS,
                   "STOP_PCT": STOP_PCT, "start": str(START.date()), "end": str(END.date())},
        "sector_relative": vs.SECTOR_RELATIVE, "holdco_mode": vs.HOLDCO_MODE,
        "summary": {
            "final_multiple": round(float(eq[-1]), 2),
            "CAGR%": round(cagr * 100, 1), "MDD%": round(mdd * 100, 1),
            "Sharpe": round(sharpe, 2) if sharpe else None,
            "KOSPI_CAGR%": round(ks_cagr * 100, 1) if ks_cagr is not None else None,
            "n_trades": len(trades),
            "trade_win_rate": f"{len(wins)}/{len(trades)}" if trades else "0/0",
            "exit_reasons": dict(reason_ct),
            "avg_hold_days": round(float(hold_avg)),
            "avg_trade_return%": round(float(ret_avg), 1),
        },
        "yearly_entries": yearly_summary,
        "trades": trades,
        "equity_curve": equity_curve,
        "disclosures": [
            "생존편향(현 상장·유동성 통과 종목만) 미제거 → 낙관 편의.",
            "익절=일봉 고가가 TP 도달 시 지정가 체결 가정(약간 낙관). 슬리피지·세금·수수료 미반영.",
            "월 1회 스크린. 과거 시총=과거종가×현재주식수. A(업종상대)·C(지주사) 적용.",
        ],
    }
    REPORTS.joinpath("value_screener_dynamic_backtest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    s = result["summary"]
    print("\n" + "=" * 72)
    print(f"동적 익절·로테이션 백테스트 — TP {TP_PCT:.0%} · 최대보유 {MAX_HOLD_DAYS}일 · {N_SLOTS}슬롯")
    print("=" * 72)
    print(f"기간 {START.date()} ~ {END.date()} ({n_yr:.1f}년) | A={vs.SECTOR_RELATIVE} C={vs.HOLDCO_MODE}\n")
    print(f"  최종배수 {s['final_multiple']}x | CAGR {s['CAGR%']}% | MDD {s['MDD%']}% | Sharpe {s['Sharpe']}")
    print(f"  KOSPI CAGR {s['KOSPI_CAGR%']}% (동기간)")
    print(f"  거래 {s['n_trades']}건 | 승률 {s['trade_win_rate']} "
          f"| 평균보유 {s['avg_hold_days']}일 | 평균수익 {s['avg_trade_return%']}%")
    print(f"  청산사유: {s['exit_reasons']}")

    # 연도별 진입 종목 승률·수익률 (사용자 요청: 년도별 쫙)
    print("\n■ 연도별 진입(매수) 종목 성과")
    print(f"  {'진입년':>6}{'건수':>5}{'승률':>8}{'평균%':>8}{'중앙%':>8}   최고 / 최악")
    for y in result["yearly_entries"]:
        print(f"  {y['entry_year']:>6}{y['n']:>5}{y['win_rate']:>8}"
              f"{y['avg_return%']:>8.1f}{y['median_return%']:>8.1f}   {y['best']} / {y['worst']}")

    # 큰 승자 (밸류정상화까지 달린 멀티배거)
    big = sorted(trades, key=lambda t: t["return%"], reverse=True)[:8]
    print("\n■ 최대 수익 종목 top 8 (밸류정상화까지 보유)")
    for t in big:
        print(f"  {t['name'][:14]:<14} {t['entry']}→{t['exit']} ({t['hold_days']:>4}일) "
              f"{t['return%']:+.0f}%  [{t['reason']}]")

    print("\n■ 한계·정직")
    for d in result["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/value_screener_dynamic_backtest.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True)
        traceback.print_exc()
        sys.exit(1)
