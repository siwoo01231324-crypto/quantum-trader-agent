"""다른 시그널 family 5y 비교 — BB 평균회귀 (PF 1.18 천장) 대비 더 강한
알파가 다른 family 에 있는지 확인.

같은 panel/period/cost/R-R 로 다음 family 들을 1h 5y 24-symbol 평가:

A. Momentum / Breakout
   - donchian20 (20-bar high/low breakout)
   - donchian55 (Turtle medium)
   - atr_breakout (N=2.5 ATR from entry)

B. Trend-following
   - ema20_50_cross
   - macd_cross
   - heikin_ashi_reversal

C. Oscillator
   - rsi_extreme_25_75 (mean-rev: long<25, short>75 with confirm)
   - stoch_extreme

D. Volume
   - volume_spike_trend (vol > 2×MA20 + same direction body)
   - obv_divergence (lite)

E. Hybrid
   - donchian20 + volume_confirm

각 family 결과 비교 + BB+wick 0.5 baseline (PF 1.177) 와 직접 대조.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

bench = importlib.import_module("bench_live_airborne_kst_morning_5y")
sweep_v1 = importlib.import_module("bench_airborne_filter_sweep_5y")
logger = logging.getLogger("bench_signal_families_5y")


# ── Common helpers ───────────────────────────────────────────────────────────
def _wilder_atr(high, low, close, period):
    return bench._wilder_atr(high, low, close, period)


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) < period:
        return out
    a = 2.0 / (period + 1)
    out[period - 1] = float(np.nanmean(arr[:period]))
    for i in range(period, len(arr)):
        if np.isnan(out[i - 1]):
            out[i] = arr[i]
        else:
            out[i] = a * arr[i] + (1 - a) * out[i - 1]
    return out


def _rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    n = len(close)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    delta = np.diff(close)
    up = np.where(delta > 0, delta, 0.0)
    dn = np.where(delta < 0, -delta, 0.0)
    avg_u = up[:period].mean()
    avg_d = dn[:period].mean()
    out[period] = 100 - 100 / (1 + (avg_u / avg_d if avg_d > 0 else 1e9))
    for i in range(period + 1, n):
        avg_u = (avg_u * (period - 1) + up[i - 1]) / period
        avg_d = (avg_d * (period - 1) + dn[i - 1]) / period
        out[i] = 100 - 100 / (1 + (avg_u / avg_d if avg_d > 0 else 1e9))
    return out


# ── Signal generators — each returns list[(bar_idx, side, entry_price)] ──────
def sig_donchian(panel: pd.DataFrame, period: int = 20) -> list[tuple]:
    h = panel["high"].to_numpy(); l = panel["low"].to_numpy()
    c = panel["close"].to_numpy()
    n = len(c)
    fires = []
    for i in range(period, n):
        hh = h[i - period:i].max()
        ll = l[i - period:i].min()
        if c[i] > hh:
            fires.append((i, "long", c[i]))
        elif c[i] < ll:
            fires.append((i, "short", c[i]))
    return fires


def sig_atr_breakout(panel: pd.DataFrame, atr_mult: float = 2.5) -> list[tuple]:
    """N봉 ATR 만큼 직전 close 위/아래 돌파 시 진입 (Turtle-style)."""
    c = panel["close"].to_numpy()
    h = panel["high"].to_numpy(); l = panel["low"].to_numpy()
    atr = _wilder_atr(h, l, c, 14)
    n = len(c)
    fires = []
    for i in range(20, n):
        if np.isnan(atr[i]):
            continue
        prev_close = c[i - 1]
        if c[i] > prev_close + atr_mult * atr[i]:
            fires.append((i, "long", c[i]))
        elif c[i] < prev_close - atr_mult * atr[i]:
            fires.append((i, "short", c[i]))
    return fires


def sig_ema_cross(panel: pd.DataFrame, fast: int = 20, slow: int = 50,
                  ) -> list[tuple]:
    c = panel["close"].to_numpy()
    ef = _ema(c, fast); es = _ema(c, slow)
    n = len(c); fires = []
    for i in range(slow + 1, n):
        if np.isnan(ef[i]) or np.isnan(es[i]) or np.isnan(ef[i - 1]) or np.isnan(es[i - 1]):
            continue
        # bullish cross: ef crosses above es
        if ef[i - 1] <= es[i - 1] and ef[i] > es[i]:
            fires.append((i, "long", c[i]))
        elif ef[i - 1] >= es[i - 1] and ef[i] < es[i]:
            fires.append((i, "short", c[i]))
    return fires


def sig_macd_cross(panel: pd.DataFrame, fast: int = 12, slow: int = 26,
                   sig_period: int = 9) -> list[tuple]:
    c = panel["close"].to_numpy()
    ef = _ema(c, fast); es = _ema(c, slow)
    macd = ef - es
    sig = _ema(macd, sig_period)
    n = len(c); fires = []
    for i in range(slow + sig_period + 1, n):
        if np.isnan(macd[i]) or np.isnan(sig[i]) or np.isnan(macd[i - 1]) or np.isnan(sig[i - 1]):
            continue
        if macd[i - 1] <= sig[i - 1] and macd[i] > sig[i]:
            fires.append((i, "long", c[i]))
        elif macd[i - 1] >= sig[i - 1] and macd[i] < sig[i]:
            fires.append((i, "short", c[i]))
    return fires


def sig_heikin_ashi_reversal(panel: pd.DataFrame) -> list[tuple]:
    """HA 봉 색깔이 3봉 연속 같다가 4번째 봉에 반대로 변하면 진입."""
    o = panel["open"].to_numpy(); h = panel["high"].to_numpy()
    l = panel["low"].to_numpy(); c = panel["close"].to_numpy()
    n = len(c)
    ha_c = (o + h + l + c) / 4.0
    ha_o = np.full(n, np.nan)
    ha_o[0] = (o[0] + c[0]) / 2.0
    for i in range(1, n):
        ha_o[i] = (ha_o[i - 1] + ha_c[i - 1]) / 2.0
    color = np.where(ha_c > ha_o, 1, np.where(ha_c < ha_o, -1, 0))
    fires = []
    for i in range(4, n):
        # 3봉 음봉 → 양봉 = long
        if color[i - 3] == -1 and color[i - 2] == -1 and color[i - 1] == -1 and color[i] == 1:
            fires.append((i, "long", c[i]))
        # 3봉 양봉 → 음봉 = short
        elif color[i - 3] == 1 and color[i - 2] == 1 and color[i - 1] == 1 and color[i] == -1:
            fires.append((i, "short", c[i]))
    return fires


def sig_rsi_extreme(panel: pd.DataFrame, lo: float = 25, hi: float = 75,
                    ) -> list[tuple]:
    """RSI(14) < lo + 다음 봉 양봉 → long. RSI > hi + 다음 봉 음봉 → short."""
    c = panel["close"].to_numpy(); o = panel["open"].to_numpy()
    rsi = _rsi(c, 14)
    n = len(c); fires = []
    for i in range(15, n):
        if np.isnan(rsi[i]) or np.isnan(rsi[i - 1]):
            continue
        if rsi[i - 1] < lo and c[i] > o[i]:
            fires.append((i, "long", c[i]))
        elif rsi[i - 1] > hi and c[i] < o[i]:
            fires.append((i, "short", c[i]))
    return fires


def sig_volume_spike_trend(panel: pd.DataFrame, vol_mult: float = 2.0,
                           ma: int = 20) -> list[tuple]:
    """volume > vol_mult × MA(20) AND 양봉 → long, 음봉 → short."""
    v = panel["volume"].to_numpy(); c = panel["close"].to_numpy()
    o = panel["open"].to_numpy()
    vma = pd.Series(v).rolling(ma).mean().to_numpy()
    n = len(c); fires = []
    for i in range(ma + 1, n):
        if np.isnan(vma[i]) or vma[i] <= 0:
            continue
        if v[i] > vol_mult * vma[i]:
            if c[i] > o[i]:
                fires.append((i, "long", c[i]))
            elif c[i] < o[i]:
                fires.append((i, "short", c[i]))
    return fires


def sig_donchian_volume_confirm(
    panel: pd.DataFrame, period: int = 20, vol_mult: float = 1.5,
) -> list[tuple]:
    """Donchian 20 breakout + volume > 1.5×MA(20) 확인."""
    h = panel["high"].to_numpy(); l = panel["low"].to_numpy()
    c = panel["close"].to_numpy(); v = panel["volume"].to_numpy()
    vma = pd.Series(v).rolling(20).mean().to_numpy()
    n = len(c); fires = []
    for i in range(period, n):
        if np.isnan(vma[i]) or vma[i] <= 0:
            continue
        if v[i] < vol_mult * vma[i]:
            continue
        hh = h[i - period:i].max()
        ll = l[i - period:i].min()
        if c[i] > hh:
            fires.append((i, "long", c[i]))
        elif c[i] < ll:
            fires.append((i, "short", c[i]))
    return fires


SIGNALS = {
    "donchian20": lambda p: sig_donchian(p, 20),
    "donchian55": lambda p: sig_donchian(p, 55),
    "atr_breakout_2.5": lambda p: sig_atr_breakout(p, 2.5),
    "ema_20_50_cross": lambda p: sig_ema_cross(p, 20, 50),
    "ema_50_200_cross": lambda p: sig_ema_cross(p, 50, 200),
    "macd_12_26_9": lambda p: sig_macd_cross(p),
    "heikin_ashi_4bar_rev": lambda p: sig_heikin_ashi_reversal(p),
    "rsi_25_75": lambda p: sig_rsi_extreme(p, 25, 75),
    "rsi_30_70": lambda p: sig_rsi_extreme(p, 30, 70),
    "volume_spike_2x_trend": lambda p: sig_volume_spike_trend(p, 2.0),
    "volume_spike_3x_trend": lambda p: sig_volume_spike_trend(p, 3.0),
    "donchian20 + vol1.5x": lambda p: sig_donchian_volume_confirm(p, 20, 1.5),
}


def simulate_trades(
    panel: pd.DataFrame, fires: list[tuple],
    stop: float, tp: float, cost_bps: float,
) -> list[dict]:
    """공통 trade 시뮬레이터 — fires list 받아 stop/TP 청산. 1포지션 unique."""
    highs = panel["high"].to_numpy()
    lows = panel["low"].to_numpy()
    times = panel.index
    if times.tz is None:
        times = times.tz_localize("UTC")
    n = len(panel)
    cost = cost_bps / 10000.0
    trades = []
    in_pos = False
    pos_side = None
    pos_entry = 0.0
    pos_entry_i = 0
    pos_sl = 0.0; pos_tp = 0.0
    fire_idx = 0

    for i in range(n):
        if in_pos:
            if pos_side == "long":
                if lows[i] <= pos_sl:
                    ret = (pos_sl / pos_entry) - 1 - 2 * cost
                    trades.append({"side": "long",
                                   "entry_ts": times[pos_entry_i].isoformat(),
                                   "exit_ts": times[i].isoformat(),
                                   "entry": pos_entry, "exit": pos_sl,
                                   "ret": ret, "exit_reason": "stop_loss"})
                    in_pos = False
                elif highs[i] >= pos_tp:
                    ret = (pos_tp / pos_entry) - 1 - 2 * cost
                    trades.append({"side": "long",
                                   "entry_ts": times[pos_entry_i].isoformat(),
                                   "exit_ts": times[i].isoformat(),
                                   "entry": pos_entry, "exit": pos_tp,
                                   "ret": ret, "exit_reason": "take_profit"})
                    in_pos = False
            else:
                if highs[i] >= pos_sl:
                    ret = 1 - (pos_sl / pos_entry) - 2 * cost
                    trades.append({"side": "short",
                                   "entry_ts": times[pos_entry_i].isoformat(),
                                   "exit_ts": times[i].isoformat(),
                                   "entry": pos_entry, "exit": pos_sl,
                                   "ret": ret, "exit_reason": "stop_loss"})
                    in_pos = False
                elif lows[i] <= pos_tp:
                    ret = 1 - (pos_tp / pos_entry) - 2 * cost
                    trades.append({"side": "short",
                                   "entry_ts": times[pos_entry_i].isoformat(),
                                   "exit_ts": times[i].isoformat(),
                                   "entry": pos_entry, "exit": pos_tp,
                                   "ret": ret, "exit_reason": "take_profit"})
                    in_pos = False
        if not in_pos and fire_idx < len(fires) and fires[fire_idx][0] == i:
            _, side, entry = fires[fire_idx]
            in_pos = True
            pos_side = side; pos_entry = entry; pos_entry_i = i
            if side == "long":
                pos_sl = entry * (1 - stop); pos_tp = entry * (1 + tp)
            else:
                pos_sl = entry * (1 + stop); pos_tp = entry * (1 - tp)
            fire_idx += 1
        elif fire_idx < len(fires) and fires[fire_idx][0] <= i:
            while fire_idx < len(fires) and fires[fire_idx][0] <= i:
                fire_idx += 1
    return trades


def run_family(panels, name, sig_fn, stop, tp, cost_bps):
    all_trades = []
    for sym, panel in panels.items():
        fires = sig_fn(panel)
        all_trades.extend(simulate_trades(panel, fires, stop, tp, cost_bps))
    agg = sweep_v1.aggregate(all_trades)
    return {"name": name, "stop": stop, "tp": tp, **agg}


async def _main_async(args):
    t0 = time.time()
    syms = bench._load_universe_symbols(args.top_n)
    panels, _ = bench._load_panels(syms, args.months, args.freq)
    if not panels:
        return 3
    print("\n" + "=" * 130)
    print(f"signal family bench — {args.freq}, {args.months}mo, {len(panels)} symbols, cost {args.cost_bps}bp, R/R {args.stop*100}%/{args.tp*100}%")
    print("=" * 130)

    results = []
    for name, fn in SIGNALS.items():
        c0 = time.time()
        r = await asyncio.to_thread(
            run_family, panels, name, fn, args.stop, args.tp, args.cost_bps,
        )
        results.append(r)
        pf = r.get("PF"); pf_t = f"{pf:.3f}" if pf is not None else "-"
        logger.info("  %s: PF=%s exp=%+.4f%% n=%d (%.1fs)",
                    name, pf_t, (r.get("exp") or 0) * 100, r["trades"],
                    time.time() - c0)

    print(f"\n  {'signal':<28} {'trades':>7}  {'PF':>6}  {'exp':>9}  "
          f"{'L n/PF':>14}  {'S n/PF':>14}  verdict")
    print("  " + "-" * 116)
    for r in sorted(results, key=lambda x: -(x["PF"] or 0)):
        pf = r["PF"]; exp = r.get("exp") or 0
        pf_t = f"{pf:6.3f}" if pf is not None else "  -   "
        verdict = "PASS" if (pf is not None and pf > 1.0 and exp > 0) else "LOSER"
        lpf = f"{r['long_PF']:5.2f}" if r['long_PF'] is not None else "  -  "
        spf = f"{r['short_PF']:5.2f}" if r['short_PF'] is not None else "  -  "
        print(f"  {r['name']:<28} {r['trades']:>7}  {pf_t}  {exp*100:+8.5f}%  "
              f"L={r['long_n']:>5}/{lpf}  S={r['short_n']:>5}/{spf}  {verdict}")

    print(f"\n  vs. BB airborne + BBW + wick≥0.5 (PF 1.177, n=320) ← 현재까지 최고")

    pass_count = sum(1 for r in results
                     if r["PF"] is not None and r["PF"] > 1.0 and (r["exp"] or 0) > 0)
    best = max(results, key=lambda r: ((r["PF"] or 0), r.get("exp") or 0))
    pf_t = f"{best['PF']:.3f}" if best['PF'] is not None else "-"
    print()
    print("=" * 130)
    print(f"BEST: {best['name']}  PF={pf_t}  exp={(best.get('exp') or 0)*100:+.5f}%  "
          f"n={best['trades']}  | PASS: {pass_count}/{len(results)}")
    print("=" * 130)

    out_path = _REPO_ROOT / "reports" / "signal_families_5y.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": args.months, "symbols_count": len(panels),
        "cost_bps": args.cost_bps, "stop": args.stop, "tp": args.tp,
        "results": results, "best": best, "pass_count": pass_count,
        "elapsed_sec": round(time.time() - t0, 1),
    }, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(_REPO_ROOT).as_posix()}")
    return 0


def _parse(argv=None):
    p = argparse.ArgumentParser(prog="bench_signal_families_5y")
    p.add_argument("--months", type=int, default=60)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--stop", type=float, default=0.03)
    p.add_argument("--tp", type=float, default=0.06)
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--freq", type=str, default="1h", choices=["1h", "4h", "15m"])
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
