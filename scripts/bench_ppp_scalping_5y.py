"""5y backtest — live-ppp-scalping-v1, 익절/손절 조합 비교 (#ppp).

전략 진입(1P EMA배열 + 2P 이평 지지/저항 + 3P QPP 골크/데크)은 청산과 무관하므로
심볼당 1회 산출하고, **청산 조합(sl_mode/tp_mode)별로만** 재시뮬해 비교한다.
지표·청산은 전략 모듈의 실제 함수(`_ema`, `stoch_rsi`, `_exit_overrides`,
`signals.rsi.detect_divergence`)를 그대로 재사용 — 백테스트=라이브 로직 동일.

데이터: lake/ohlcv/freq=5m → 15m 리샘플 (10 메이저, ~5y). 비용 round-trip
``2 × cost_bps`` (기본 10bp×2=20bp). 게이트: PF>1 AND 거래당 기대값>0.

Usage:
    python scripts/bench_ppp_scalping_5y.py
    python scripts/bench_ppp_scalping_5y.py --symbols BTCUSDT,ETHUSDT --cost-bps 10
    python scripts/bench_ppp_scalping_5y.py --output reports/bench_ppp_scalping_5y.json
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

from backtest.strategies.live_ppp_scalping_v1 import (  # noqa: E402
    LivePppScalping, _ema, stoch_rsi,
)
from signals.rsi import detect_divergence  # noqa: E402

_LAKE = _ROOT / "lake" / "ohlcv" / "freq=5m"
_DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "ADAUSDT", "AVAXUSDT", "DOGEUSDT", "LINKUSDT", "ATOMUSDT",
]

# 비교할 청산 조합.
_CONFIGS = [
    {"name": "fixed_1to2",   "sl_mode": "fixed", "tp_mode": "fixed"},
    {"name": "ema_nextema",  "sl_mode": "ema",   "tp_mode": "next_ema"},
    {"name": "ema_bbupper",  "sl_mode": "ema",   "tp_mode": "bb_upper"},
    {"name": "ema_bbmid",    "sl_mode": "ema",   "tp_mode": "bb_mid"},
]


def _load_15m(symbol: str) -> pd.DataFrame | None:
    """lake 5m parquet → 15m OHLCV 리샘플."""
    files = sorted(_LAKE.glob(f"year=*/month=*/symbol={symbol}/*.parquet"))
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
    # ts 컬럼 또는 인덱스 정규화
    if "ts" in df.columns:
        df = df.set_index(pd.to_datetime(df["ts"], utc=True))
    else:
        df.index = pd.to_datetime(df.index, utc=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    cols = {c.lower(): c for c in df.columns}
    o, h, l, c, v = (cols.get(k, k) for k in ("open", "high", "low", "close", "volume"))
    r = df.resample("15min").agg(
        {o: "first", h: "max", l: "min", c: "last", v: "sum"}
    ).dropna()
    r.columns = ["open", "high", "low", "close", "volume"]
    return r if len(r) > 300 else None


def _entries(panel: pd.DataFrame, strat: LivePppScalping) -> list[tuple[int, str]]:
    """심볼 1회 진입 산출 (1P+2P+3P) — 청산 무관. (bar_idx, side) 리스트."""
    close = panel["close"]
    low = panel["low"].to_numpy()
    high = panel["high"].to_numpy()
    e60 = _ema(close, strat.ema_fast).to_numpy()
    e120 = _ema(close, strat.ema_mid).to_numpy()
    e240 = _ema(close, strat.ema_slow).to_numpy()
    main, sig = stoch_rsi(close, rsi_len=strat.rsi_len, stoch_len=strat.stoch_len,
                          smooth_k=strat.smooth_k, smooth_d=strat.smooth_d)
    main = main.to_numpy(); sig = sig.to_numpy()
    c = close.to_numpy()
    n = len(panel)
    tol = strat.tol_pct
    lb = strat.touch_lookback
    mh = strat.min_history
    emas = (e60, e120, e240)

    def sup(j):  # long 지지 터치 (j 봉)
        for e in emas:
            ev = e[j]
            if ev == ev and ev > 0 and low[j] <= ev * (1 + tol) and c[j] > ev:
                return True
        return False

    def res(j):  # short 저항 터치
        for e in emas:
            ev = e[j]
            if ev == ev and ev > 0 and high[j] >= ev * (1 - tol) and c[j] < ev:
                return True
        return False

    out = []
    for i in range(mh, n):
        if main[i] != main[i] or sig[i] != sig[i] or e240[i] != e240[i]:
            continue
        bull = e120[i] > e240[i]
        bear = e120[i] < e240[i]
        golden = main[i - 1] <= sig[i - 1] and main[i] > sig[i]
        dead = main[i - 1] >= sig[i - 1] and main[i] < sig[i]
        lo = max(0, i - lb + 1)
        if bull and golden and any(sup(j) for j in range(lo, i + 1)):
            out.append((i, "long"))
        elif bear and dead and any(res(j) for j in range(lo, i + 1)):
            out.append((i, "short"))
    return out


def _simulate(panel: pd.DataFrame, entries, strat: LivePppScalping,
              cost_bps: float) -> list[dict]:
    """청산 조합별 시뮬 — 1포지션, intrabar high/low 로 stop/tp 체결."""
    close = panel["close"]
    c = close.to_numpy(); high = panel["high"].to_numpy(); low = panel["low"].to_numpy()
    idx = panel.index.to_numpy()
    e60 = _ema(close, strat.ema_fast).to_numpy()
    e120 = _ema(close, strat.ema_mid).to_numpy()
    e240 = _ema(close, strat.ema_slow).to_numpy()
    n = len(panel)
    rt = 2 * cost_bps / 10000.0
    trades = []
    busy_until = -1
    for (i, side) in entries:
        if i <= busy_until:
            continue
        entry_px = float(c[i])
        slp, tpp = strat._exit_overrides(
            close.iloc[: i + 1], entry_px,
            [e60[i], e120[i], e240[i]], side,
        )
        slp = slp if slp is not None else strat.stop_loss_pct
        tpp = tpp if tpp is not None else strat.take_profit_pct
        if side == "long":
            sl = entry_px * (1 - slp); tp = entry_px * (1 + tpp)
        else:
            sl = entry_px * (1 + slp); tp = entry_px * (1 - tpp)
        # forward exit
        exit_px = None; exit_i = None
        for j in range(i + 1, n):
            hi = float(high[j]); lo = float(low[j])
            if side == "long":
                if lo <= sl:
                    exit_px = sl; exit_i = j; break
                if hi >= tp:
                    exit_px = tp; exit_i = j; break
            else:
                if hi >= sl:
                    exit_px = sl; exit_i = j; break
                if lo <= tp:
                    exit_px = tp; exit_i = j; break
        if exit_px is None:  # 미청산 — 마지막 종가 청산
            exit_px = float(c[-1]); exit_i = n - 1
        gross = (exit_px / entry_px - 1) if side == "long" else (entry_px / exit_px - 1)
        trades.append({
            "side": side, "ret": gross - rt, "hold": exit_i - i,
            "exit_ts": pd.Timestamp(idx[exit_i]),
        })
        busy_until = exit_i
    return trades


def _metrics(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "pf": 0.0, "expectancy": 0.0, "win_rate": 0.0,
                "mdd": 0.0, "avg_hold": 0.0, "total_ret": 0.0}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    gross_win = float(wins.sum()); gross_loss = float(abs(losses.sum()))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    eq = np.cumprod(1 + np.clip(rets, -0.999, None))
    dd = eq / np.maximum.accumulate(eq) - 1
    return {
        "trades": int(len(trades)),
        "pf": round(pf, 4),
        "expectancy": round(float(rets.mean()), 6),
        "win_rate": round(float(len(wins) / len(rets)), 4),
        "mdd": round(float(dd.min()), 4),
        "avg_hold": round(float(np.mean([t["hold"] for t in trades])), 1),
        "total_ret": round(float(eq[-1] - 1), 4),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PPP scalping 5y exit-combo bench")
    ap.add_argument("--symbols", default=",".join(_DEFAULT_SYMBOLS))
    ap.add_argument("--cost-bps", type=float, default=10.0, help="one-way bp (round-trip 2x)")
    ap.add_argument("--output", default=None)
    args = ap.parse_args(argv)
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    # 진입 산출용 베이스 전략 (진입 파라미터는 조합 무관 동일).
    base = LivePppScalping()
    panels = {}
    entries_by_sym = {}
    for s in symbols:
        p = _load_15m(s)
        if p is None:
            print(f"  skip {s} (no data)", flush=True)
            continue
        panels[s] = p
        entries_by_sym[s] = _entries(p, base)
        print(f"  {s}: bars={len(p)} entries={len(entries_by_sym[s])} "
              f"({p.index[0].date()}~{p.index[-1].date()})", flush=True)

    results = []
    for cfg in _CONFIGS:
        strat = LivePppScalping(sl_mode=cfg["sl_mode"], tp_mode=cfg["tp_mode"])
        all_trades = []
        for s in panels:
            all_trades += _simulate(panels[s], entries_by_sym[s], strat, args.cost_bps)
        m = _metrics(all_trades)
        m["config"] = cfg["name"]
        m["sl_mode"] = cfg["sl_mode"]; m["tp_mode"] = cfg["tp_mode"]
        m["gate_pass"] = bool(m["pf"] > 1.0 and m["expectancy"] > 0)
        results.append(m)
        print(f"[{cfg['name']:<12}] trades={m['trades']:<6} PF={m['pf']:<7} "
              f"exp={m['expectancy']*100:+.3f}% win={m['win_rate']*100:.1f}% "
              f"MDD={m['mdd']*100:.1f}% hold={m['avg_hold']}봉 "
              f"GATE={'PASS' if m['gate_pass'] else 'FAIL'}", flush=True)

    out = {"symbols": list(panels.keys()), "cost_bps_roundtrip": args.cost_bps * 2,
           "interval": "15m", "results": results}
    if args.output:
        Path(args.output).write_text(json.dumps(out, indent=2, ensure_ascii=False))
        print("wrote", args.output, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
