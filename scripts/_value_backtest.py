"""KOSPI200 밸류(가치) 전략 백테스트 — 재무 펀더멘털 기반 (기술적분석 아님).

산출:
  (a) point-in-time 백테스트 성과 (3방식: 종합스코어 / 마법공식 / F-Score) vs KOSPI
  (b) 현재 top-20 픽 (페이퍼용)

데이터:
  - DART fnlttSinglAcnt (재무): 연결(CFS) 우선, 없으면 OFS
  - FinanceDataReader: 주가·시총·주식수, KOSPI 지수(KS11)
  - 유니버스: src.universe.kospi200.get_codes (~197)

look-ahead 금지: 매년 5/1 리밸, 그 시점엔 FY(전년) 사업보고서(11011)가 공시(~3~4월)됨.
  FY(Y-1) 재무 + 5/1 Y 시총 → 팩터. 보유 5/1 Y ~ 4/30 Y+1.

근사·한계 (정직 disclosure):
  - 과거 시총 = 과거 종가 × 현재 주식수(고정). 액면분할/증자 오차 감수.
  - EV ≈ 시총 + 부채 (현금 데이터 없음). EBITDA ≈ 영업이익.
  - F-Score: 현금흐름 컴포넌트(CFO>0, CFO>순익) 결측 → 가용 7개 컴포넌트만. max=7, 고득점 임계는 7/9 스케일.
  - 생존편향: 현 KOSPI200 구성(살아남은 종목)만 사용. 과거 편입/퇴출 미반영 → 성과 상방 편의.
  - 캐시: data/cache/dart/, data/cache/fdr/. 재실행 시 재페치 skip.

실행: python scripts/_value_backtest.py   (리서치)
결과: stdout + reports/value_backtest.json
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.universe.kospi200 import get_codes  # noqa: E402

KEY = os.environ.get("DART_API_KEY", "5477d449aca29c2eb551a96ccb1bda540534eafd")
BASE = "https://opendart.fss.or.kr/api"

ROOT = Path(__file__).resolve().parents[1]
DART_CACHE = ROOT / "data" / "cache" / "dart"
FIN_CACHE = DART_CACHE / "fin"
FDR_CACHE = ROOT / "data" / "cache" / "fdr"
CORP_MAP_PATH = DART_CACHE / "corp_map.json"
REPORTS = ROOT / "reports"
for d in (FIN_CACHE, FDR_CACHE, REPORTS):
    d.mkdir(parents=True, exist_ok=True)

FIN_YEARS = list(range(2016, 2025))   # FY2016..FY2024
REBAL_YEARS = list(range(2018, 2026))  # 매년 5/1 매수 (2018..2025), 1년 보유
TOP_N = 20
COST_OF_EQUITY = 0.08  # 정당PBR용 자기자본비용 가정

# DART 주요계정 account_nm (정확 매칭)
ACC = {
    "revenue": "매출액",
    "op": "영업이익",
    "net": "당기순이익",
    "assets": "자산총계",
    "liab": "부채총계",
    "equity": "자본총계",
    "cur_assets": "유동자산",
    "cur_liab": "유동부채",
}


try:  # Windows 콘솔 cp949 → UTF-8 (한글·em-dash 출력)
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass


def log(msg: str) -> None:
    print(f"[value] {msg}", flush=True)


# --------------------------------------------------------------------------
# 1. corp_code 매핑
# --------------------------------------------------------------------------
def build_corp_map() -> dict[str, str]:
    if CORP_MAP_PATH.exists():
        return json.loads(CORP_MAP_PATH.read_text(encoding="utf-8"))
    log("corpCode.xml 다운로드...")
    r = requests.get(f"{BASE}/corpCode.xml", params={"crtfc_key": KEY}, timeout=60)
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))
    m: dict[str, str] = {}
    for lst in root.findall("list"):
        sc = (lst.findtext("stock_code") or "").strip()
        cc = (lst.findtext("corp_code") or "").strip()
        if sc and cc:
            m[sc] = cc
    CORP_MAP_PATH.write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    log(f"corp_map {len(m)}개 종목 캐시")
    return m


# --------------------------------------------------------------------------
# 2. DART 재무 페치 (캐시)
# --------------------------------------------------------------------------
def _to_int(s: str | None) -> int | None:
    if s is None:
        return None
    s = str(s).strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


def fetch_fin_raw(corp: str, year: int) -> dict | None:
    """단일 corp×year 원본 list 응답을 캐시. status!=000 → None 캐시."""
    cache = FIN_CACHE / f"{corp}_{year}.json"
    if cache.exists():
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        r = requests.get(
            f"{BASE}/fnlttSinglAcnt.json",
            params={"crtfc_key": KEY, "corp_code": corp, "bsns_year": str(year),
                    "reprt_code": "11011"},
            timeout=30,
        )
        d = r.json()
    except Exception as e:  # noqa: BLE001
        log(f"  fetch err {corp} {year}: {e}")
        return None
    if d.get("status") != "000":
        cache.write_text(json.dumps({"status": d.get("status")}, ensure_ascii=False),
                         encoding="utf-8")
        return None
    cache.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return d


def parse_fin(raw: dict | None) -> dict | None:
    """원본 → {revenue, op, net, assets, liab, equity, cur_assets, cur_liab} (CFS 우선)."""
    if not raw or "list" not in raw:
        return None
    # CFS 먼저, 그다음 OFS 로 결측 보충
    # 주의: 순이익 account_nm 은 '당기순이익(손실)' 등 접미사 변형 → startswith 매칭.
    #       나머지는 총계 계정이라 정확 매칭(부분일치 오검출 방지).
    out: dict[str, int | None] = {k: None for k in ACC}
    for pref in ("CFS", "OFS"):
        for it in raw["list"]:
            if it.get("fs_div") != pref:
                continue
            nm = it.get("account_nm", "").strip()
            for key, target in ACC.items():
                if out[key] is not None:
                    continue
                if key == "net":
                    if nm.startswith("당기순이익"):  # '당기순이익(손실)' 포함, 총포괄손익 제외
                        out[key] = _to_int(it.get("thstrm_amount"))
                elif nm == target:
                    out[key] = _to_int(it.get("thstrm_amount"))
    return out


def fetch_all_financials(corp_map: dict[str, str], codes: list[str]) -> dict:
    """{code: {year: parsed_fin}}"""
    fins: dict[str, dict[int, dict]] = {}
    total = len(codes)
    for i, code in enumerate(codes):
        corp = corp_map.get(code)
        if not corp:
            continue
        per_year = {}
        for y in FIN_YEARS:
            raw = fetch_fin_raw(corp, y)
            parsed = parse_fin(raw)
            if parsed:
                per_year[y] = parsed
            if not (FIN_CACHE / f"{corp}_{y}.json").exists():
                time.sleep(0.04)
        if per_year:
            fins[code] = per_year
        if (i + 1) % 25 == 0:
            log(f"재무 페치 {i + 1}/{total}")
    log(f"재무 확보 종목 {len(fins)}/{total}")
    return fins


# --------------------------------------------------------------------------
# 3. 주가·주식수 (FDR)
# --------------------------------------------------------------------------
def load_listing() -> pd.DataFrame:
    import FinanceDataReader as fdr
    df = fdr.StockListing("KOSPI")
    df = df[["Code", "Name", "Close", "Marcap", "Stocks"]].copy()
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    return df.set_index("Code")


def load_price(code: str) -> pd.Series | None:
    import FinanceDataReader as fdr
    cache = FDR_CACHE / f"{code}.csv"
    if cache.exists():
        s = pd.read_csv(cache, index_col=0, parse_dates=True)["Close"]
        return s if len(s) else None
    try:
        df = fdr.DataReader(code, "2015-01-01", "2026-12-31")
    except Exception as e:  # noqa: BLE001
        log(f"  price err {code}: {e}")
        return None
    if df is None or df.empty or "Close" not in df:
        pd.DataFrame({"Close": []}).to_csv(cache)
        return None
    s = df["Close"].dropna()
    s.to_frame("Close").to_csv(cache)
    return s if len(s) else None


def load_all_prices(codes: list[str]) -> dict[str, pd.Series]:
    prices = {}
    for i, code in enumerate(codes):
        s = load_price(code)
        if s is not None and len(s):
            prices[code] = s
        if (i + 1) % 50 == 0:
            log(f"주가 로드 {i + 1}/{len(codes)}")
    log(f"주가 확보 종목 {len(prices)}/{len(codes)}")
    return prices


def price_asof(s: pd.Series, dt: pd.Timestamp) -> float | None:
    """dt 이하 최근 종가. dt 이전 데이터 없으면 None."""
    v = s.asof(dt)
    if pd.isna(v):
        return None
    return float(v)


# --------------------------------------------------------------------------
# 4. 팩터 계산 (리밸 시점)
# --------------------------------------------------------------------------
def safe_div(a, b):
    if a is None or b is None or b == 0:
        return None
    return a / b


def build_factor_row(code: str, name: str, shares: float,
                     fin_y1: dict, fin_y2: dict | None, mktcap: float) -> dict | None:
    """FY(Y-1)=fin_y1, FY(Y-2)=fin_y2 (성장·ΔF-score용), mktcap=리밸일 시총."""
    rev = fin_y1.get("revenue")
    op = fin_y1.get("op")
    net = fin_y1.get("net")
    assets = fin_y1.get("assets")
    liab = fin_y1.get("liab")
    eq = fin_y1.get("equity")
    ca = fin_y1.get("cur_assets")
    cl = fin_y1.get("cur_liab")
    # 금융업(매출액 없음) 제외
    if rev is None or eq is None or assets is None or net is None:
        return None
    if eq <= 0 or assets <= 0:
        return None

    per = safe_div(mktcap, net) if net and net > 0 else None
    pbr = safe_div(mktcap, eq)
    psr = safe_div(mktcap, rev) if rev and rev > 0 else None
    ev = mktcap + (liab or 0)  # 현금無 → EV≈시총+부채
    ev_ebitda = safe_div(ev, op) if op and op > 0 else None
    roe = safe_div(net, eq)
    net_margin = safe_div(net, rev)
    debt_ratio = safe_div(liab, eq)  # 부채비율 (부채/자본)
    roa = safe_div(net, assets)
    asset_turn = safe_div(rev, assets)
    cur_ratio = safe_div(ca, cl) if ca is not None and cl not in (None, 0) else None

    # 성장 (YoY, FY(Y-2) 필요)
    rev_g = net_g = None
    roa_prev = dr_prev = at_prev = nm_prev = cr_prev = None
    if fin_y2:
        rev2, net2, ass2, eq2, liab2 = (fin_y2.get("revenue"), fin_y2.get("net"),
                                        fin_y2.get("assets"), fin_y2.get("equity"),
                                        fin_y2.get("liab"))
        ca2, cl2 = fin_y2.get("cur_assets"), fin_y2.get("cur_liab")
        if rev2 and rev2 > 0:
            rev_g = (rev - rev2) / abs(rev2)
        if net2 and net2 != 0:
            net_g = (net - net2) / abs(net2)
        roa_prev = safe_div(net2, ass2)
        dr_prev = safe_div(liab2, eq2)
        at_prev = safe_div(rev2, ass2)
        nm_prev = safe_div(net2, rev2) if rev2 else None
        cr_prev = safe_div(ca2, cl2) if ca2 is not None and cl2 not in (None, 0) else None

    # F-Score 가용 컴포넌트 (max 7; CFO 2종·신주발행·매출총이익률 결측)
    fcomp = {}
    fcomp["roa_pos"] = 1 if (roa is not None and roa > 0) else 0
    fcomp["d_roa"] = 1 if (roa is not None and roa_prev is not None and roa > roa_prev) else 0
    fcomp["net_pos"] = 1 if (net is not None and net > 0) else 0
    fcomp["d_leverage"] = 1 if (debt_ratio is not None and dr_prev is not None and debt_ratio < dr_prev) else 0
    fcomp["d_current"] = 1 if (cur_ratio is not None and cr_prev is not None and cur_ratio > cr_prev) else 0
    fcomp["d_margin"] = 1 if (net_margin is not None and nm_prev is not None and net_margin > nm_prev) else 0
    fcomp["d_turnover"] = 1 if (asset_turn is not None and at_prev is not None and asset_turn > at_prev) else 0
    fscore = sum(fcomp.values())

    # 적정주가 (Graham, 정당PBR)
    eps = safe_div(net, shares)
    bps = safe_div(eq, shares)
    graham = None
    if eps and bps and eps > 0 and bps > 0:
        graham = (22.5 * eps * bps) ** 0.5
    fair_pbr = safe_div(roe, COST_OF_EQUITY) if roe is not None else None  # 정당PBR

    # 마법공식
    earnings_yield = safe_div(op, ev) if op is not None else None  # 영업이익/EV
    roc = safe_div(op, (assets - (liab or 0))) if op is not None else None  # 영업이익/(자산-부채)

    return {
        "code": code, "name": name, "mktcap": mktcap, "shares": shares,
        "revenue": rev, "op": op, "net": net, "assets": assets, "liab": liab, "equity": eq,
        "PER": per, "PBR": pbr, "PSR": psr, "EV_EBITDA": ev_ebitda,
        "ROE": roe, "net_margin": net_margin, "debt_ratio": debt_ratio, "ROA": roa,
        "rev_growth": rev_g, "net_growth": net_g,
        "graham": graham, "fair_pbr": fair_pbr, "eps": eps, "bps": bps,
        "earnings_yield": earnings_yield, "roc": roc,
        "fscore": fscore,
    }


# --------------------------------------------------------------------------
# 5. 스코어링
# --------------------------------------------------------------------------
def add_scores(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    def pct_low(col):  # 낮을수록 좋음 → 높은 점수
        v = df[col]
        r = v.rank(pct=True, ascending=True)  # 낮은값=낮은 rank
        return 1.0 - r  # 낮은값 → 높은 점수

    def pct_high(col):  # 높을수록 좋음
        return df[col].rank(pct=True, ascending=True)

    # 저평가 (양수만 유효, 결측/음수는 중립 0.5 아님 → NaN 제외 rank)
    val_cols = {"PER": pct_low, "PBR": pct_low, "PSR": pct_low, "EV_EBITDA": pct_low}
    qual = {"ROE": pct_high, "net_margin": pct_high, "debt_ratio": pct_low,
            "rev_growth": pct_high, "net_growth": pct_high}

    value_score = pd.DataFrame({c: f(c) for c, f in val_cols.items()}).mean(axis=1, skipna=True)
    qual_score = pd.DataFrame({c: f(c) for c, f in qual.items()}).mean(axis=1, skipna=True)
    df["value_score"] = value_score
    df["quality_score"] = qual_score
    # 종합: 저평가 60% + 퀄리티 40%
    df["composite"] = 0.6 * value_score.fillna(0) + 0.4 * qual_score.fillna(0)

    # 마법공식: 이익수익률↑, ROC↑ 각 랭크 합 (작을수록 좋게 하기 위해 순위 오름차 합)
    ey_rank = df["earnings_yield"].rank(ascending=False)  # 1=최고
    roc_rank = df["roc"].rank(ascending=False)
    df["magic_rank"] = ey_rank + roc_rank
    return df


def trap_filter(row) -> bool:
    """밸류트랩 배제: 순익<0 OR 매출역성장 OR 부채비율>300% → True=제외."""
    if row["net"] is not None and row["net"] < 0:
        return True
    if row["rev_growth"] is not None and row["rev_growth"] < 0:
        return True
    if row["debt_ratio"] is not None and row["debt_ratio"] > 3.0:
        return True
    return False


def pick_composite(df: pd.DataFrame) -> list[str]:
    d = df[~df.apply(trap_filter, axis=1)].copy()
    d = d[d["composite"] > 0]
    d = d.sort_values("composite", ascending=False)
    return d.head(TOP_N)["code"].tolist()


def pick_magic(df: pd.DataFrame) -> list[str]:
    # 마법공식: 영업이익>0 요구 (EY/ROC 양수)
    d = df[(df["op"].notna()) & (df["op"] > 0) & (df["magic_rank"].notna())].copy()
    d = d.sort_values("magic_rank", ascending=True)
    return d.head(TOP_N)["code"].tolist()


def pick_fscore(df: pd.DataFrame, threshold: int) -> list[str]:
    d = df[df["fscore"] >= threshold].copy()
    # 동일 임계 다수면 저PBR 우선
    d = d.sort_values(["fscore", "PBR"], ascending=[False, True])
    return d.head(TOP_N)["code"].tolist()


# --------------------------------------------------------------------------
# 6. 백테스트
# --------------------------------------------------------------------------
def forward_return(prices: dict, code: str, buy: pd.Timestamp, sell: pd.Timestamp):
    s = prices.get(code)
    if s is None:
        return None
    p0 = price_asof(s, buy)
    p1 = price_asof(s, sell)
    if p0 is None or p1 is None or p0 <= 0:
        return None
    return p1 / p0 - 1.0


def kospi_year_return(ks: pd.Series, buy: pd.Timestamp, sell: pd.Timestamp):
    p0 = price_asof(ks, buy)
    p1 = price_asof(ks, sell)
    if p0 is None or p1 is None:
        return None
    return p1 / p0 - 1.0


def equity_curve_stats(yearly: list[float]) -> dict:
    """연수익률 리스트 → CAGR, MDD, Sharpe(연수익 기반)."""
    if not yearly:
        return {"CAGR": None, "MDD": None, "Sharpe": None, "years": 0}
    eq = np.cumprod([1.0 + r for r in yearly])
    n = len(yearly)
    cagr = eq[-1] ** (1.0 / n) - 1.0
    peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
    dd = np.concatenate([[1.0], eq]) / peak - 1.0
    mdd = float(dd.min())
    arr = np.array(yearly)
    sharpe = float(arr.mean() / arr.std(ddof=1)) if n > 1 and arr.std(ddof=1) > 0 else None
    return {"CAGR": float(cagr), "MDD": mdd, "Sharpe": sharpe, "years": n,
            "final_multiple": float(eq[-1])}


def run_backtest(method: str, picks_by_year: dict[int, list[str]],
                 prices: dict, ks: pd.Series) -> dict:
    rows = []
    port_yearly, bench_yearly = [], []
    for y in REBAL_YEARS:
        buy = pd.Timestamp(f"{y}-05-01")
        sell = pd.Timestamp(f"{y + 1}-05-01")
        picks = picks_by_year.get(y, [])
        rets = [forward_return(prices, c, buy, sell) for c in picks]
        rets = [r for r in rets if r is not None]
        pr = float(np.mean(rets)) if rets else None
        br = kospi_year_return(ks, buy, sell)
        rows.append({"year": y, "n_picks": len(picks), "n_priced": len(rets),
                     "port_return": pr, "kospi_return": br,
                     "excess": (pr - br) if (pr is not None and br is not None) else None})
        if pr is not None and br is not None:
            port_yearly.append(pr)
            bench_yearly.append(br)
    port_stats = equity_curve_stats(port_yearly)
    bench_stats = equity_curve_stats(bench_yearly)
    excesses = [r["excess"] for r in rows if r["excess"] is not None]
    win_years = sum(1 for e in excesses if e > 0)
    return {
        "method": method,
        "yearly": rows,
        "port_stats": port_stats,
        "bench_stats": bench_stats,
        "avg_excess": float(np.mean(excesses)) if excesses else None,
        "win_years": win_years,
        "total_years": len(excesses),
    }


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main() -> None:
    codes = get_codes()
    log(f"유니버스 {len(codes)} 종목")
    corp_map = build_corp_map()

    listing = load_listing()
    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in listing.index if c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in listing.index if c in codes}
    cur_close = {c: float(listing.loc[c, "Close"]) for c in listing.index if c in codes}
    cur_marcap = {c: float(listing.loc[c, "Marcap"]) for c in listing.index if c in codes}

    fins = fetch_all_financials(corp_map, codes)
    prices = load_all_prices(codes)

    import FinanceDataReader as fdr
    ks_cache = FDR_CACHE / "KS11.csv"
    if ks_cache.exists():
        ks = pd.read_csv(ks_cache, index_col=0, parse_dates=True)["Close"]
    else:
        ks = fdr.DataReader("KS11", "2015-01-01", "2026-12-31")["Close"].dropna()
        ks.to_frame("Close").to_csv(ks_cache)

    # 각 리밸 연도 팩터 테이블 + 3방식 픽
    comp_picks, magic_picks, fscore_picks = {}, {}, {}
    fscore_max = 7
    fscore_threshold = int(round(7 / 9 * fscore_max))  # =5 → ≥5 of 7 (표준 ≥7 of 9 스케일)
    year_universe_counts = {}

    for y in REBAL_YEARS:
        fy1, fy2 = y - 1, y - 2
        buy = pd.Timestamp(f"{y}-05-01")
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
            p = price_asof(s, buy)
            if p is None:
                continue
            mktcap = p * shares_map[code]
            row = build_factor_row(code, name_map.get(code, code), shares_map[code],
                                   f1, f2, mktcap)
            if row:
                rows.append(row)
        if not rows:
            continue
        df = add_scores(pd.DataFrame(rows))
        year_universe_counts[y] = len(df)
        comp_picks[y] = pick_composite(df)
        magic_picks[y] = pick_magic(df)
        fscore_picks[y] = pick_fscore(df, fscore_threshold)

    bt_comp = run_backtest("composite", comp_picks, prices, ks)
    bt_magic = run_backtest("magic_formula", magic_picks, prices, ks)
    bt_fscore = run_backtest(f"fscore_ge_{fscore_threshold}", fscore_picks, prices, ks)

    # 7. 현재 픽 (최신 FY = 2024, 오늘 시총)
    latest_fy = max(FIN_YEARS)
    cur_rows = []
    for code in codes:
        if code not in fins or code not in shares_map:
            continue
        f1 = fins[code].get(latest_fy)
        f2 = fins[code].get(latest_fy - 1)
        if not f1:
            continue
        mktcap = cur_marcap.get(code) or (cur_close.get(code, 0) * shares_map[code])
        if not mktcap:
            continue
        row = build_factor_row(code, name_map.get(code, code), shares_map[code], f1, f2, mktcap)
        if row:
            cur_rows.append(row)
    cur_df = add_scores(pd.DataFrame(cur_rows))
    cur_top = cur_df[~cur_df.apply(trap_filter, axis=1)].copy()
    cur_top = cur_top[cur_top["composite"] > 0].sort_values("composite", ascending=False).head(TOP_N)

    def _pick_view(df_top):
        out = []
        for _, r in df_top.iterrows():
            out.append({
                "code": r["code"], "name": r["name"],
                "PER": round(r["PER"], 2) if pd.notna(r["PER"]) else None,
                "PBR": round(r["PBR"], 2) if pd.notna(r["PBR"]) else None,
                "PSR": round(r["PSR"], 2) if pd.notna(r["PSR"]) else None,
                "ROE": round(r["ROE"] * 100, 1) if pd.notna(r["ROE"]) else None,
                "debt_ratio_pct": round(r["debt_ratio"] * 100, 0) if pd.notna(r["debt_ratio"]) else None,
                "fscore": int(r["fscore"]),
                "composite": round(r["composite"], 4),
            })
        return out

    result = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "universe_size": len(codes),
        "financials_covered": len(fins),
        "prices_covered": len(prices),
        "fin_years": FIN_YEARS,
        "rebal_years": REBAL_YEARS,
        "top_n": TOP_N,
        "fscore_max": fscore_max,
        "fscore_threshold": fscore_threshold,
        "year_universe_counts": year_universe_counts,
        "backtests": {"composite": bt_comp, "magic_formula": bt_magic, "fscore": bt_fscore},
        "current_top20_composite": _pick_view(cur_top),
        "current_universe_valid": len(cur_df),
        "disclosures": [
            "과거 시총 = 과거 종가 × 현재 주식수(고정) — 액면분할/증자 오차 감수",
            "EV ≈ 시총 + 부채 (현금 데이터 없음), EBITDA ≈ 영업이익",
            f"F-Score 가용 {fscore_max}개 컴포넌트만(CFO 2종·신주발행·매출총이익률 결측), 임계 ≥{fscore_threshold}",
            "생존편향: 현 KOSPI200 구성만 사용, 과거 편입/퇴출 미반영 → 성과 상방 편의",
            "금융업(매출액 없음)은 자동 제외",
            "Sharpe = 연수익률 기반(표본 8), 소표본 주의",
        ],
    }

    REPORTS.joinpath("value_backtest.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # ----- stdout 리포트 -----
    print("\n" + "=" * 72)
    print("KOSPI200 밸류(펀더멘털) 백테스트 — 3방식 vs KOSPI")
    print("=" * 72)
    print(f"유니버스 {len(codes)} | 재무확보 {len(fins)} | 주가확보 {len(prices)} | 연리밸 {REBAL_YEARS[0]}~{REBAL_YEARS[-1]} 5/1")

    for tag, bt in (("종합스코어", bt_comp), ("마법공식", bt_magic),
                    (f"F-Score≥{fscore_threshold}", bt_fscore)):
        ps, bs = bt["port_stats"], bt["bench_stats"]
        print(f"\n■ {tag}")
        print(f"  {'연도':>6} {'포트%':>9} {'KOSPI%':>9} {'초과%':>9} {'픽수':>5}")
        for r in bt["yearly"]:
            pr = f"{r['port_return']*100:7.1f}" if r["port_return"] is not None else "    n/a"
            br = f"{r['kospi_return']*100:7.1f}" if r["kospi_return"] is not None else "    n/a"
            ex = f"{r['excess']*100:7.1f}" if r["excess"] is not None else "    n/a"
            print(f"  {r['year']:>6} {pr:>9} {br:>9} {ex:>9} {r['n_priced']:>5}")
        cagr = f"{ps['CAGR']*100:.1f}%" if ps["CAGR"] is not None else "n/a"
        mdd = f"{ps['MDD']*100:.1f}%" if ps["MDD"] is not None else "n/a"
        shp = f"{ps['Sharpe']:.2f}" if ps["Sharpe"] is not None else "n/a"
        kcagr = f"{bs['CAGR']*100:.1f}%" if bs["CAGR"] is not None else "n/a"
        avgex = f"{bt['avg_excess']*100:.1f}%" if bt["avg_excess"] is not None else "n/a"
        print(f"  → CAGR {cagr} | MDD {mdd} | Sharpe {shp} | KOSPI CAGR {kcagr} "
              f"| 연평균초과 {avgex} | 초과승 {bt['win_years']}/{bt['total_years']}")

    print(f"\n■ 현재 top-20 (종합스코어, 최신 FY{latest_fy} + 오늘 시총)")
    print(f"  {'종목':<14} {'PER':>7} {'PBR':>6} {'ROE%':>7} {'부채%':>7} {'Fscore':>7} {'score':>7}")
    for p in result["current_top20_composite"]:
        per = f"{p['PER']:.1f}" if p["PER"] is not None else "  -"
        pbr = f"{p['PBR']:.2f}" if p["PBR"] is not None else "  -"
        roe = f"{p['ROE']:.1f}" if p["ROE"] is not None else "  -"
        dr = f"{p['debt_ratio_pct']:.0f}" if p["debt_ratio_pct"] is not None else "  -"
        print(f"  {p['name'][:14]:<14} {per:>7} {pbr:>6} {roe:>7} {dr:>7} {p['fscore']:>7} {p['composite']:>7.3f}")

    print("\n■ 한계·정직")
    for d in result["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/value_backtest.json")


if __name__ == "__main__":
    main()
