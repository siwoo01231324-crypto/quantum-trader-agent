"""v11 5m-exit bench v2 — adds --rr-scale flag for stop/tp scaling.

Same as v1 but supports --rr-scale to multiply base R/R combos.
  --rr-scale 1.0  → coin 0.05%~0.30% stop (1/10 of 0.5%~3% original)
  --rr-scale 2.0  → coin 0.10%~0.60% stop (1/5)
  --rr-scale 5.0  → coin 0.25%~1.50% stop (1/2)
  --rr-scale 10.0 → coin 0.50%~3.00% stop (original)
"""
from __future__ import annotations
import argparse, gc, logging, sys, time
from pathlib import Path
import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))
import signals
from signals.airborne_bb_reversal import RETRACE_RATIO

logger = logging.getLogger("v11_5m_v2")

BASE_SWEEP_RR = [
    (0.0005, 0.0010, "0.05/0.10 (1:2)"),
    (0.0010, 0.0020, "0.10/0.20 (1:2)"),
    (0.0015, 0.0030, "0.15/0.30 (1:2)"),
    (0.0020, 0.0040, "0.20/0.40 (1:2)"),
    (0.0030, 0.0060, "0.30/0.60 (1:2)"),
    (0.0010, 0.0030, "0.10/0.30 (1:3)"),
    (0.0020, 0.0060, "0.20/0.60 (1:3)"),
    (0.0005, 0.0020, "0.05/0.20 (1:4)"),
]

BB_WINDOW = 20; BB_STD = 2.0; MIN_MARGIN = 0.001; MIN_BODY = 0.005


def _resample(df_1m, rule):
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (df_1m[cols].resample(rule, label="right", closed="right")
            .agg({k: agg[k] for k in cols}).dropna(subset=["close"]))


def _load_universe(months, symbols):
    cache_dir = _REPO / "data" / "cache" / "binance_1m"
    selected = {}
    for sym in symbols:
        path = cache_dir / f"{sym}.parquet"
        if not path.exists(): continue
        p1m = pd.read_parquet(path)
        if p1m.index.tz is None: p1m = p1m.tz_localize("UTC")
        first_ts = p1m.index.max() - pd.DateOffset(months=months)
        p1m = p1m.loc[first_ts:]
        panel_1h = _resample(p1m, "1h")
        panel_5m = _resample(p1m, "5min")
        if len(panel_1h) < 60: continue
        selected[sym] = (panel_1h, panel_5m)
        del p1m
    gc.collect()
    return selected


def extract_fires(panel_1h):
    bb = signals.compute("bollinger", close=panel_1h["close"], window=BB_WINDOW, n_std=BB_STD)
    upper, lower = bb["upper"].values, bb["lower"].values
    closes = panel_1h["close"].values
    opens = panel_1h["open"].values
    highs = panel_1h["high"].values
    lows = panel_1h["low"].values
    body_pct = np.abs(closes - opens) / np.where(opens > 0, opens, 1.0)
    upper_thr = upper * (1 + MIN_MARGIN)
    lower_thr = lower * (1 - MIN_MARGIN)
    n = len(panel_1h)
    ub = np.zeros(n, dtype=bool); lb = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(upper_thr[i]) or np.isnan(upper_thr[i-1]): continue
        ub[i] = (closes[i] > upper_thr[i]) and (closes[i-1] <= upper_thr[i-1]) and (body_pct[i] >= MIN_BODY)
        lb[i] = (closes[i] < lower_thr[i]) and (closes[i-1] >= lower_thr[i-1]) and (body_pct[i] >= MIN_BODY)
    state = 0; base = np.nan; extreme = np.nan
    fires = []; ts = panel_1h.index
    for i in range(n):
        if state == 0:
            if ub[i]:
                state = 2; base = closes[i]; extreme = highs[i]
            elif lb[i]:
                state = 1; base = closes[i]; extreme = lows[i]
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, lows[i])
            trig = extreme + RETRACE_RATIO * (base - extreme)
            if closes[i] >= trig:
                fires.append((ts[i], "long", float(closes[i])))
                state = 0; base = np.nan; extreme = np.nan
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, highs[i])
            trig = extreme - RETRACE_RATIO * (extreme - base)
            if closes[i] <= trig:
                fires.append((ts[i], "short", float(closes[i])))
                state = 0; base = np.nan; extreme = np.nan
    return fires


def execute_trade_5m(panel_5m, entry_ts, side, entry_px, stop, tp, cost_bps, max_hold=288):
    idx = panel_5m.index.searchsorted(entry_ts, side='right')
    if idx >= len(panel_5m): return None
    end = min(idx + max_hold, len(panel_5m))
    highs = panel_5m["high"].values
    lows = panel_5m["low"].values
    cost = cost_bps / 10000.0
    if side == "long":
        sl = entry_px * (1 - stop); tpp = entry_px * (1 + tp)
        for j in range(idx, end):
            if lows[j] <= sl:
                return {"side": "long", "ret": (sl/entry_px) - 1 - 2*cost, "reason": "stop_loss", "bars": j-idx+1}
            if highs[j] >= tpp:
                return {"side": "long", "ret": (tpp/entry_px) - 1 - 2*cost, "reason": "take_profit", "bars": j-idx+1}
    else:
        sl = entry_px * (1 + stop); tpp = entry_px * (1 - tp)
        for j in range(idx, end):
            if highs[j] >= sl:
                return {"side": "short", "ret": 1 - (sl/entry_px) - 2*cost, "reason": "stop_loss", "bars": j-idx+1}
            if lows[j] <= tpp:
                return {"side": "short", "ret": 1 - (tpp/entry_px) - 2*cost, "reason": "take_profit", "bars": j-idx+1}
    return None


def aggregate(trades):
    if not trades: return {"trades": 0, "win_rate": 0, "payoff": 0, "PF": 0, "exp": 0, "tp_pct": 0}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    tp_count = sum(1 for t in trades if t["reason"] == "take_profit")
    total_profit = wins.sum() if len(wins) else 0
    total_loss = -losses.sum() if len(losses) else 0
    PF = total_profit / total_loss if total_loss > 0 else float("inf")
    avg_w = wins.mean() if len(wins) else 0
    avg_l = losses.mean() if len(losses) else 0
    return {"trades": len(trades), "win_rate": len(wins)/len(trades),
            "payoff": abs(avg_w/avg_l) if avg_l < 0 else float("inf"),
            "PF": PF, "exp": rets.mean(), "tp_pct": tp_count/len(trades)}


def run_combo(data, stop, tp, cost_bps):
    all_trades = []
    for sym, (p1h, p5m) in data.items():
        fires = extract_fires(p1h)
        for ts, side, px in fires:
            t = execute_trade_5m(p5m, ts, side, px, stop, tp, cost_bps)
            if t: all_trades.append(t)
    return aggregate(all_trades)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=12)
    p.add_argument("--symbols", type=str, default="ALL")
    p.add_argument("--cost-bps", type=float, default=10.0)
    p.add_argument("--rr-scale", type=float, default=1.0,
                   help="multiply base R/R by this. 1.0=0.05-0.3 pct stop, 2.0=0.1-0.6, 5.0=0.25-1.5, 10.0=0.5-3.0")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    if args.symbols == "ALL":
        cache = _REPO / "data" / "cache" / "binance_1m"
        symbols = [f.stem for f in cache.glob("*.parquet") if not f.stem.startswith("_")]
    else:
        symbols = [s.strip() for s in args.symbols.split(",")]
    logger.info(f"Load {len(symbols)} syms, {args.months}mo, cost={args.cost_bps}bp, rr_scale={args.rr_scale}")
    data = _load_universe(args.months, symbols)
    if not data: return 3

    scaled_sweep = [(s*args.rr_scale, t*args.rr_scale, l) for s, t, l in BASE_SWEEP_RR]

    print(f"\n{'='*120}")
    print(f"v1.1 BIDIR  cost={args.cost_bps}bp  rr_scale={args.rr_scale}x  syms={len(data)}  {args.months}mo")
    print('='*120)

    rows = []
    for stop, tp, label in scaled_sweep:
        c0 = time.time()
        m = run_combo(data, stop, tp, args.cost_bps)
        rows.append((label, stop, tp, m))
        logger.info("  %s (s=%.3f%% t=%.3f%%) %ds  trades=%d PF=%.3f exp=%+.4f%%",
                    label, stop*100, tp*100, time.time()-c0, m["trades"], m["PF"], m["exp"]*100)

    print(f"\n  {'label':<20} {'stop':<8} {'tp':<8}  {'trades':>8}  {'win%':>5}  {'payoff':>6}  {'PF':>6}  {'exp%':>8}  {'tp%':>5}  verdict")
    print("  " + "-"*116)
    for label, stop, tp, m in sorted(rows, key=lambda r: r[3]["PF"], reverse=True):
        v = "PASS" if (m["PF"] > 1.0 and m["exp"] > 0) else "LOSER"
        print(f"  {label:<20} {stop*100:.3f}%   {tp*100:.3f}%   {m['trades']:>8}  {m['win_rate']*100:5.2f}  "
              f"{m['payoff']:6.2f}  {m['PF']:6.3f}  {m['exp']*100:+8.5f}  {m['tp_pct']*100:5.2f}  {v}")
    print(f"{'='*120}\nelapsed: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
