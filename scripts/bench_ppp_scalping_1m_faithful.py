"""PPP 스캘핑 충실(faithful) 5y bench — 1m 진입 + 15m HTF 레짐 + 강의 실제 청산.

1차 bench(15m 단일·stop1.5%/tp3%)가 강의에 비충실했던 점 교정:
  - 진입: 방향(1P)·지지(2P)는 **15m HTF**, 트리거(3P QPP 골크/데크)는 **1분봉**
    (강의 "15m 셋업 → 1분봉 미세진입" 그대로). HTF 값은 1봉 shift+ffill (lookahead 차단).
  - 청산: 강의 실제 룰을 백테스트(오케 제약 없음)로 직접 시뮬 —
      opp_cross : 1분봉 반대 QPP 크로스에서 청산 (+안전 SL) ← 강의 핵심 "데크에 내려"
      next_ema  : 다음 HTF 이평 목표 익절 + HTF 이평 이탈 손절
      fixed_*   : 소폭 고정 % (강의 ROI 30%@50x ≈ 가격 0.6% 수준)
  - 비용 민감도: 왕복 12 / 20 / 30 bp (gross 저장 후 사후 차감 — 체결타이밍 무관).

데이터: lake/ohlcv/freq=1m (10 메이저, ~5y). 1포지션/종목.

Usage:
    python scripts/bench_ppp_scalping_1m_faithful.py --output reports/bench_ppp_faithful_5y.json
    python scripts/bench_ppp_scalping_1m_faithful.py --symbols BTCUSDT --htf 15min
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest.strategies.live_ppp_scalping_v1 import _ema, stoch_rsi, _rsi  # noqa: E402
from signals.rsi import detect_divergence  # noqa: E402

_LAKE1M = _ROOT / "lake" / "ohlcv" / "freq=1m"
_DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "ATOMUSDT",
]
_EMA_F, _EMA_M, _EMA_S = 60, 120, 240
_TOL = 0.0015
_TOUCH_LB = 3
_SCAN_CAP = 2000          # 청산 미발생 시 강제 청산 봉수 (1m → ~33h)
_SAFETY_SL = 0.01         # opp_cross 안전 손절 1%
_COSTS_BP = [12.0, 20.0, 30.0]  # 왕복 bp 민감도

# 청산 조합 (강의 충실).
_CONFIGS = [
    {"name": "opp_cross",   "kind": "opp",   "sl": _SAFETY_SL, "tp": None},
    {"name": "next_ema",    "kind": "ema",   "sl": None,       "tp": None},
    {"name": "fixed_03_06", "kind": "fixed", "sl": 0.003,      "tp": 0.006},
    {"name": "fixed_03_05", "kind": "fixed", "sl": 0.003,      "tp": 0.005},
    {"name": "fixed_05_10", "kind": "fixed", "sl": 0.005,      "tp": 0.010},
]


def _load_1m(symbol: str) -> pd.DataFrame | None:
    files = sorted(_LAKE1M.glob(f"year=*/month=*/symbol={symbol}/*.parquet"))
    if not files:
        return None
    parts = []
    for f in files:
        try:
            parts.append(pd.read_parquet(f))
        except Exception:
            continue
    if not parts:
        return None
    df = pd.concat(parts)
    if "ts" in df.columns:
        df = df.set_index(pd.to_datetime(df["ts"], utc=True))
    else:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cols = {c.lower(): c for c in df.columns}
    ren = {cols.get(k, k): k for k in ("open", "high", "low", "close", "volume")}
    df = df.rename(columns=ren)[["open", "high", "low", "close", "volume"]]
    return df if len(df) > 5000 else None


def _prep(symbol: str, htf: str):
    """1m + HTF 지표 산출 → 진입 마스크/시그널 + 청산용 배열."""
    m1 = _load_1m(symbol)
    if m1 is None:
        return None
    close1 = m1["close"]
    # 1m QPP
    main, sig = stoch_rsi(close1)
    main = main.to_numpy(); sig = sig.to_numpy()
    gpre = np.concatenate([[np.nan], main[:-1]]); spre = np.concatenate([[np.nan], sig[:-1]])
    golden1 = (gpre <= spre) & (main > sig)
    dead1 = (gpre >= spre) & (main < sig)
    # 4P 다이버전스 (1m) + OB/OS 구간 (1m QPP 본선)
    rsi1 = _rsi(close1, 14)
    div1 = detect_divergence(close1, rsi1, 14).to_numpy()
    main_arr = main

    # HTF (resample) EMA + 레짐 + 지지/저항 (1봉 shift → lookahead 차단)
    h = m1.resample(htf).agg({"open": "first", "high": "max", "low": "min",
                              "close": "last", "volume": "sum"}).dropna()
    hc = h["close"]
    e60 = _ema(hc, _EMA_F); e120 = _ema(hc, _EMA_M); e240 = _ema(hc, _EMA_S)
    bull_h = (e120 > e240)
    bear_h = (e120 < e240)
    emas_h = [e60, e120, e240]
    sup_h = pd.Series(False, index=h.index)
    res_h = pd.Series(False, index=h.index)
    for e in emas_h:
        sup_h |= (h["low"] <= e * (1 + _TOL)) & (h["close"] > e)
        res_h |= (h["high"] >= e * (1 - _TOL)) & (h["close"] < e)
    sup_recent = sup_h.rolling(_TOUCH_LB).max().fillna(0) > 0
    res_recent = res_h.rolling(_TOUCH_LB).max().fillna(0) > 0

    def to1m(s):
        return s.shift(1).reindex(m1.index, method="ffill")

    bull = to1m(bull_h).fillna(False).to_numpy().astype(bool)
    bear = to1m(bear_h).fillna(False).to_numpy().astype(bool)
    supR = to1m(sup_recent).fillna(False).to_numpy().astype(bool)
    resR = to1m(res_recent).fillna(False).to_numpy().astype(bool)
    # next-EMA 목표/이탈용: HTF EMA 값들을 1m 로 매핑
    e60_1 = to1m(e60).to_numpy(); e120_1 = to1m(e120).to_numpy(); e240_1 = to1m(e240).to_numpy()

    return {
        "idx": m1.index.to_numpy(),
        "close": close1.to_numpy(), "high": m1["high"].to_numpy(), "low": m1["low"].to_numpy(),
        "golden": golden1, "dead": dead1, "div": div1, "qmain": main_arr,
        "bull": bull, "bear": bear, "supR": supR, "resR": resR,
        "e": (e60_1, e120_1, e240_1),
        "n": len(m1),
        "span": (m1.index[0], m1.index[-1]),
    }


def _entries(d, confluence: bool = False, mode: str = "trend",
             ob: float = 75.0, os_: float = 25.0) -> list[tuple[int, str]]:
    """mode='trend' (1P+2P+3P[+4P confluence]) / 'reversion' (사용자 가설:
    OB/OS 극단 + QPP 크로스 + 다이버전스, 추세게이트 없음)."""
    n = d["n"]; golden = d["golden"]; dead = d["dead"]
    bull = d["bull"]; bear = d["bear"]; supR = d["supR"]; resR = d["resR"]
    div = d["div"]; qm = d["qmain"]
    main_ok = ~np.isnan(d["e"][2])
    out = []
    for i in range(250, n):
        if not main_ok[i]:
            continue
        if mode == "reversion":
            # 과매도 골크 + 상승 다이버 → 롱 / 과매수 데크 + 하락 다이버 → 숏 (추세무관)
            if golden[i] and qm[i] <= os_ and div[i] == "bullish":
                out.append((i, "long"))
            elif dead[i] and qm[i] >= ob and div[i] == "bearish":
                out.append((i, "short"))
            continue
        # trend mode (기존)
        if golden[i] and bull[i] and supR[i]:
            if confluence and not (div[i] == "bullish" and qm[i] <= os_):
                continue
            out.append((i, "long"))
        elif dead[i] and bear[i] and resR[i]:
            if confluence and not (div[i] == "bearish" and qm[i] >= ob):
                continue
            out.append((i, "short"))
    return out


def _sim(d, entries, cfg) -> list[dict]:
    close = d["close"]; high = d["high"]; low = d["low"]
    golden = d["golden"]; dead = d["dead"]
    e60, e120, e240 = d["e"]
    n = d["n"]; kind = cfg["kind"]
    trades = []; busy = -1
    for (i, side) in entries:
        if i <= busy:
            continue
        ep = float(close[i])
        # 손절/익절 가격 결정
        if kind == "fixed":
            sl = ep * (1 - cfg["sl"]) if side == "long" else ep * (1 + cfg["sl"])
            tp = ep * (1 + cfg["tp"]) if side == "long" else ep * (1 - cfg["tp"])
        elif kind == "ema":  # next HTF EMA 목표 + EMA 이탈 손절
            vals = [v for v in (e60[i], e120[i], e240[i]) if v == v and v > 0]
            below = [v for v in vals if v <= ep]; above = [v for v in vals if v >= ep]
            if side == "long":
                sl = (max(below) if below else ep * 0.995) * (1 - 0.001)
                tp = min(above) if above else ep * 1.01
            else:
                sl = (min(above) if above else ep * 1.005) * (1 + 0.001)
                tp = max(below) if below else ep * 0.99
        else:  # opp_cross — 안전 SL + 반대크로스 청산
            sl = ep * (1 - cfg["sl"]) if side == "long" else ep * (1 + cfg["sl"])
            tp = None
        exit_i = None; exit_px = None
        end = min(n, i + 1 + _SCAN_CAP)
        for j in range(i + 1, end):
            hi = float(high[j]); lo = float(low[j])
            if side == "long":
                if lo <= sl:
                    exit_i, exit_px = j, sl; break
                if tp is not None and hi >= tp:
                    exit_i, exit_px = j, tp; break
                if kind == "opp" and dead[j]:
                    exit_i, exit_px = j, float(close[j]); break
            else:
                if hi >= sl:
                    exit_i, exit_px = j, sl; break
                if tp is not None and lo <= tp:
                    exit_i, exit_px = j, tp; break
                if kind == "opp" and golden[j]:
                    exit_i, exit_px = j, float(close[j]); break
        if exit_i is None:
            exit_i = end - 1; exit_px = float(close[exit_i])
        gross = (exit_px / ep - 1) if side == "long" else (ep / exit_px - 1)
        trades.append({"gross": gross, "hold": exit_i - i, "side": side})
        busy = exit_i
    return trades


def _metrics(trades, cost_bp_roundtrip):
    if not trades:
        return {"trades": 0, "pf": 0.0, "expectancy": 0.0, "win_rate": 0.0, "mdd": 0.0, "avg_hold": 0.0}
    rt = cost_bp_roundtrip / 10000.0
    rets = np.array([t["gross"] - rt for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    gl = float(abs(losses.sum()))
    pf = float(wins.sum()) / gl if gl > 0 else float("inf")
    eq = np.cumprod(1 + np.clip(rets, -0.999, None))
    dd = eq / np.maximum.accumulate(eq) - 1
    return {
        "trades": int(len(trades)), "pf": round(pf, 4),
        "expectancy": round(float(rets.mean()), 6),
        "win_rate": round(float(len(wins) / len(rets)), 4),
        "mdd": round(float(dd.min()), 4),
        "avg_hold": round(float(np.mean([t["hold"] for t in trades])), 1),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(_DEFAULT_SYMBOLS))
    ap.add_argument("--htf", default="15min")
    ap.add_argument("--confluence", action="store_true", help="4P 다이버전스+OB/OS 필수")
    ap.add_argument("--mode", choices=["trend", "reversion"], default="trend",
                    help="reversion = OB/OS 극단+QPP 크로스+다이버전스 (추세게이트 없음)")
    ap.add_argument("--output", default=None)
    args = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    prepped = {}; entries_by = {}
    for s in symbols:
        d = _prep(s, args.htf)
        if d is None:
            print(f"  skip {s}", flush=True); continue
        prepped[s] = d
        entries_by[s] = _entries(d, confluence=args.confluence, mode=args.mode)
        sp = d["span"]
        print(f"  {s}: 1m bars={d['n']} entries={len(entries_by[s])} ({sp[0].date()}~{sp[1].date()})", flush=True)

    results = []
    for cfg in _CONFIGS:
        all_tr = []; per_sym = {}
        for s in prepped:
            tr = _sim(prepped[s], entries_by[s], cfg)
            all_tr += tr; per_sym[s] = tr
        row = {"config": cfg["name"]}
        for cost in _COSTS_BP:
            row[f"cost{int(cost)}bp"] = _metrics(all_tr, cost)
        # 종목별(왕복20bp)
        row["per_symbol_20bp"] = {s: _metrics(per_sym[s], 20.0) for s in per_sym}
        results.append(row)
        m20 = row["cost20bp"]
        print(f"[{cfg['name']:<12}] trades={m20['trades']:<6} PF@20bp={m20['pf']:<7} "
              f"exp={m20['expectancy']*100:+.3f}% win={m20['win_rate']*100:.1f}% "
              f"MDD={m20['mdd']*100:.1f}% hold={m20['avg_hold']}m", flush=True)

    out = {"htf": args.htf, "entry_tf": "1m", "symbols": list(prepped.keys()),
           "costs_roundtrip_bp": _COSTS_BP, "results": results}
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print("wrote", args.output, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
