"""저평가·우량주 스크리너 — "싸면서 안 망하고 실적 좋은" 종목 자동 선별.

관점: 수익률/승률 최적화가 아니라, 가치투자 원칙에 부합하는 종목을 다단계
게이트로 걸러낸다. 단순 저PER 이 아니라 [싸다 × 재무탄탄 × 실적양호 × 안전마진]
4조건을 모두 만족하는 종목만 통과.

퍼널 (funnel):
  Stage 0  유효성    : 재무 존재 · 비금융(매출 존재) · 자본잠식 아님(equity>0)
  Stage 1  안전성    : 흑자(순익>0 & 영업익>0) · 부채비율<150% · 유동비율>1.0 · F-Score>=5
  Stage 2  실적/퀄리티: ROE>=8% · 순이익률>0 · 매출 역성장 아님
  Stage 3  저평가    : 0<PER<=15 · PBR<=1.5
  Stage 4  안전마진  : 현재가 < 그레이엄 적정가 (상승여력>0)  [기본 ON, 랭킹에도 반영]

생존자 랭킹: value_score(저평가 백분위) 50% + quality_score(수익성·성장·건전성) 50%.
각 종목 스코어카드로 "왜 뽑혔나"(싸다/탄탄/실적/안전마진) 근거 표시.

데이터: _value_backtest.py 의 DART 재무 캐시 + FDR 현재 시총 재사용 (현재 스냅샷만).
유니버스: src.universe.kospi200 (~197). 확장(KOSDAQ)은 후속 — DART 재무 대량 페치 필요.

한계 (정직):
  - EV≈시총+부채, EBITDA≈영업이익. F-Score 7/9 컴포넌트(CFO 결측).
  - 부채비율·유동비율 절대 임계는 업종 편차 무시 — 조선/유통 등 구조적 고부채 배제될 수 있음.
  - 그레이엄 적정가는 순익·순자산 기반 보수적 근사(성장주엔 과소평가).
  - 이 스크리너는 "후보 발굴" 도구. 매수 결정은 사업내용·촉매·업종 정성판단 병행 필요.

실행: python scripts/_value_screener.py   (리서치 산출물 산출물)
결과: stdout 퍼널·스코어카드 + reports/value_screener.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _value_backtest as vb  # noqa: E402  (DART/FDR 파이프라인 재사용)

try:  # Windows 콘솔 cp949 → UTF-8
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
REPORTS.mkdir(parents=True, exist_ok=True)
HISTORY_PATH = REPORTS / "value_screener_history.jsonl"  # 주간 스냅샷 적립

LATEST_FY = max(vb.FIN_YEARS)  # 2024

# ---- 유니버스 설정 -----------------------------------------------------------
UNIVERSE = "all"          # "all"=KOSPI+KOSDAQ 전체, "kospi200"=기존 정적 리스트
MARCAP_FLOOR = 100e8      # 시총 하한 100억 (잡주·shell 배제)
AMOUNT_FLOOR = 2e8        # 일 거래대금 하한 2억 (유동성 — 실제 매수가능성)

# ---- A안: 업종상대 임계 / C안: 지주사 보정 -----------------------------------
SECTOR_RELATIVE = True     # A: PER/PBR/부채 게이트를 max(절대임계, 업종중앙값)으로 완화
HOLDCO_MODE = "discount"   # C: "discount"(NAV할인) | "exclude"(제외) | "off"
HOLDCO_DISCOUNT = 0.5      # 지주사 순자산 할인율 (한국 지주사 구조적 NAV 할인 ~40-60%)
SECTOR_MAP_CACHE = ROOT / "data" / "cache" / "fdr" / "sector_map.json"

# ---- 게이트 임계 (튜닝 가능) --------------------------------------------------
GATE = {
    # 안전성 (안 망한다)
    "max_debt_ratio": 1.5,      # 부채비율(부채/자본) 150% 상한
    "min_current_ratio": 1.0,   # 유동비율(유동자산/유동부채) 100%
    "min_fscore": 5,            # 피오트로스키 F-Score (가용 7 스케일 중 >=5)
    # 실적/퀄리티 (실적 좋다)
    "min_roe": 0.08,            # ROE 8%
    "min_rev_growth": -0.05,    # 매출 역성장 -5% 까지만 허용 (경기민감 완충)
    # 저평가 (싸다)
    "max_per": 15.0,            # PER 상한
    "max_pbr": 1.5,             # PBR 상한
    # 안전마진 (상승 예측)
    "require_margin_of_safety": True,  # 현재가 < 그레이엄 적정가 요구
}
TOP_N = 25


def log(msg: str) -> None:
    print(f"[screener] {msg}", flush=True)


# ---- A/C: 업종 분류 + 지주사 감지 --------------------------------------------
# KSIC industry 문자열(KRX-DESC Industry) → 광의 섹터. 순서대로 첫 매칭.
_SECTOR_KEYWORDS = [
    ("건설", ["건설", "건물", "토목", "엔지니어링", "건축기술"]),
    ("조선", ["선박", "보트"]),
    ("운송", ["운송", "항공", "해상", "물류", "택배", "운수"]),
    ("반도체전자", ["반도체", "전자", "통신", "방송 장비", "디스플레이", "영상", "음향",
                "전동기", "발전기", "전기 변환", "제어 장치", "정밀기기", "측정", "시험",
                "컴퓨터", "주변장치", "가정용 기기", "일차전지", "이차전지", "전지", "광학"]),
    ("자동차", ["자동차", "차량", "운송장비"]),
    ("철강금속", ["철강", "1차 금속", "제철", "비철", "금속 가공", "금속제품", "구조용 금속"]),
    ("화학", ["화학", "석유", "고무", "플라스틱", "정유"]),
    ("바이오제약", ["의약", "의료", "바이오", "생물"]),
    ("금융지주", ["금융", "은행", "보험", "증권", "지주", "신탁", "집합투자"]),
    ("유통소비", ["유통", "소매", "도매", "음식", "식료품", "식품", "음료", "의복", "화장품",
               "섬유", "가구", "사료"]),
    ("기계장비", ["기계", "장비"]),
    ("건자재", ["시멘트", "요업", "비금속", "콘크리트"]),
    ("IT서비스", ["소프트웨어", "정보서비스", "정보 서비스", "게임", "인터넷", "출판",
               "컴퓨터 프로그래밍", "영화", "비디오", "방송프로그램", "방송업", "광고"]),
]
# 이름에 '홀딩스/지주' 없는 순수 지주사 (KSIC '기타 금융업'으로 분류됨).
_HOLDCO_NAMES = {"SK", "LG", "GS", "CJ", "LS", "한화", "두산", "한진칼",
                 "HD현대", "SK디스커버리", "삼성물산", "농심홀딩스"}


def map_industry_to_sector(industry) -> str:
    s = str(industry or "")
    for sector, kws in _SECTOR_KEYWORDS:
        if any(k in s for k in kws):
            return sector
    return "기타"


def is_holdco(name, industry) -> bool:
    n = str(name or "")
    if "홀딩스" in n or "지주" in n:
        return True
    if n in _HOLDCO_NAMES:
        return True
    # KSIC '기타 금융업' + 이름에 홀딩스 없으면 애매 → 순수지주 셋에서만 인정(위)
    return False


def get_sector_map() -> dict[str, dict]:
    """{code: {sector, industry, is_holdco}}. KRX-DESC 1회 페치 후 캐시."""
    if SECTOR_MAP_CACHE.exists():
        return json.loads(SECTOR_MAP_CACHE.read_text(encoding="utf-8"))
    import FinanceDataReader as fdr
    df = fdr.StockListing("KRX-DESC")
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        ind = r.get("Industry")
        out[r["Code"]] = {
            "sector": map_industry_to_sector(ind),
            "industry": str(ind) if pd.notna(ind) else None,
            "is_holdco": is_holdco(r.get("Name"), ind),
        }
    SECTOR_MAP_CACHE.parent.mkdir(parents=True, exist_ok=True)
    SECTOR_MAP_CACHE.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    log(f"섹터맵 {len(out)}종목 캐시 (KRX-DESC)")
    return out


def apply_holdco_adjustment(row: dict) -> None:
    """C안: 지주사면 순자산 NAV 할인 → PBR·graham·안전마진 뻥튀기 제거 (in-place)."""
    if HOLDCO_MODE == "off" or not row.get("is_holdco"):
        return
    d = HOLDCO_DISCOUNT
    # 순자산 d 만큼 할인 → PBR 상향(덜 싸게), graham 하향(적정가 낮게)
    if row.get("PBR") is not None:
        row["PBR"] = row["PBR"] / (1.0 - d)
    if row.get("graham") is not None:
        row["graham"] = row["graham"] * (1.0 - d)
        price = row.get("price")
        if price and price > 0:
            row["margin_of_safety"] = row["graham"] / price - 1.0
    if row.get("fair_pbr") is not None:
        row["fair_pbr"] = row["fair_pbr"] * (1.0 - d)


# ---- 유니버스 로딩 -----------------------------------------------------------
def load_full_listing() -> pd.DataFrame:
    """KOSPI+KOSDAQ 전체 상장 → 시총·거래대금 하한 필터. 인덱스=6자리 code."""
    import FinanceDataReader as fdr
    frames = []
    for mkt in ("KOSPI", "KOSDAQ"):
        d = fdr.StockListing(mkt)
        d = d[["Code", "Name", "Close", "Marcap", "Stocks", "Amount"]].copy()
        d["Market"] = mkt
        frames.append(d)
    df = pd.concat(frames, ignore_index=True)
    df["Code"] = df["Code"].astype(str).str.zfill(6)
    df = df.dropna(subset=["Marcap", "Stocks"])
    # 우선주(코드 끝자리 0 아님)·잡주 배제 + 시총·거래대금 하한
    df = df[df["Code"].str.endswith("0")]
    before = len(df)
    df = df[(df["Marcap"] >= MARCAP_FLOOR) & (df["Amount"].fillna(0) >= AMOUNT_FLOOR)]
    log(f"전체상장 {before} → 시총>={MARCAP_FLOOR/1e8:.0f}억 & 거래대금>={AMOUNT_FLOOR/1e8:.0f}억 필터 후 {len(df)}")
    return df.drop_duplicates("Code").set_index("Code")


def fetch_snapshot_financials(corp_map: dict, codes: list[str]) -> dict:
    """스냅샷용: 최신 2년(LATEST_FY, LATEST_FY-1)만 페치 (9년 백테스트와 달리 경량)."""
    import time
    years = [LATEST_FY, LATEST_FY - 1]
    fins: dict[str, dict[int, dict]] = {}
    total = len(codes)
    for i, code in enumerate(codes):
        corp = corp_map.get(code)
        if not corp:
            continue
        per_year = {}
        for y in years:
            cache = vb.FIN_CACHE / f"{corp}_{y}.json"
            existed = cache.exists()
            raw = vb.fetch_fin_raw(corp, y)
            parsed = vb.parse_fin(raw)
            if parsed:
                per_year[y] = parsed
            if not existed:
                time.sleep(0.04)
        if per_year:
            fins[code] = per_year
        if (i + 1) % 100 == 0:
            log(f"재무 스냅샷 페치 {i + 1}/{total}")
    log(f"재무 확보 {len(fins)}/{total}")
    return fins


def extra_safety(fin_y1: dict) -> dict:
    """build_factor_row 가 반환 안 하는 안전성 지표 보강."""
    ca, cl = fin_y1.get("cur_assets"), fin_y1.get("cur_liab")
    op, rev = fin_y1.get("op"), fin_y1.get("revenue")
    current_ratio = (ca / cl) if (ca is not None and cl not in (None, 0)) else None
    op_margin = (op / rev) if (op is not None and rev not in (None, 0)) else None
    return {"current_ratio": current_ratio, "op_margin": op_margin}


def build_rows(codes, fins, shares_map, name_map, marcap_map, close_map,
               amount_map=None, sector_map=None):
    """현재 스냅샷 팩터 테이블 (FY2024 재무 + 오늘 시총)."""
    amount_map = amount_map or {}
    sector_map = sector_map or {}
    rows = []
    for code in codes:
        if code not in fins or code not in shares_map:
            continue
        f1 = fins[code].get(LATEST_FY)
        f2 = fins[code].get(LATEST_FY - 1)
        if not f1:
            continue
        mktcap = marcap_map.get(code) or (close_map.get(code, 0) * shares_map[code])
        if not mktcap:
            continue
        row = vb.build_factor_row(code, name_map.get(code, code), shares_map[code],
                                  f1, f2, mktcap)
        if not row:
            continue
        row.update(extra_safety(f1))
        # 안전마진: 그레이엄 적정가 / 현재 주가 - 1  (>0 이면 저평가·상승여력)
        price = mktcap / shares_map[code] if shares_map[code] else None
        mos = None
        if row.get("graham") and price and price > 0:
            mos = row["graham"] / price - 1.0
        row["price"] = price
        row["margin_of_safety"] = mos
        row["amount"] = amount_map.get(code)  # 일 거래대금
        sm = sector_map.get(code, {})
        row["sector"] = sm.get("sector", "기타")
        row["is_holdco"] = bool(sm.get("is_holdco", False))
        apply_holdco_adjustment(row)  # C안: 지주사 NAV 할인 (PBR·graham·안전마진 보정)
        rows.append(row)
    return pd.DataFrame(rows)


# ---- A안: 업종상대 임계 계산 --------------------------------------------------
def attach_sector_thresholds(df: pd.DataFrame) -> pd.DataFrame:
    """섹터별 PER/PBR/부채 중앙값을 각 행에 부여 (게이트를 max(절대, 업종중앙값)으로 완화)."""
    if not SECTOR_RELATIVE or "sector" not in df.columns or df.empty:
        return df
    df = df.copy()
    for col, out in [("PER", "sec_med_per"), ("PBR", "sec_med_pbr"),
                     ("debt_ratio", "sec_med_debt")]:
        valid = df[df[col] > 0] if col == "PER" else df
        med = valid.groupby("sector")[col].median()
        df[out] = df["sector"].map(med)
    return df


def _eff_thresh(r, abs_key: str, sec_key: str) -> float:
    """업종상대 유효 임계 = max(절대임계, 업종중앙값). SECTOR_RELATIVE off 시 절대만."""
    base = GATE[abs_key]
    if SECTOR_RELATIVE:
        sm = r.get(sec_key)
        if sm is not None and pd.notna(sm):
            return max(base, float(sm))
    return base


# ---- 게이트 판정 (사유 기록) --------------------------------------------------
def gate_stage1_safety(r) -> list[str]:
    """안전성 위반 사유 리스트 (빈 리스트 = 통과)."""
    fails = []
    if not (r["net"] is not None and r["net"] > 0):
        fails.append("적자(순익<=0)")
    if not (r["op"] is not None and r["op"] > 0):
        fails.append("영업적자")
    dr = r.get("debt_ratio")
    debt_cap = _eff_thresh(r, "max_debt_ratio", "sec_med_debt")  # A: 업종 고부채 허용
    if dr is None or dr > debt_cap:
        fails.append(f"부채비율>{debt_cap*100:.0f}%")
    cr = r.get("current_ratio")
    if cr is None or cr < GATE["min_current_ratio"]:
        fails.append("유동비율<100%")
    if not (r.get("fscore") is not None and r["fscore"] >= GATE["min_fscore"]):
        fails.append(f"F-Score<{GATE['min_fscore']}")
    return fails


def gate_stage2_quality(r) -> list[str]:
    fails = []
    roe = r.get("ROE")
    if roe is None or roe < GATE["min_roe"]:
        fails.append(f"ROE<{GATE['min_roe']*100:.0f}%")
    nm = r.get("net_margin")
    if nm is None or nm <= 0:
        fails.append("순이익률<=0")
    rg = r.get("rev_growth")
    if rg is not None and rg < GATE["min_rev_growth"]:
        fails.append("매출역성장")
    return fails


def gate_stage3_value(r) -> list[str]:
    fails = []
    per_cap = _eff_thresh(r, "max_per", "sec_med_per")   # A: 업종 고PER 허용
    pbr_cap = _eff_thresh(r, "max_pbr", "sec_med_pbr")   # A: 업종 고PBR 허용
    per = r.get("PER")
    if per is None or per <= 0 or per > per_cap:
        fails.append(f"PER>{per_cap:.0f} 또는 적자")
    pbr = r.get("PBR")
    if pbr is None or pbr > pbr_cap:
        fails.append(f"PBR>{pbr_cap:.2f}")
    return fails


def gate_stage4_mos(r) -> list[str]:
    if not GATE["require_margin_of_safety"]:
        return []
    mos = r.get("margin_of_safety")
    if mos is None or mos <= 0:
        return ["안전마진<=0(현재가>=적정가)"]
    return []


def run_funnel(df: pd.DataFrame):
    """단계별 통과 집합과 카운트 반환."""
    stages = []
    survivors = df.copy()
    if HOLDCO_MODE == "exclude" and "is_holdco" in survivors.columns:  # C: 지주사 제외 모드
        survivors = survivors[~survivors["is_holdco"].astype(bool)]
    survivors = attach_sector_thresholds(survivors)  # A: 업종상대 임계 부여
    stages.append(("Stage0 유효성(비금융·재무존재)", len(survivors)))

    for label, gate in [
        ("Stage1 안전성(흑자·부채·유동·F-Score)", gate_stage1_safety),
        ("Stage2 실적(ROE·마진·성장)", gate_stage2_quality),
        ("Stage3 저평가(PER·PBR)", gate_stage3_value),
        ("Stage4 안전마진(그레이엄)", gate_stage4_mos),
    ]:
        keep = survivors[survivors.apply(lambda r: len(gate(r)) == 0, axis=1)]
        stages.append((label, len(keep)))
        survivors = keep
    return survivors, stages


def rank_survivors(survivors: pd.DataFrame) -> pd.DataFrame:
    """생존자 밸류×퀄리티 랭킹 (백분위 기반, 생존자 내부 상대평가)."""
    if survivors.empty:
        return survivors
    d = vb.add_scores(survivors)  # value_score / quality_score / composite 부여
    # 최종 = 저평가 50% + 퀄리티 50% (안전성은 이미 게이트 통과)
    d = d.copy()
    d["final_score"] = 0.5 * d["value_score"].fillna(0) + 0.5 * d["quality_score"].fillna(0)
    return d.sort_values("final_score", ascending=False)


def scorecard(r) -> dict:
    """종목별 '왜 뽑혔나' 근거."""
    def pct(x, d=1):
        return round(x * 100, d) if x is not None and pd.notna(x) else None

    return {
        "code": r["code"], "name": r["name"],
        "sector": r.get("sector"),
        "지주사": bool(r.get("is_holdco", False)),
        "price": int(r["price"]) if pd.notna(r.get("price")) else None,
        "mktcap_억": round(r["mktcap"] / 1e8) if pd.notna(r["mktcap"]) else None,
        "거래대금_억": round(r["amount"] / 1e8, 1) if pd.notna(r.get("amount")) else None,
        # 싸다
        "PER": round(r["PER"], 2) if pd.notna(r["PER"]) else None,
        "PBR": round(r["PBR"], 2) if pd.notna(r["PBR"]) else None,
        "PSR": round(r["PSR"], 2) if pd.notna(r["PSR"]) else None,
        "EV_EBITDA": round(r["EV_EBITDA"], 2) if pd.notna(r["EV_EBITDA"]) else None,
        # 안전마진 (상승여력)
        "graham_적정가": int(r["graham"]) if pd.notna(r.get("graham")) else None,
        "안전마진%": pct(r.get("margin_of_safety")),
        # 탄탄 (재무건전)
        "부채비율%": pct(r.get("debt_ratio"), 0),
        "유동비율%": pct(r.get("current_ratio"), 0),
        "Fscore": int(r["fscore"]) if pd.notna(r.get("fscore")) else None,
        # 실적 (수익성·성장)
        "ROE%": pct(r.get("ROE")),
        "영업이익률%": pct(r.get("op_margin")),
        "순이익률%": pct(r.get("net_margin")),
        "매출성장%": pct(r.get("rev_growth")),
        "순익성장%": pct(r.get("net_growth")),
        # 랭킹
        "value_score": round(r["value_score"], 3) if pd.notna(r.get("value_score")) else None,
        "quality_score": round(r["quality_score"], 3) if pd.notna(r.get("quality_score")) else None,
        "final_score": round(r["final_score"], 3) if pd.notna(r.get("final_score")) else None,
    }


def history_diff(current_codes: list[str]) -> dict | None:
    """직전 실행 대비 신규진입/이탈 종목 (주간 추적)."""
    if not HISTORY_PATH.exists():
        return None
    lines = [l for l in HISTORY_PATH.read_text(encoding="utf-8").splitlines() if l.strip()]
    if not lines:
        return None
    prev = json.loads(lines[-1])
    prev_map = {p["code"]: p["name"] for p in prev.get("picks", [])}
    prev_codes = set(prev_map)
    cur = set(current_codes)
    return {
        "prev_date": prev.get("date"),
        "entered": [c for c in current_codes if c not in prev_codes],
        "exited": [(c, prev_map[c]) for c in prev_codes if c not in cur],
        "stayed": [c for c in current_codes if c in prev_codes],
    }


def main() -> None:
    corp_map = vb.build_corp_map()

    if UNIVERSE == "all":
        listing = load_full_listing()
        codes = list(listing.index)
        amount_map = {c: float(listing.loc[c, "Amount"]) for c in codes
                      if pd.notna(listing.loc[c, "Amount"])}
    else:  # kospi200 정적 리스트
        codes = vb.get_codes()
        listing = vb.load_listing()
        codes = [c for c in codes if c in listing.index]
        amount_map = {}
    log(f"유니버스({UNIVERSE}) {len(codes)} 종목")

    shares_map = {c: float(listing.loc[c, "Stocks"]) for c in codes}
    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    marcap_map = {c: float(listing.loc[c, "Marcap"]) for c in codes}
    close_map = {c: float(listing.loc[c, "Close"]) for c in codes}

    if UNIVERSE == "all":
        fins = fetch_snapshot_financials(corp_map, codes)
    else:
        fins = vb.fetch_all_financials(corp_map, codes)
    log(f"재무 확보 {len(fins)} 종목 → 팩터 계산")

    sector_map = get_sector_map()  # A/C: 업종·지주사 분류
    df = build_rows(codes, fins, shares_map, name_map, marcap_map, close_map,
                    amount_map, sector_map)
    log(f"유효 팩터 종목 {len(df)} | A(업종상대)={SECTOR_RELATIVE} C(지주사)={HOLDCO_MODE}")

    survivors, stages = run_funnel(df)
    ranked = rank_survivors(survivors)
    picks = [scorecard(r) for _, r in ranked.head(TOP_N).iterrows()]

    diff = history_diff([p["code"] for p in picks])
    today = pd.Timestamp.now().strftime("%Y-%m-%d")

    result = {
        "generated_at": pd.Timestamp.now().isoformat(),
        "date": today,
        "universe_mode": UNIVERSE,
        "marcap_floor_억": MARCAP_FLOOR / 1e8,
        "amount_floor_억": AMOUNT_FLOOR / 1e8,
        "sector_relative": SECTOR_RELATIVE,
        "holdco_mode": HOLDCO_MODE,
        "holdco_discount": HOLDCO_DISCOUNT,
        "latest_fy": LATEST_FY,
        "universe_size": len(codes),
        "valid_factor_stocks": len(df),
        "gates": GATE,
        "funnel": [{"stage": s, "count": c} for s, c in stages],
        "survivors_total": len(survivors),
        "top_n": TOP_N,
        "diff_vs_prev": diff,
        "picks": picks,
        "disclosures": [
            "EV≈시총+부채, EBITDA≈영업이익. F-Score 7/9 컴포넌트(CFO 결측).",
            "부채·유동비율 절대임계는 업종편차 무시 — 구조적 고부채 업종(조선·유통·건설) 배제 가능.",
            "그레이엄 적정가는 순익·순자산 보수근사 — 고성장주 과소평가.",
            f"C안 적용: 지주사 순자산 {HOLDCO_DISCOUNT*100:.0f}% NAV할인({HOLDCO_MODE}) — PBR·안전마진 뻥튀기 억제.",
            f"A안 적용: PER/PBR/부채 게이트 = max(절대임계, 업종중앙값)({'ON' if SECTOR_RELATIVE else 'OFF'}) — 고부채/고PBR 우량업종 재편입.",
            "생존편향(현 상장·유동성 통과 종목만) 미제거 — 백테스트 수치 낙관. TODO 1순위.",
            "후보 발굴 도구 — 매수 결정은 사업내용·촉매·업종 정성판단 병행 필요.",
        ],
    }
    REPORTS.joinpath("value_screener.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    # 주간 히스토리 적립 (같은 날짜 재실행은 덮어씀)
    hist_rec = {"date": today, "universe_mode": UNIVERSE, "survivors_total": len(survivors),
                "picks": [{"code": p["code"], "name": p["name"],
                           "final_score": p["final_score"]} for p in picks]}
    prior = []
    if HISTORY_PATH.exists():
        prior = [l for l in HISTORY_PATH.read_text(encoding="utf-8").splitlines()
                 if l.strip() and json.loads(l).get("date") != today]
    prior.append(json.dumps(hist_rec, ensure_ascii=False))
    HISTORY_PATH.write_text("\n".join(prior) + "\n", encoding="utf-8")

    # ---------- stdout ----------
    print("\n" + "=" * 78)
    print("저평가·우량주 스크리너 — 싸다 × 재무탄탄 × 실적양호 × 안전마진")
    print("=" * 78)
    print(f"유니버스({UNIVERSE}) {len(codes)} | 유효팩터 {len(df)} | 기준 FY{LATEST_FY} + 오늘 시총 | {today}\n")
    print("■ 퍼널 (단계별 통과)")
    for s, c in stages:
        print(f"  {s:<40} {c:>4} 종목")

    if diff:
        print(f"\n■ 직전({diff['prev_date']}) 대비 변화")
        ent = ", ".join(name_map.get(c, c) for c in diff["entered"]) or "없음"
        exi = ", ".join(nm for _, nm in diff["exited"]) or "없음"
        print(f"  신규진입({len(diff['entered'])}): {ent}")
        print(f"  이탈({len(diff['exited'])}): {exi}")
        print(f"  유지({len(diff['stayed'])})")

    print(f"\n■ 최종 통과 {len(survivors)} 종목 → 밸류×퀄리티 상위 {min(TOP_N, len(picks))}")
    if picks:
        hdr = (f"  {'종목':<14}{'PER':>6}{'PBR':>6}{'안전마진':>8}"
               f"{'부채%':>7}{'유동%':>7}{'F':>3}{'ROE%':>7}{'영익률':>7}{'매출성장':>8}{'score':>7}")
        print(hdr)
        print("  " + "-" * (len(hdr) - 2))
        for p in picks:
            def f(v, w, dec=1, suf=""):
                if v is None:
                    return f"{'-':>{w}}"
                return f"{v:>{w}.{dec}f}{suf}" if isinstance(v, float) else f"{v:>{w}}{suf}"
            print(f"  {p['name'][:14]:<14}"
                  f"{f(p['PER'],6,1)}{f(p['PBR'],6,2)}{f(p['안전마진%'],7,0)}%"
                  f"{f(p['부채비율%'],6,0)}%{f(p['유동비율%'],6,0)}%{f(p['Fscore'],3)}"
                  f"{f(p['ROE%'],7,1)}{f(p['영업이익률%'],7,1)}{f(p['매출성장%'],8,1)}"
                  f"{f(p['final_score'],7,3)}")

    print("\n■ 한계·정직")
    for d in result["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/value_screener.json")


if __name__ == "__main__":
    main()
