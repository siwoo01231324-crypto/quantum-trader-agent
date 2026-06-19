"""PPP 반전 스캘핑 — 고변동 알트 + 강한 S/R + 횡보레짐 필터 검증 (#ppp).

사용자 가설: BTC/ETH(작은 움직임)가 아니라 **변동성 큰 알트(WLD 등)** 에서,
**강한 지지/저항(국소 극단)** 의 **확실한 자리**만, **횡보 레짐**일 때 반전 진입하면
비용을 이길 수 있는가. 내가 찾은 실패원인 2개(① 움직임이 비용 대비 작음 ② 추세장
다이버전스 페이크)를 동시에 해소하는 조합.

진입(반전): QPP(StochRSI) 골크@과매도 + 상승다이버 + 국소저점(=지지) [+ 횡보레짐]
            → 롱 / 대칭 숏. (추세게이트 없음)
필터: ① S/R = 국소 W봉 극단 ② 레짐 = Choppiness Index ≥ chop_thr (횡보)
데이터: Binance fapi 5m 직접 fetch (KR IP REST 무차단), /tmp 캐시.
비용: 왕복 12/20/30bp 민감도. 종목별 PF·기대값·승률.

Usage:
    python scripts/bench_ppp_altvol.py --symbols WLDUSDT,WIFUSDT,1000PEPEUSDT,ORDIUSDT
    python scripts/bench_ppp_altvol.py --no-regime --no-sr   # ablation
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(_ROOT), str(_ROOT / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from backtest.strategies.live_ppp_scalping_v1 import stoch_rsi, _rsi  # noqa: E402
from signals.rsi import detect_divergence  # noqa: E402

_CACHE = Path("/tmp")
_DEFAULT = ["WLDUSDT", "WIFUSDT", "1000PEPEUSDT", "ORDIUSDT"]
_START_MS = 1690156800000  # 2023-07-24
_COSTS = [12.0, 20.0, 30.0]
_SCAN_CAP = 1500


def _fetch_5m(symbol: str) -> pd.DataFrame | None:
    cache = _CACHE / f"altvol_{symbol}_5m.parquet"
    if cache.exists():
        return pd.read_parquet(cache)
    rows = []
    start = _START_MS
    url = "https://fapi.binance.com/fapi/v1/klines"
    while True:
        u = f"{url}?symbol={symbol}&interval=5m&limit=1500&startTime={start}"
        try:
            data = json.load(urllib.request.urlopen(u, timeout=30))
        except Exception as e:
            print(f"    fetch err {symbol}: {str(e)[:50]}", flush=True)
            break
        if not data:
            break
        rows += data
        last = data[-1][0]
        if len(data) < 1500:
            break
        start = last + 1
        time.sleep(0.12)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume",
                                     "ct", "qv", "n", "tb", "tq", "ig"])
    df = df[["t", "open", "high", "low", "close", "volume"]].astype(
        {"open": float, "high": float, "low": float, "close": float, "volume": float})
    df.index = pd.to_datetime(df["t"], unit="ms", utc=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()[
        ["open", "high", "low", "close", "volume"]]
    df.to_parquet(cache)
    return df


def _choppiness(high, low, close, n=14):
    tr = np.maximum(high[1:] - low[1:],
                    np.maximum(np.abs(high[1:] - close[:-1]),
                               np.abs(low[1:] - close[:-1])))
    tr = np.concatenate([[np.nan], tr])
    s = pd.Series(tr)
    atrsum = s.rolling(n).sum().to_numpy()
    hh = pd.Series(high).rolling(n).max().to_numpy()
    ll = pd.Series(low).rolling(n).min().to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        ci = 100 * np.log10(atrsum / (hh - ll)) / np.log10(n)
    return ci


def _entries(d, *, use_sr, use_regime, use_div, sr_win, chop_thr, ob, os_):
    n = d["n"]; golden = d["golden"]; dead = d["dead"]; div = d["div"]; qm = d["qm"]
    high = d["high"]; low = d["low"]; ci = d["ci"]
    roll_min = pd.Series(low).rolling(sr_win).min().shift(1).to_numpy()
    roll_max = pd.Series(high).rolling(sr_win).max().shift(1).to_numpy()
    out = []
    for i in range(260, n):
        if np.isnan(qm[i]):
            continue
        if use_regime and (np.isnan(ci[i]) or ci[i] < chop_thr):
            continue
        if golden[i] and qm[i] <= os_ and (not use_div or div[i] == "bullish"):
            if use_sr and not (low[i] <= roll_min[i]):
                continue
            out.append((i, "long"))
        elif dead[i] and qm[i] >= ob and (not use_div or div[i] == "bearish"):
            if use_sr and not (high[i] >= roll_max[i]):
                continue
            out.append((i, "short"))
    return out


def _sim(d, entries, cfg):
    close = d["close"]; high = d["high"]; low = d["low"]
    golden = d["golden"]; dead = d["dead"]; n = d["n"]; kind = cfg["kind"]
    trades = []; busy = -1
    for (i, side) in entries:
        if i <= busy:
            continue
        ep = float(close[i])
        if kind == "fixed":
            sl = ep * (1 - cfg["sl"]) if side == "long" else ep * (1 + cfg["sl"])
            tp = ep * (1 + cfg["tp"]) if side == "long" else ep * (1 - cfg["tp"])
        else:  # opp_cross + 안전 SL
            sl = ep * (1 - cfg["sl"]) if side == "long" else ep * (1 + cfg["sl"])
            tp = None
        exit_i = exit_px = None
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
        trades.append({"gross": gross, "hold": exit_i - i, "i": i, "n": n})
        busy = exit_i
    return trades


def _metrics(trades, cost):
    if not trades:
        return {"trades": 0, "pf": 0.0, "expectancy": 0.0, "win_rate": 0.0, "mdd": 0.0}
    rets = np.array([t["gross"] - cost / 10000.0 for t in trades])
    w = rets[rets > 0]; l = rets[rets <= 0]
    gl = float(abs(l.sum()))
    pf = float(w.sum()) / gl if gl > 0 else float("inf")
    eq = np.cumprod(1 + np.clip(rets, -0.999, None))
    dd = eq / np.maximum.accumulate(eq) - 1
    return {"trades": int(len(trades)), "pf": round(pf, 4),
            "expectancy": round(float(rets.mean()), 6),
            "win_rate": round(float(len(w) / len(rets)), 4),
            "mdd": round(float(dd.min()), 4)}


_CONFIGS = [
    {"name": "opp_cross", "kind": "opp", "sl": 0.02},
    {"name": "fixed_1_2", "kind": "fixed", "sl": 0.01, "tp": 0.02},
    {"name": "fixed_1_3", "kind": "fixed", "sl": 0.01, "tp": 0.03},
    {"name": "fixed_2_4", "kind": "fixed", "sl": 0.02, "tp": 0.04},
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default=",".join(_DEFAULT))
    ap.add_argument("--sr-win", type=int, default=30)
    ap.add_argument("--chop-thr", type=float, default=61.8)
    ap.add_argument("--ob", type=float, default=75.0)
    ap.add_argument("--os", type=float, default=25.0)
    ap.add_argument("--no-sr", action="store_true")
    ap.add_argument("--no-regime", action="store_true")
    ap.add_argument("--no-div", action="store_true")
    ap.add_argument("--output", default=None)
    args = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    prepped = {}
    for s in symbols:
        df = _fetch_5m(s)
        if df is None or len(df) < 2000:
            print(f"  skip {s}", flush=True); continue
        close = df["close"]
        main_, sig = stoch_rsi(close)
        m = main_.to_numpy(); sg = sig.to_numpy()
        gp = np.concatenate([[np.nan], m[:-1]]); sp = np.concatenate([[np.nan], sg[:-1]])
        d = {
            "n": len(df), "close": close.to_numpy(),
            "high": df["high"].to_numpy(), "low": df["low"].to_numpy(),
            "golden": (gp <= sp) & (m > sg), "dead": (gp >= sp) & (m < sg),
            "div": detect_divergence(close, _rsi(close, 14), 14).to_numpy(),
            "qm": m,
            "ci": _choppiness(df["high"].to_numpy(), df["low"].to_numpy(),
                              df["close"].to_numpy(), 14),
        }
        prepped[s] = d
        print(f"  {s}: 5m bars={d['n']} ({df.index[0].date()}~{df.index[-1].date()})", flush=True)

    results = []
    for cfg in _CONFIGS:
        all_tr = []; per = {}
        for s, d in prepped.items():
            ent = _entries(d, use_sr=not args.no_sr, use_regime=not args.no_regime,
                           use_div=not args.no_div, sr_win=args.sr_win,
                           chop_thr=args.chop_thr, ob=args.ob, os_=args.os)
            tr = _sim(d, ent, cfg)
            all_tr += tr; per[s] = tr
        row = {"config": cfg["name"]}
        for c in _COSTS:
            row[f"cost{int(c)}bp"] = _metrics(all_tr, c)
        row["per_symbol_20bp"] = {s: _metrics(per[s], 20.0) for s in per}
        # OOS 분할: 각 종목 봉 중간 기준 train(전반)/test(후반) @20bp
        train = [t for t in all_tr if t["i"] < t["n"] / 2]
        test = [t for t in all_tr if t["i"] >= t["n"] / 2]
        row["oos_train_20bp"] = _metrics(train, 20.0)
        row["oos_test_20bp"] = _metrics(test, 20.0)
        results.append(row)
        m20 = row["cost20bp"]; tr_, te_ = row["oos_train_20bp"], row["oos_test_20bp"]
        print(f"[{cfg['name']:<11}] all: trades={m20['trades']:<6} PF12={row['cost12bp']['pf']:<6} "
              f"PF20={m20['pf']:<6} exp={m20['expectancy']*100:+.3f}% win={m20['win_rate']*100:.1f}% | "
              f"OOS train PF={tr_['pf']}(n{tr_['trades']}) test PF={te_['pf']}(n{te_['trades']})", flush=True)

    out = {"symbols": list(prepped.keys()), "sr_win": args.sr_win,
           "chop_thr": args.chop_thr, "use_sr": not args.no_sr,
           "use_regime": not args.no_regime, "results": results}
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print("wrote", args.output, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
