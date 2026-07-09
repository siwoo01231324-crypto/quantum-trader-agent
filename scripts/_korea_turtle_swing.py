"""한국주식 터틀 돌파 스윙 전략 백테스트 — 크립토 터틀(PF 2.33) 패턴 이식.

수급 데이터(외국인·기관)가 pykrx KRX 로그인으로 막혀, 스윙 트레이더의 "스마트머니 추종"
대신 데이터 되는 검증 패턴(일봉 Donchian 터틀 돌파)을 한국 주식에 적용.
밸류·배당(가치)과 완전히 다른 축(추세추종) → 진짜 분산 후보.

규칙 (터틀 클래식):
  진입: 종가 > 최근 ENTRY_N일 신고가 (Donchian breakout) AND 종가 > 200일 이동평균(추세)
  청산: 종가 < 최근 EXIT_N일 신저가 OR 진입가 - ATR_MULT×ATR (손절/트레일)
  포트: 최대 MAX_POS 동시보유, 동일가중, 빈 슬롯에 신규 돌파 진입
  스윙 보유(수일~수개월, 추세 지속 동안).

평가: PF(총이익/총손실)·승률·평균손익·CAGR·MDD·거래수. (사용자 요청 PF 명확)
유니버스: 현 상장 시총≥300억·거래대금≥5억(스윙은 유동성 더 필요). 생존편향 인정(현 상장).

실행: python scripts/_korea_turtle_swing.py   (리서치)
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

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

REPORTS = Path(__file__).resolve().parents[1] / "reports"
START, END = pd.Timestamp("2019-01-01"), pd.Timestamp("2026-06-01")
ENTRY_N = 20        # 돌파 신고가 기간
EXIT_N = 10         # 청산 신저가 기간
ATR_N = 20
ATR_MULT = 2.0
MAX_POS = 15
MARCAP_FLOOR = 300e8
AMOUNT_FLOOR = 5e8
COST = 0.0055       # 라운드트립 55bp (KRX 수수료+세금+슬립)


def log(m): print(f"[turtle] {m}", flush=True)


def atr(df, n):
    h, l, c = df["High"], df["Low"], df["Close"]
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean()


def load_ohlc(code):
    """FDR OHLC (터틀은 고저 필요). 캐시 재사용 불가 시 재로드."""
    import FinanceDataReader as fdr
    try:
        df = fdr.DataReader(code, "2017-06-01", "2026-06-01")
        if df is None or df.empty or "Close" not in df:
            return None
        return df[["Open", "High", "Low", "Close"]].dropna()
    except Exception:  # noqa: BLE001
        return None


def signals(df):
    """진입/청산 레벨 시계열."""
    df = df.copy()
    df["hi"] = df["Close"].rolling(ENTRY_N).max().shift(1)   # 직전까지 신고가
    df["lo"] = df["Close"].rolling(EXIT_N).min().shift(1)
    df["ma200"] = df["Close"].rolling(200).mean()
    df["atr"] = atr(df, ATR_N)
    return df


def main():
    listing = vs.load_full_listing()  # 이미 시총·거래대금 필터 (100억/2억)
    listing = listing[(listing["Marcap"] >= MARCAP_FLOOR) & (listing["Amount"] >= AMOUNT_FLOOR)]
    codes = list(listing.index)
    log(f"유니버스 {len(codes)} (시총≥300억·거래대금≥5억) — OHLC 로드")

    data = {}
    for i, c in enumerate(codes):
        d = load_ohlc(c)
        if d is not None and len(d) > 250:
            data[c] = signals(d)
        if (i + 1) % 100 == 0:
            log(f"OHLC {i+1}/{len(codes)}")
    log(f"OHLC 확보 {len(data)} — 시뮬")

    name_map = {c: str(listing.loc[c, "Name"]) for c in codes}
    # 공통 거래일 인덱스에 정렬 → 넘파이 배열(정수 인덱싱, 고속)
    master = sorted(set().union(*[set(df.index) for df in data.values()]))
    master = [d for d in master if START <= d <= END]
    midx = pd.DatetimeIndex(master)
    arr = {}
    for code, df in data.items():
        d = df.reindex(midx)
        arr[code] = {k: d[k].to_numpy() for k in ("Close", "hi", "lo", "ma200", "atr")}
    log(f"정렬 완료 {len(master)}거래일 × {len(arr)}종목 — 시뮬")

    positions = {}   # code -> {entry_price, entry_date_i, stop}
    trades = []
    codes_arr = list(arr)
    for i in range(len(master)):
        # 청산
        for code in list(positions):
            a = arr[code]; c = a["Close"][i]
            if np.isnan(c):
                continue
            pos = positions[code]
            if (c < a["lo"][i]) or (c <= pos["stop"]):
                r = c / pos["entry_price"] - 1 - COST
                ed = master[pos["entry_i"]]
                trades.append({"code": code, "name": name_map.get(code, code),
                               "entry": ed.strftime("%Y-%m-%d"),
                               "exit": master[i].strftime("%Y-%m-%d"),
                               "hold_days": (master[i] - ed).days,
                               "return%": round(r * 100, 1)})
                del positions[code]
            elif not np.isnan(a["atr"][i]):
                pos["stop"] = max(pos["stop"], c - ATR_MULT * a["atr"][i])

        # 진입 (빈 슬롯)
        if len(positions) < MAX_POS:
            cands = []
            for code in codes_arr:
                if code in positions:
                    continue
                a = arr[code]; c = a["Close"][i]; hi = a["hi"][i]
                if np.isnan(c) or np.isnan(hi) or np.isnan(a["ma200"][i]) or np.isnan(a["atr"][i]):
                    continue
                if c > hi and c > a["ma200"][i]:
                    cands.append((code, c / hi - 1))
            cands.sort(key=lambda x: x[1], reverse=True)
            for code, _ in cands[:MAX_POS - len(positions)]:
                a = arr[code]
                positions[code] = {"entry_price": a["Close"][i], "entry_i": i,
                                   "stop": a["Close"][i] - ATR_MULT * a["atr"][i]}

    # 성과 (거래 기반)
    rets = np.array([t["return%"] / 100 for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    pf = (wins.sum() / -losses.sum()) if len(losses) and losses.sum() < 0 else None
    # equity curve (거래별 순차 근사 — 동일가중 MAX_POS 슬롯)
    n_yr = (END - START).days / 365.25
    # 슬롯 기반 복리: 각 거래 수익을 1/MAX_POS 비중으로
    eq = 1.0; peak = 1.0; mdd = 0.0
    port = sorted(trades, key=lambda t: t["exit"])
    for t in port:
        eq *= (1 + t["return%"] / 100 / MAX_POS)
        peak = max(peak, eq); mdd = min(mdd, eq / peak - 1)
    cagr = eq ** (1 / n_yr) - 1

    result = {"generated_at": pd.Timestamp.now().isoformat(),
              "params": {"ENTRY_N": ENTRY_N, "EXIT_N": EXIT_N, "ATR_MULT": ATR_MULT,
                         "MAX_POS": MAX_POS, "cost_bp": COST * 1e4},
              "n_trades": len(trades),
              "PF": round(pf, 2) if pf else None,
              "win_rate": f"{len(wins)}/{len(trades)}",
              "avg_win%": round(float(wins.mean()) * 100, 1) if len(wins) else None,
              "avg_loss%": round(float(losses.mean()) * 100, 1) if len(losses) else None,
              "avg_hold_days": round(float(np.mean([t["hold_days"] for t in trades]))) if trades else 0,
              "CAGR%": round(cagr * 100, 1), "MDD%": round(mdd * 100, 1), "final": round(eq, 2),
              "disclosures": ["생존편향: 현 상장만(상폐 미포함). 스윙은 유동성 필수라 시총≥300억.",
                              "돌파 지정가 아닌 종가 체결 가정. 비용 55bp 차감. equity=슬롯 복리 근사."]}
    REPORTS.joinpath("korea_turtle_swing.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 60)
    print("한국주식 터틀 돌파 스윙 — Donchian20/10 + 200MA + 2ATR")
    print("=" * 60)
    print(f"(현 유니버스 {len(data)}, 2019~2026, 최대 {MAX_POS}보유, 비용 55bp)\n")
    r = result
    print(f"  PF(총이익/총손실)  {r['PF']}   ← 사용자 핵심 지표")
    print(f"  거래수            {r['n_trades']}")
    print(f"  승률             {r['win_rate']}")
    print(f"  평균수익/손실      {r['avg_win%']}% / {r['avg_loss%']}%")
    print(f"  평균보유          {r['avg_hold_days']}일 (스윙 확인)")
    print(f"  CAGR / MDD       {r['CAGR%']}% / {r['MDD%']}%")
    top = sorted(trades, key=lambda t: t["return%"], reverse=True)[:6]
    print(f"\n■ 최대 수익 거래 top 6")
    for t in top:
        print(f"  {t['name'][:14]:<14} {t['entry']}→{t['exit']} ({t['hold_days']:>3}일) {t['return%']:+.0f}%")
    print("\n■ 한계")
    for d in r["disclosures"]:
        print(f"  - {d}")
    print(f"\n결과 저장: reports/korea_turtle_swing.json")


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:  # noqa: BLE001
        print("!!! 예외 !!!", flush=True); traceback.print_exc(); sys.exit(1)
