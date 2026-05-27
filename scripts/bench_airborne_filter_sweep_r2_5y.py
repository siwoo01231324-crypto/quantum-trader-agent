"""Round 2 filter sweep — 영상 (오더블록 + 멀티-근거) + 웹 리서치 (VWAP z-score,
delta proxy, value-area, wick rejection) 인사이트 적용.

[[round 1 결과]] = BB family ceiling PF 0.926 (BBW P40-P80 단독). 본 라운드는
*시그널 자체* 도 손대지 않고 *외부 신호와 confluence* 만 추가.

추가 필터:
1. **VWAP z-score** — (close - vwap) z-score (168 lookback). long 은 z<-1.5,
   short 은 z>+1.5 일 때만 진입.
2. **delta proxy** — (close-open)/range. long 은 ≤+0.3 (closed soft, 안 chase),
   short 은 ≥-0.3.
3. **order block** — 직전 N=10봉 안에 engulfing OB 가 있어야 진입 (영상의
   "근거 2개 이상" 원칙).
4. **wick rejection** — long 은 lower_wick/range >= 0.5, short 은 upper/range
   >= 0.5 (영상 + 웹 모두 언급).
5. **value area fade (VAH/VAL)** — 7d/168봉 volume profile 의 70% value area
   바깥에서만 fade (long 은 < VAL, short 은 > VAH).

각각 단독 + BBW 와 결합한 변종 등 ~12 시나리오.

Output: ``reports/airborne_filter_sweep_r2_5y.json``
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

import signals  # noqa: E402
from signals.airborne_bb_reversal import (  # noqa: E402
    DEFAULT_ATR_BODY_MULT_V11, DEFAULT_ATR_PERIOD_V11,
    DEFAULT_MIN_CLOSE_MARGIN_V11, RETRACE_RATIO,
)

bench = importlib.import_module("bench_live_airborne_kst_morning_5y")
sweep_v1 = importlib.import_module("bench_airborne_filter_sweep_5y")
logger = logging.getLogger("bench_airborne_filter_sweep_r2_5y")

BB_WINDOW = 20
BB_STD = 2.0


# ── derived indicators ────────────────────────────────────────────────────────
def _vwap_zscore(panel: pd.DataFrame, lookback: int = 168) -> np.ndarray:
    """Rolling VWAP deviation z-score (1h panel)."""
    close = panel["close"].to_numpy()
    high = panel["high"].to_numpy()
    low = panel["low"].to_numpy()
    vol = panel["volume"].to_numpy()
    tp = (high + low + close) / 3.0  # typical price
    n = len(close)
    z = np.full(n, np.nan)
    cum_pv = np.cumsum(tp * vol)
    cum_v = np.cumsum(vol) + 1e-9
    vwap_full = cum_pv / cum_v
    dev = (close - vwap_full) / vwap_full
    for i in range(lookback, n):
        w = dev[i - lookback:i + 1]
        mu = float(np.nanmean(w)); sd = float(np.nanstd(w))
        if sd > 1e-9:
            z[i] = (dev[i] - mu) / sd
    return z


def _delta_proxy(panel: pd.DataFrame) -> np.ndarray:
    close = panel["close"].to_numpy()
    open_ = panel["open"].to_numpy()
    high = panel["high"].to_numpy()
    low = panel["low"].to_numpy()
    rng = high - low + 1e-12
    return (close - open_) / rng  # -1..+1


def _wick_ratio(panel: pd.DataFrame, side: str) -> np.ndarray:
    """side='long' → lower_wick/range, 'short' → upper_wick/range."""
    close = panel["close"].to_numpy()
    open_ = panel["open"].to_numpy()
    high = panel["high"].to_numpy()
    low = panel["low"].to_numpy()
    rng = high - low + 1e-12
    body_top = np.maximum(open_, close)
    body_bot = np.minimum(open_, close)
    if side == "long":
        wick = body_bot - low
    else:
        wick = high - body_top
    return wick / rng


def _engulf_recent(panel: pd.DataFrame, lookback: int, side: str) -> np.ndarray:
    """직전 lookback 봉 안에 side 방향 engulfing 이 있으면 True.

    bullish engulf (long용): bar i 양봉 + body(i) 가 직전 음봉 body(i-1) 완전포함
    bearish engulf (short용): bar i 음봉 + body(i) 가 직전 양봉 body(i-1) 완전포함
    """
    close = panel["close"].to_numpy()
    open_ = panel["open"].to_numpy()
    n = len(close)
    flag = np.zeros(n, dtype=bool)
    is_bull = close > open_
    is_bear = close < open_
    body_top = np.maximum(open_, close)
    body_bot = np.minimum(open_, close)
    if side == "long":
        engulf = (
            is_bull[1:] & is_bear[:-1]
            & (body_top[1:] >= body_top[:-1])
            & (body_bot[1:] <= body_bot[:-1])
        )
    else:
        engulf = (
            is_bear[1:] & is_bull[:-1]
            & (body_top[1:] >= body_top[:-1])
            & (body_bot[1:] <= body_bot[:-1])
        )
    engulf = np.concatenate([[False], engulf])
    # 최근 lookback 봉 안에 engulf 있었는가?
    for i in range(n):
        s = max(0, i - lookback)
        flag[i] = engulf[s:i + 1].any() if i > 0 else False
    return flag


def _value_area_bounds(panel: pd.DataFrame, lookback: int = 168) -> tuple[np.ndarray, np.ndarray]:
    """Rolling 168봉 volume profile 의 VAL / VAH (70% value area).

    20 가격 버킷 히스토그램 → POC 중심으로 인접 버킷 누적해 70% 차지하는 범위.
    """
    close = panel["close"].to_numpy()
    vol = panel["volume"].to_numpy()
    n = len(close)
    val_arr = np.full(n, np.nan)
    vah_arr = np.full(n, np.nan)
    n_bins = 20
    for i in range(lookback, n):
        w_close = close[i - lookback:i + 1]
        w_vol = vol[i - lookback:i + 1]
        lo, hi = float(w_close.min()), float(w_close.max())
        if hi <= lo:
            continue
        edges = np.linspace(lo, hi, n_bins + 1)
        hist, _ = np.histogram(w_close, bins=edges, weights=w_vol)
        total = hist.sum()
        if total <= 0:
            continue
        poc_idx = int(hist.argmax())
        cum = hist[poc_idx]; target = total * 0.7
        left = right = poc_idx
        while cum < target and (left > 0 or right < n_bins - 1):
            l_v = hist[left - 1] if left > 0 else -1
            r_v = hist[right + 1] if right < n_bins - 1 else -1
            if l_v >= r_v:
                left -= 1; cum += hist[left]
            else:
                right += 1; cum += hist[right]
        val_arr[i] = edges[left]
        vah_arr[i] = edges[right + 1]
    return val_arr, vah_arr


# ── core simulate ────────────────────────────────────────────────────────────
def simulate_with_round2_filters(
    panel: pd.DataFrame,
    *,
    stop: float,
    tp: float,
    cost_bps: float,
    # round 1 옵션 (재사용)
    bbw_regime: bool = False,
    bbw_lo: float = 0.40,
    bbw_hi: float = 0.80,
    # round 2 옵션
    vwap_z_gate: bool = False,
    vwap_z_thresh: float = 1.5,
    delta_proxy_gate: bool = False,
    delta_thresh: float = 0.3,
    ob_recent_gate: bool = False,
    ob_lookback: int = 10,
    wick_rejection_gate: bool = False,
    wick_thresh: float = 0.5,
    value_area_gate: bool = False,
) -> list[dict]:
    bb = signals.compute("bollinger", close=panel["close"],
                         window=BB_WINDOW, n_std=BB_STD)
    upper = bb["upper"].to_numpy()
    lower = bb["lower"].to_numpy()
    closes = panel["close"].to_numpy()
    opens = panel["open"].to_numpy()
    highs = panel["high"].to_numpy()
    lows = panel["low"].to_numpy()
    body_abs = np.abs(closes - opens)
    atr = bench._wilder_atr(highs, lows, closes, DEFAULT_ATR_PERIOD_V11)
    upper_thr = upper * (1 + DEFAULT_MIN_CLOSE_MARGIN_V11)
    lower_thr = lower * (1 - DEFAULT_MIN_CLOSE_MARGIN_V11)
    n = len(panel)
    times = panel.index
    if times.tz is None:
        times = times.tz_localize("UTC")

    bbw_pct = sweep_v1._bbw_percentile(panel, 250) if bbw_regime else None
    vwap_z = _vwap_zscore(panel, 168) if vwap_z_gate else None
    delta = _delta_proxy(panel) if delta_proxy_gate else None
    ob_long = _engulf_recent(panel, ob_lookback, "long") if ob_recent_gate else None
    ob_short = _engulf_recent(panel, ob_lookback, "short") if ob_recent_gate else None
    wick_lo = _wick_ratio(panel, "long") if wick_rejection_gate else None
    wick_up = _wick_ratio(panel, "short") if wick_rejection_gate else None
    if value_area_gate:
        val_arr, vah_arr = _value_area_bounds(panel, 168)
    else:
        val_arr = vah_arr = None

    upper_break = np.zeros(n, dtype=bool)
    lower_break = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if (np.isnan(upper_thr[i]) or np.isnan(upper_thr[i - 1])
                or np.isnan(atr[i])):
            continue
        if body_abs[i] < DEFAULT_ATR_BODY_MULT_V11 * atr[i]:
            continue
        if closes[i] > upper_thr[i] and closes[i - 1] <= upper_thr[i - 1]:
            upper_break[i] = True
        elif closes[i] < lower_thr[i] and closes[i - 1] >= lower_thr[i - 1]:
            lower_break[i] = True

    state = 0; base = np.nan; extreme = np.nan
    fires = []
    for i in range(n):
        if state == 0:
            if upper_break[i]:
                state, base, extreme = 2, closes[i], highs[i]
            elif lower_break[i]:
                state, base, extreme = 1, closes[i], lows[i]
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, lows[i])
            trig = extreme + RETRACE_RATIO * (base - extreme)
            if closes[i] >= trig:
                fires.append((i, "long", closes[i]))
                state, base, extreme = 0, np.nan, np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, highs[i])
            trig = extreme - RETRACE_RATIO * (extreme - base)
            if closes[i] <= trig:
                fires.append((i, "short", closes[i]))
                state, base, extreme = 0, np.nan, np.nan

    trades = []
    in_pos = False
    pos_side = None; pos_entry = 0.0; pos_entry_i = 0
    pos_tp_px = 0.0; pos_sl_px = 0.0
    fire_idx = 0
    cost = cost_bps / 10000.0

    for i in range(n):
        if in_pos:
            exit_reason, exit_px = None, None
            if pos_side == "long":
                if lows[i] <= pos_sl_px:
                    exit_reason, exit_px = "stop_loss", pos_sl_px
                elif highs[i] >= pos_tp_px:
                    exit_reason, exit_px = "take_profit", pos_tp_px
                if exit_reason:
                    ret = (exit_px / pos_entry) - 1 - 2 * cost
                    trades.append({"side": "long",
                                   "entry_ts": times[pos_entry_i].isoformat(),
                                   "exit_ts": times[i].isoformat(),
                                   "entry": pos_entry, "exit": exit_px,
                                   "ret": ret, "exit_reason": exit_reason})
                    in_pos = False
            else:
                if highs[i] >= pos_sl_px:
                    exit_reason, exit_px = "stop_loss", pos_sl_px
                elif lows[i] <= pos_tp_px:
                    exit_reason, exit_px = "take_profit", pos_tp_px
                if exit_reason:
                    ret = 1 - (exit_px / pos_entry) - 2 * cost
                    trades.append({"side": "short",
                                   "entry_ts": times[pos_entry_i].isoformat(),
                                   "exit_ts": times[i].isoformat(),
                                   "entry": pos_entry, "exit": exit_px,
                                   "ret": ret, "exit_reason": exit_reason})
                    in_pos = False

        if not in_pos and fire_idx < len(fires) and fires[fire_idx][0] == i:
            _, side, entry = fires[fire_idx]
            allow = True
            # BBW regime
            if allow and bbw_regime:
                p = bbw_pct[i]
                if np.isnan(p) or p < bbw_lo or p > bbw_hi:
                    allow = False
            # VWAP z-score
            if allow and vwap_z_gate:
                z = vwap_z[i]
                if np.isnan(z):
                    allow = False
                elif side == "long" and z > -vwap_z_thresh:
                    allow = False
                elif side == "short" and z < vwap_z_thresh:
                    allow = False
            # Delta proxy (anti-chasing)
            if allow and delta_proxy_gate:
                d = delta[i]
                if side == "long" and d > delta_thresh:
                    allow = False
                elif side == "short" and d < -delta_thresh:
                    allow = False
            # Order block confirmation
            if allow and ob_recent_gate:
                ok = ob_long[i] if side == "long" else ob_short[i]
                if not ok:
                    allow = False
            # Wick rejection
            if allow and wick_rejection_gate:
                w = wick_lo[i] if side == "long" else wick_up[i]
                if w < wick_thresh:
                    allow = False
            # Value area fade
            if allow and value_area_gate:
                v_lo = val_arr[i]; v_hi = vah_arr[i]
                if np.isnan(v_lo) or np.isnan(v_hi):
                    allow = False
                elif side == "long" and entry > v_lo:
                    allow = False
                elif side == "short" and entry < v_hi:
                    allow = False

            if allow:
                in_pos = True
                pos_side = side; pos_entry = entry; pos_entry_i = i
                if side == "long":
                    pos_sl_px = entry * (1 - stop)
                    pos_tp_px = entry * (1 + tp)
                else:
                    pos_sl_px = entry * (1 + stop)
                    pos_tp_px = entry * (1 - tp)
            fire_idx += 1
        elif fire_idx < len(fires) and fires[fire_idx][0] <= i:
            while fire_idx < len(fires) and fires[fire_idx][0] <= i:
                fire_idx += 1
    return trades


# ── scenario sweep ───────────────────────────────────────────────────────────
SCENARIOS = [
    ("baseline (3%/6%)", {}),
    ("round1 best: BBW P40-P80", dict(bbw_regime=True)),
    # round 2 단독
    ("VWAP z>=1.5", dict(vwap_z_gate=True, vwap_z_thresh=1.5)),
    ("VWAP z>=2.0", dict(vwap_z_gate=True, vwap_z_thresh=2.0)),
    ("delta proxy |.|<=0.3", dict(delta_proxy_gate=True, delta_thresh=0.3)),
    ("delta proxy |.|<=0.5", dict(delta_proxy_gate=True, delta_thresh=0.5)),
    ("OB recent 10", dict(ob_recent_gate=True, ob_lookback=10)),
    ("OB recent 5", dict(ob_recent_gate=True, ob_lookback=5)),
    ("wick rejection>=0.5", dict(wick_rejection_gate=True, wick_thresh=0.5)),
    ("wick rejection>=0.4", dict(wick_rejection_gate=True, wick_thresh=0.4)),
    ("value area fade (VAH/VAL)", dict(value_area_gate=True)),
    # round 2 조합
    ("BBW + VWAP z1.5", dict(bbw_regime=True, vwap_z_gate=True)),
    ("BBW + delta 0.3", dict(bbw_regime=True, delta_proxy_gate=True)),
    ("BBW + OB10", dict(bbw_regime=True, ob_recent_gate=True)),
    ("BBW + wick 0.5", dict(bbw_regime=True, wick_rejection_gate=True)),
    ("BBW + value-area", dict(bbw_regime=True, value_area_gate=True)),
    ("BBW + VWAP z + delta", dict(bbw_regime=True, vwap_z_gate=True, delta_proxy_gate=True)),
    ("BBW + VWAP z + OB10", dict(bbw_regime=True, vwap_z_gate=True, ob_recent_gate=True)),
    ("ALL r2 (BBW+VWAP+delta+OB+wick+VA)",
        dict(bbw_regime=True, vwap_z_gate=True, delta_proxy_gate=True,
             ob_recent_gate=True, wick_rejection_gate=True, value_area_gate=True)),
]


def run_scenario(panels, name, kw, cost_bps, base_stop, base_tp):
    stop = kw.get("stop", base_stop)
    tp = kw.get("tp", base_tp)
    filter_kw = {k: v for k, v in kw.items() if k not in ("stop", "tp")}
    all_trades = []
    for sym, panel in panels.items():
        all_trades.extend(simulate_with_round2_filters(
            panel, stop=stop, tp=tp, cost_bps=cost_bps, **filter_kw,
        ))
    return {"name": name, "stop": stop, "tp": tp, **filter_kw,
            **sweep_v1.aggregate(all_trades)}


async def _main_async(args):
    t0 = time.time()
    symbols = bench._load_universe_symbols(args.top_n)
    panels, coverage = bench._load_panels(symbols, args.months, "1h")
    if not panels:
        return 3
    print("\n" + "=" * 130)
    print(f"airborne filter sweep — Round 2 (video + web research insights)")
    print(f"  months={args.months}  symbols={len(panels)}  cost={args.cost_bps:.0f}bp  R/R 3%/6%")
    print("=" * 130)

    results = []
    for name, kw in SCENARIOS:
        c0 = time.time()
        r = await asyncio.to_thread(
            run_scenario, panels, name, kw, args.cost_bps, args.stop, args.tp,
        )
        results.append(r)
        pf = r.get("PF")
        logger.info("  %s: PF=%s exp=%+.5f%% n=%d (%.1fs)", name,
                    f"{pf:.3f}" if pf is not None else "-",
                    (r["exp"] or 0) * 100, r["trades"], time.time() - c0)

    print(f"\n  {'scenario':<48} {'trades':>6}  {'PF':>6}  {'exp':>9}  "
          f"{'long_n/PF':>14}  {'short_n/PF':>14}  verdict")
    print("  " + "-" * 124)
    for r in sorted(results, key=lambda x: -(x["PF"] or 0)):
        pf = r["PF"]; exp = r["exp"]
        pf_t = f"{pf:6.3f}" if pf is not None else "  -   "
        verdict = "PASS" if (pf is not None and pf > 1.0 and (exp or 0) > 0) else "LOSER"
        lpf = f"{r['long_PF']:5.2f}" if r['long_PF'] is not None else "  -  "
        spf = f"{r['short_PF']:5.2f}" if r['short_PF'] is not None else "  -  "
        print(f"  {r['name']:<48} {r['trades']:>6}  {pf_t}  "
              f"{(exp or 0)*100:+8.5f}%  "
              f"L={r['long_n']:>5}/{lpf}  S={r['short_n']:>5}/{spf}  {verdict}")

    best = max(results, key=lambda r: ((r["PF"] or 0), r["exp"] or 0))
    pass_count = sum(
        1 for r in results
        if (r["PF"] is not None and r["PF"] > 1.0 and (r["exp"] or 0) > 0)
    )
    print()
    print("=" * 130)
    pf_t = f"{best['PF']:.3f}" if best['PF'] is not None else "-"
    print(f"BEST: {best['name']}  PF={pf_t}  exp={(best['exp'] or 0)*100:+.5f}%  "
          f"n={best['trades']}  | PASS scenarios: {pass_count}/{len(results)}")
    print("=" * 130)

    out_path = _REPO_ROOT / "reports" / "airborne_filter_sweep_r2_5y.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": args.months, "symbols_count": len(panels),
        "cost_bps": args.cost_bps, "base_stop": args.stop, "base_tp": args.tp,
        "scenarios": results, "best": best,
        "pass_count": pass_count, "elapsed_sec": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str),
                        encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(_REPO_ROOT).as_posix()}")
    return 0


def _parse(argv=None):
    p = argparse.ArgumentParser(prog="bench_airborne_filter_sweep_r2_5y")
    p.add_argument("--months", type=int, default=60)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--stop", type=float, default=0.03)
    p.add_argument("--tp", type=float, default=0.06)
    p.add_argument("--cost-bps", type=float, default=10.0)
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = _parse(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
