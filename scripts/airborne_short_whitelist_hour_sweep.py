"""SHORT × 21종 whitelist × KST hour sweep — 시간 게이트 검증.

선행 PR ``live-airborne-short-whitelist-v1`` 의 Hard OOS PF=1.112 는 *전 시간대*
가정. 그러나 daemon 은 ``kst_entry_hours={8,11,16,22}`` 를 default 로 상속 →
**검증 ≠ 구현**.

본 스크립트는 21종 whitelist × SHORT only 조합에서 각 KST hour 별 train/test
PF 측정 후 *진짜* 최적 hour subset 도출.

Output:
  - ``reports/airborne_short_whitelist_hour_sweep.json``
  - 콘솔: per-hour 표 + 추천 subset
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections import defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))

import signals  # noqa: E402

from src.live.airborne_short_whitelist.whitelist_loader import (  # noqa: E402
    active_symbols,
    load_whitelist,
)

logger = logging.getLogger("airborne_short_whitelist_hour_sweep")
_KST = ZoneInfo("Asia/Seoul")

CACHE_1H_DIR = _REPO_ROOT / "data" / "cache" / "binance_1h"
CACHE_1M_DIR = _REPO_ROOT / "data" / "cache" / "binance_1m"
CACHE_FUNDING_DIR = _REPO_ROOT / "data" / "cache" / "binance_funding"

COST_BPS_ROUNDTRIP = 10.0
LOOKAHEAD_BARS = 48
ENTRY = dict(retrace=0.6, bb_window=20, bb_std=2.0,
             min_margin=0.001, atr_body_mult=0.3, atr_period=14)
SL = 0.03
TP = 0.06

TRAIN_RANGE = (2021, 2023)
TEST_RANGE = (2024, 2025)


def _resample_1h(df_1m: pd.DataFrame) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (df_1m[cols]
            .resample("1h", label="right", closed="right")
            .agg({k: agg[k] for k in cols})
            .dropna(subset=["close"]))


def load_panel(symbol: str, months: int = 60) -> pd.DataFrame | None:
    end = pd.Timestamp.utcnow()
    start = end - pd.DateOffset(months=months)
    p1h = CACHE_1H_DIR / f"{symbol}.parquet"
    if p1h.exists():
        try:
            df = pd.read_parquet(p1h)
            if df.index.tz is None: df = df.tz_localize("UTC")
            df = df.loc[start:end]
            if len(df) >= 50: return df
        except Exception: pass
    p1m = CACHE_1M_DIR / f"{symbol}.parquet"
    if p1m.exists():
        try:
            d = pd.read_parquet(p1m)
            if d.index.tz is None: d = d.tz_localize("UTC")
            d = d.loc[start:end]
            panel = _resample_1h(d)
            if len(panel) >= 50: return panel
        except Exception: pass
    return None


def load_funding(symbol: str) -> pd.DataFrame:
    p = CACHE_FUNDING_DIR / f"{symbol}.parquet"
    if not p.exists():
        return pd.DataFrame(columns=["fundingRate"])
    try:
        df = pd.read_parquet(p)
        if df.index.tz is None: df = df.tz_localize("UTC")
        return df
    except Exception:
        return pd.DataFrame(columns=["fundingRate"])


def _wilder_atr(high, low, close, period):
    n = len(close); atr = np.full(n, np.nan)
    if n < period + 1: return atr
    tr = np.zeros(n); tr[0] = high[0] - low[0]
    for i in range(1, n):
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i] - close[i - 1])
        tr[i] = max(a, b, c)
    atr[period] = tr[1:period + 1].mean()
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def _sim(side, highs, lows, closes, idx, entry_px, sl, tp, cost):
    n = len(closes)
    end = min(idx + LOOKAHEAD_BARS + 1, n)
    if end - idx - 1 < 1: return 0.0, idx
    for j in range(idx + 1, end):
        if side == "long":
            rl = (lows[j] - entry_px) / entry_px
            rh = (highs[j] - entry_px) / entry_px
        else:
            rl = (entry_px - highs[j]) / entry_px
            rh = (entry_px - lows[j]) / entry_px
        if rl <= -sl: return -sl - cost, j
        if rh >= tp: return tp - cost, j
    last = closes[end - 1]
    if side == "long":
        return (last - entry_px) / entry_px - cost, end - 1
    return (entry_px - last) / entry_px - cost, end - 1


def extract_short_fires(symbol: str, panel: pd.DataFrame,
                        funding_df: pd.DataFrame) -> list[dict]:
    """Return list[{kst_hour, year, ret_with_funding}] — SHORT only."""
    bb = signals.compute("bollinger", close=panel["close"],
                         window=ENTRY["bb_window"], n_std=ENTRY["bb_std"])
    upper = bb["upper"].to_numpy(); lower = bb["lower"].to_numpy()
    closes = panel["close"].to_numpy(); opens = panel["open"].to_numpy()
    highs = panel["high"].to_numpy(); lows = panel["low"].to_numpy()
    body_abs = np.abs(closes - opens)
    atr = _wilder_atr(highs, lows, closes, ENTRY["atr_period"])
    upper_thr = upper * (1 + ENTRY["min_margin"])
    lower_thr = lower * (1 - ENTRY["min_margin"])
    n = len(panel)
    times = panel.index
    if times.tz is None: times = times.tz_localize("UTC")
    kst = times.tz_convert(_KST)
    kst_hours = kst.hour.to_numpy()
    years = times.year.to_numpy()
    cost_frac = COST_BPS_ROUNDTRIP / 10000.0
    state = 0; base = np.nan; extreme = np.nan
    out: list[dict] = []
    for i in range(1, n):
        if state == 0:
            if (not np.isnan(upper_thr[i]) and not np.isnan(upper_thr[i-1])
                    and not np.isnan(atr[i])
                    and body_abs[i] >= ENTRY["atr_body_mult"] * atr[i]):
                if closes[i] > upper_thr[i] and closes[i-1] <= upper_thr[i-1]:
                    state, base, extreme = 2, closes[i], highs[i]; continue
                if closes[i] < lower_thr[i] and closes[i-1] >= lower_thr[i-1]:
                    state, base, extreme = 1, closes[i], lows[i]; continue
        if state == 1 and not np.isnan(extreme):
            extreme = min(extreme, lows[i])
            trig = extreme + ENTRY["retrace"] * (base - extreme)
            if closes[i] >= trig:
                state, base, extreme = 0, np.nan, np.nan  # long fire — skip
        elif state == 2 and not np.isnan(extreme):
            extreme = max(extreme, highs[i])
            trig = extreme - ENTRY["retrace"] * (extreme - base)
            if closes[i] <= trig:
                # SHORT fire
                entry_px = float(closes[i])
                base_ret, exit_idx = _sim("short", highs, lows, closes, i,
                                          entry_px, SL, TP, cost_frac)
                # funding pnl
                if not funding_df.empty:
                    mask = (funding_df.index > times[i]) & (funding_df.index <= times[exit_idx])
                    fr_sum = float(funding_df.loc[mask, "fundingRate"].sum())
                else:
                    fr_sum = 0.0
                # SHORT: + fr_sum
                out.append({
                    "kst_hour": int(kst_hours[i]),
                    "year": int(years[i]),
                    "ret_funded": base_ret + fr_sum,
                })
                state, base, extreme = 0, np.nan, np.nan
    return out


def agg(rets: list[float]) -> dict:
    if not rets:
        return {"n": 0, "PF": None, "sum_R": 0.0, "exp": 0.0, "win_rate": 0.0}
    arr = np.asarray(rets)
    wins = arr[arr > 0]; losses = arr[arr <= 0]
    gw = float(wins.sum()); gl = float(-losses.sum())
    pf = gw / gl if gl > 0 else None
    return {
        "n": int(len(arr)),
        "PF": round(pf, 4) if pf is not None else None,
        "sum_R": round(float(arr.sum()), 4),
        "exp": round(float(arr.mean()), 6),
        "win_rate": round(float(len(wins) / len(arr)), 4),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", default="reports/airborne_short_whitelist_hour_sweep.json")
    p.add_argument("--whitelist", default="config/airborne_short_whitelist.yaml")
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_whitelist(args.whitelist)
    syms = sorted(active_symbols(cfg))
    logger.info("active whitelist (%d): %s", len(syms), syms)

    all_fires: list[dict] = []
    for sym in syms:
        panel = load_panel(sym)
        if panel is None:
            logger.warning("skip %s (no data)", sym)
            continue
        fdf = load_funding(sym)
        fires = extract_short_fires(sym, panel, fdf)
        all_fires.extend(fires)
        logger.info("  %s: %d SHORT fires", sym, len(fires))
    logger.info("total SHORT fires: %d", len(all_fires))

    # per-hour breakdown
    by_hour_train: dict[int, list[float]] = defaultdict(list)
    by_hour_test: dict[int, list[float]] = defaultdict(list)
    for f in all_fires:
        h = f["kst_hour"]; y = f["year"]
        if TRAIN_RANGE[0] <= y <= TRAIN_RANGE[1]:
            by_hour_train[h].append(f["ret_funded"])
        if TEST_RANGE[0] <= y <= TEST_RANGE[1]:
            by_hour_test[h].append(f["ret_funded"])

    rows: list[dict] = []
    for h in range(24):
        tr = agg(by_hour_train[h]); te = agg(by_hour_test[h])
        rows.append({"hour": h, "train": tr, "test": te})

    # Greedy: best subset by train PF
    train_pfs = [(r["hour"], r["train"]["PF"], r["train"]["n"], r["test"]["PF"]) for r in rows]
    sorted_by_train = sorted(
        [t for t in train_pfs if t[1] is not None and t[2] >= 30],
        key=lambda t: -t[1],
    )
    # 모든 subset 시나리오
    scenarios = []
    # baseline: all 24h
    all_train = [f["ret_funded"] for f in all_fires if TRAIN_RANGE[0] <= f["year"] <= TRAIN_RANGE[1]]
    all_test = [f["ret_funded"] for f in all_fires if TEST_RANGE[0] <= f["year"] <= TEST_RANGE[1]]
    scenarios.append({
        "label": "ALL 24h (no gate)",
        "hours": list(range(24)),
        "train": agg(all_train),
        "test": agg(all_test),
    })
    # existing daemon default
    legacy = {8, 11, 16, 22}
    leg_tr = [f["ret_funded"] for f in all_fires
              if TRAIN_RANGE[0] <= f["year"] <= TRAIN_RANGE[1] and f["kst_hour"] in legacy]
    leg_te = [f["ret_funded"] for f in all_fires
              if TEST_RANGE[0] <= f["year"] <= TEST_RANGE[1] and f["kst_hour"] in legacy]
    scenarios.append({
        "label": "Legacy {8,11,16,22}",
        "hours": sorted(legacy),
        "train": agg(leg_tr),
        "test": agg(leg_te),
    })
    # greedy top-K by train PF
    for K in (3, 5, 8, 12):
        top_hours = sorted([t[0] for t in sorted_by_train[:K]])
        tr_set = set(top_hours)
        tr_rets = [f["ret_funded"] for f in all_fires
                   if TRAIN_RANGE[0] <= f["year"] <= TRAIN_RANGE[1] and f["kst_hour"] in tr_set]
        te_rets = [f["ret_funded"] for f in all_fires
                   if TEST_RANGE[0] <= f["year"] <= TEST_RANGE[1] and f["kst_hour"] in tr_set]
        scenarios.append({
            "label": f"Top-{K} by train PF",
            "hours": top_hours,
            "train": agg(tr_rets),
            "test": agg(te_rets),
        })
    # train_PF > 1 만
    train_pass = sorted([t[0] for t in train_pfs if t[1] is not None and t[1] > 1.0 and t[2] >= 30])
    tps = set(train_pass)
    tr_p = [f["ret_funded"] for f in all_fires
            if TRAIN_RANGE[0] <= f["year"] <= TRAIN_RANGE[1] and f["kst_hour"] in tps]
    te_p = [f["ret_funded"] for f in all_fires
            if TEST_RANGE[0] <= f["year"] <= TEST_RANGE[1] and f["kst_hour"] in tps]
    scenarios.append({
        "label": "train_PF>1 hours only",
        "hours": train_pass,
        "train": agg(tr_p),
        "test": agg(te_p),
    })

    report = {
        "meta": {
            "generated_at": pd.Timestamp.utcnow().isoformat(),
            "active_whitelist": syms,
            "n_active": len(syms),
            "total_short_fires": len(all_fires),
            "entry_params": ENTRY,
            "exit": {"stop": SL, "tp": TP},
            "cost_bps": COST_BPS_ROUNDTRIP,
            "train_range": list(TRAIN_RANGE),
            "test_range": list(TEST_RANGE),
        },
        "per_hour": rows,
        "scenarios": scenarios,
    }
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    logger.info("wrote %s", out_path)

    # Console
    print()
    print("=" * 90)
    print("AIRBORNE SHORT × 21-whitelist × KST hour sweep")
    print("=" * 90)
    print(f"active whitelist (n={len(syms)}): {syms}")
    print(f"total SHORT fires: {len(all_fires)}")
    print()
    print(f"{'hour':>4} {'tr_n':>5} {'tr_PF':>6} {'tr_sumR%':>9} | "
          f"{'te_n':>5} {'te_PF':>6} {'te_sumR%':>9}")
    print("-" * 70)
    for r in rows:
        tr = r["train"]; te = r["test"]
        tr_pf = f"{tr['PF']:.3f}" if tr['PF'] else "  -"
        te_pf = f"{te['PF']:.3f}" if te['PF'] else "  -"
        mark = ""
        if tr['PF'] and te['PF'] and tr['PF'] > 1.0 and te['PF'] > 1.0:
            mark = " ★"
        print(f"{r['hour']:>4} {tr['n']:>5} {tr_pf:>6} {tr['sum_R']*100:>+8.2f} | "
              f"{te['n']:>5} {te_pf:>6} {te['sum_R']*100:>+8.2f}{mark}")

    print("\n=== SCENARIOS ===")
    print(f"  {'label':<26} {'hours':<32} {'tr_PF':>7} {'te_PF':>7} "
          f"{'te_n':>6} {'te_sumR%':>10} {'tr/day_test':>11}")
    test_days = (TEST_RANGE[1] - TEST_RANGE[0] + 1) * 365
    for s in scenarios:
        tr_pf = f"{s['train']['PF']:.3f}" if s['train']['PF'] else "  -"
        te_pf = f"{s['test']['PF']:.3f}" if s['test']['PF'] else "  -"
        tr_per_day = s['test']['n'] / test_days
        hours_s = str(s['hours'])
        if len(hours_s) > 30:
            hours_s = hours_s[:27] + "..."
        print(f"  {s['label']:<26} {hours_s:<32} {tr_pf:>7} {te_pf:>7} "
              f"{s['test']['n']:>6} {s['test']['sum_R']*100:>+9.1f} "
              f"{tr_per_day:>10.2f}")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
