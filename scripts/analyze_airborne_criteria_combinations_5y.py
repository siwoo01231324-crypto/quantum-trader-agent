"""각 기준 단독 + 조합별 PF / 승률 분석 — 점수 합산 대신 *어떤 조합이 가장 강한가*.

5y v1.2 bidir fire 마다 7 기준의 boolean mask 와 거래 결과 (TP/SL/RET) 를
함께 기록 → conditional PF / win rate 계산.

기준:
  1. 추세 EMA21        (close > EMA21 or EMA up)       — GATE +3
  2. 거래량 클라이맥스   (vol >= 1.5 × MA20)             — GATE +3
  3. FVG               (직전 20봉 안 unfilled FVG)      — +2
  4. 오더블럭           (직전 20봉 안 engulfing)         — +2
  5. 꼬리 거부 ≥0.5     (wick/range >= 0.5)              — +1
  6. 프랙탈             (Williams 5봉 pivot)             — +1
  7. 4h EMA21 동의      (4h EMA21 같은 방향)             — +1

출력:
  - per-criterion PF (각 기준 단독)
  - top 10 pairs (2개 조합)
  - top 10 triples (3개 조합)
  - top 4-tuple/5-tuple (작은 n 위주)
  - 전체 7-bit mask 분포
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import combinations
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
logger = logging.getLogger("analyze_airborne_criteria_combinations_5y")

CRITERIA_NAMES = [
    "추세_EMA21",          # 1
    "거래량_클라이맥스",     # 2
    "FVG",                # 3
    "오더블럭",             # 4
    "꼬리_거부",            # 5
    "프랙탈",               # 6
    "4h_EMA21_동의",       # 7
]


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) < period:
        return out
    a = 2.0 / (period + 1)
    out[period - 1] = float(np.nanmean(arr[:period]))
    for i in range(period, len(arr)):
        prev = out[i - 1]
        out[i] = a * arr[i] + (1 - a) * prev if not np.isnan(prev) else arr[i]
    return out


def evaluate_criteria(
    panel: pd.DataFrame,
    fire_idx: int,
    side: str,
    *,
    ema_v: np.ndarray, vol_ma: np.ndarray, ema_4h: np.ndarray,
    wick_thresh: float = 0.5,
    fvg_lookback: int = 20, ob_lookback: int = 20,
    vol_climax_mult: float = 1.5,
) -> tuple[bool, ...]:
    """fire 봉의 7 기준 boolean tuple."""
    closes = panel["close"].to_numpy()
    opens = panel["open"].to_numpy()
    highs = panel["high"].to_numpy()
    lows = panel["low"].to_numpy()
    vols = panel["volume"].to_numpy()
    i = fire_idx
    n = len(panel)

    # 1. 추세 EMA21
    e = ema_v[i]
    if np.isnan(e):
        trend_ok = False
    elif side == "long":
        trend_ok = closes[i] > e or (i >= 2 and ema_v[i] > ema_v[i - 1] > ema_v[i - 2])
    else:
        trend_ok = closes[i] < e or (i >= 2 and ema_v[i] < ema_v[i - 1] < ema_v[i - 2])

    # 2. 거래량 클라이맥스
    vol_ok = vols[i] >= vol_climax_mult * vol_ma[i] if not np.isnan(vol_ma[i]) else False

    # 3. FVG within lookback (bullish for long: low[j] > high[j+2])
    fvg_ok = False
    for j in range(1, min(fvg_lookback, i - 1) + 1):
        if i - j - 2 < 0:
            break
        if side == "long":
            if lows[i - j] > highs[i - j - 2]:
                fvg_ok = True
                break
        else:
            if highs[i - j] < lows[i - j - 2]:
                fvg_ok = True
                break

    # 4. 오더블럭 (engulfing within lookback)
    ob_ok = False
    for j in range(1, min(ob_lookback, i - 1) + 1):
        if i - j - 1 < 0:
            break
        a, b = i - j, i - j - 1
        if side == "long":
            # bullish engulf: close[a] > open[a], close[b] < open[b], engulf body
            if (closes[a] > opens[a] and closes[b] < opens[b]
                    and closes[a] >= opens[b] and opens[a] <= closes[b]):
                ob_ok = True
                break
        else:
            if (closes[a] < opens[a] and closes[b] > opens[b]
                    and opens[a] >= closes[b] and closes[a] <= opens[b]):
                ob_ok = True
                break

    # 5. 꼬리 거부
    rng = max(highs[i] - lows[i], 1e-9)
    if side == "long":
        wick = (min(opens[i], closes[i]) - lows[i]) / rng
    else:
        wick = (highs[i] - max(opens[i], closes[i])) / rng
    wick_ok = wick >= wick_thresh

    # 6. 프랙탈 (Williams 5봉 pivot at i-2)
    frac_ok = False
    if i >= 4:
        c = i - 2
        if side == "long":
            frac_ok = (lows[c] < lows[c - 1] and lows[c] < lows[c - 2]
                       and lows[c] < lows[c + 1] and lows[c] < lows[c + 2])
        else:
            frac_ok = (highs[c] > highs[c - 1] and highs[c] > highs[c - 2]
                       and highs[c] > highs[c + 1] and highs[c] > highs[c + 2])

    # 7. 4h EMA21 동의
    e4 = ema_4h[i]
    if np.isnan(e4):
        mtf_ok = False
    elif side == "long":
        mtf_ok = closes[i] > e4
    else:
        mtf_ok = closes[i] < e4

    return (trend_ok, vol_ok, fvg_ok, ob_ok, wick_ok, frac_ok, mtf_ok)


def simulate_and_label(
    panel: pd.DataFrame, stop: float, tp: float, cost_bps: float,
) -> list[dict]:
    """v1.2 bidir 시뮬 + 각 fire 의 7 기준 mask + trade 결과 (ret) 동기 반환."""
    bb = signals.compute("bollinger", close=panel["close"], window=20, n_std=2.0)
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

    ema_v = _ema(closes, 21)
    vol_ma = pd.Series(panel["volume"].to_numpy()).rolling(20).mean().to_numpy()
    # 4h EMA21 — resample 1h panel to 4h
    p4h = panel["close"].resample("4h", label="right", closed="right").last().dropna()
    ema_4h_full = pd.Series(_ema(p4h.to_numpy(), 21), index=p4h.index)
    ema_4h_aligned = ema_4h_full.reindex(panel.index, method="ffill").to_numpy()

    # find fires
    upper_break = np.zeros(n, dtype=bool)
    lower_break = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isnan(upper_thr[i]) or np.isnan(upper_thr[i - 1]) or np.isnan(atr[i]):
            continue
        if body_abs[i] < DEFAULT_ATR_BODY_MULT_V11 * atr[i]:
            continue
        if closes[i] > upper_thr[i] and closes[i - 1] <= upper_thr[i - 1]:
            upper_break[i] = True
        elif closes[i] < lower_thr[i] and closes[i - 1] >= lower_thr[i - 1]:
            lower_break[i] = True

    state = 0; base = np.nan; ext = np.nan
    fires = []
    for i in range(n):
        if state == 0:
            if upper_break[i]:
                state, base, ext = 2, closes[i], highs[i]
            elif lower_break[i]:
                state, base, ext = 1, closes[i], lows[i]
        if state == 1 and not np.isnan(ext):
            ext = min(ext, lows[i])
            trig = ext + RETRACE_RATIO * (base - ext)
            if closes[i] >= trig:
                fires.append((i, "long", closes[i]))
                state, base, ext = 0, np.nan, np.nan
        elif state == 2 and not np.isnan(ext):
            ext = max(ext, highs[i])
            trig = ext - RETRACE_RATIO * (ext - base)
            if closes[i] <= trig:
                fires.append((i, "short", closes[i]))
                state, base, ext = 0, np.nan, np.nan

    # simulate trades + record criteria at fire time
    records = []
    in_pos = False
    pos_side = None; pos_entry = 0.0; pos_entry_i = 0; pos_sl = 0.0; pos_tp = 0.0
    fire_idx = 0
    cost = cost_bps / 10000.0
    times = panel.index
    if times.tz is None:
        times = times.tz_localize("UTC")
    pending_mask = None

    for i in range(n):
        if in_pos:
            exit_reason, exit_px = None, None
            if pos_side == "long":
                if lows[i] <= pos_sl:
                    exit_reason, exit_px = "stop_loss", pos_sl
                elif highs[i] >= pos_tp:
                    exit_reason, exit_px = "take_profit", pos_tp
            else:
                if highs[i] >= pos_sl:
                    exit_reason, exit_px = "stop_loss", pos_sl
                elif lows[i] <= pos_tp:
                    exit_reason, exit_px = "take_profit", pos_tp
            if exit_reason:
                if pos_side == "long":
                    ret = (exit_px / pos_entry) - 1 - 2 * cost
                else:
                    ret = 1 - (exit_px / pos_entry) - 2 * cost
                records.append({
                    "side": pos_side, "ret": ret,
                    "mask": pending_mask,
                    "exit_reason": exit_reason,
                    "entry_ts": times[pos_entry_i].isoformat(),
                })
                in_pos = False

        if not in_pos and fire_idx < len(fires) and fires[fire_idx][0] == i:
            _, side, entry = fires[fire_idx]
            mask = evaluate_criteria(
                panel, i, side,
                ema_v=ema_v, vol_ma=vol_ma, ema_4h=ema_4h_aligned,
            )
            in_pos = True
            pos_side = side; pos_entry = entry; pos_entry_i = i
            pending_mask = mask
            if side == "long":
                pos_sl = entry * (1 - stop); pos_tp = entry * (1 + tp)
            else:
                pos_sl = entry * (1 + stop); pos_tp = entry * (1 - tp)
            fire_idx += 1
        elif fire_idx < len(fires) and fires[fire_idx][0] <= i:
            while fire_idx < len(fires) and fires[fire_idx][0] <= i:
                fire_idx += 1
    return records


def metrics(records: list[dict]) -> dict:
    n = len(records)
    if n == 0:
        return {"n": 0, "PF": None, "exp": None, "win_rate": None}
    rets = np.array([r["ret"] for r in records])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    tp = float(wins.sum()); tl = float(-losses.sum())
    pf = (tp / tl) if tl > 0 else None
    return {"n": n, "PF": pf, "exp": float(rets.mean()),
            "win_rate": float(len(wins) / n)}


async def _main(args):
    t0 = time.time()
    syms = bench._load_universe_symbols(args.top_n)
    panels, _ = bench._load_panels(syms, args.months, "1h")
    all_records = []
    for sym, panel in panels.items():
        all_records.extend(simulate_and_label(panel, args.stop, args.tp, args.cost_bps))
    n = len(all_records)
    print(f"\ntotal fires (after trade close): {n}")
    base = metrics(all_records)
    print(f"BASELINE 모든 fire: PF={base['PF']:.3f}  win={base['win_rate']*100:.1f}%  exp={base['exp']*100:+.3f}%")
    print()

    print("=" * 110)
    print("[per-criterion conditional metrics] 각 기준 PASS 한 fire 만 모음")
    print(f"  {'criterion':<22} {'n':>6}  {'PF':>6}  {'win%':>6}  {'exp':>9}  {'lift PF':>8}")
    print("  " + "-" * 90)
    for k in range(7):
        pass_recs = [r for r in all_records if r["mask"][k]]
        fail_recs = [r for r in all_records if not r["mask"][k]]
        mp = metrics(pass_recs); mf = metrics(fail_recs)
        if mp["PF"] is None:
            continue
        lift = mp["PF"] - (base["PF"] or 0)
        print(f"  {CRITERIA_NAMES[k]:<22} {mp['n']:>6}  {mp['PF']:6.3f}  "
              f"{mp['win_rate']*100:5.1f}%  {mp['exp']*100:+7.3f}%  {lift:+7.3f}")
    print()

    print("=" * 110)
    print("[pair conditional] 두 기준 모두 PASS")
    pairs = []
    for c1, c2 in combinations(range(7), 2):
        recs = [r for r in all_records if r["mask"][c1] and r["mask"][c2]]
        m = metrics(recs)
        if m["n"] >= 50 and m["PF"] is not None:
            pairs.append((c1, c2, m))
    pairs.sort(key=lambda x: -(x[2]["PF"] or 0))
    print(f"  {'pair':<48} {'n':>5}  {'PF':>6}  {'win%':>6}  {'exp':>9}")
    print("  " + "-" * 95)
    for c1, c2, m in pairs[:15]:
        name = f"{CRITERIA_NAMES[c1]} + {CRITERIA_NAMES[c2]}"
        print(f"  {name:<48} {m['n']:>5}  {m['PF']:6.3f}  {m['win_rate']*100:5.1f}%  {m['exp']*100:+7.3f}%")
    print()

    print("=" * 110)
    print("[triple conditional] 세 기준 모두 PASS")
    triples = []
    for c1, c2, c3 in combinations(range(7), 3):
        recs = [r for r in all_records if r["mask"][c1] and r["mask"][c2] and r["mask"][c3]]
        m = metrics(recs)
        if m["n"] >= 30 and m["PF"] is not None:
            triples.append(((c1, c2, c3), m))
    triples.sort(key=lambda x: -(x[1]["PF"] or 0))
    print(f"  {'triple':<60} {'n':>5}  {'PF':>6}  {'win%':>6}  {'exp':>9}")
    print("  " + "-" * 110)
    for cs, m in triples[:15]:
        name = " + ".join(CRITERIA_NAMES[c] for c in cs)
        print(f"  {name:<60} {m['n']:>5}  {m['PF']:6.3f}  {m['win_rate']*100:5.1f}%  {m['exp']*100:+7.3f}%")
    print()

    # 4-criteria 조합 (sample 작아질 것 — n>=20)
    print("=" * 110)
    print("[4-criteria conditional] 4 기준 모두 PASS (n>=20)")
    quads = []
    for combo in combinations(range(7), 4):
        recs = [r for r in all_records if all(r["mask"][c] for c in combo)]
        m = metrics(recs)
        if m["n"] >= 20 and m["PF"] is not None:
            quads.append((combo, m))
    quads.sort(key=lambda x: -(x[1]["PF"] or 0))
    print(f"  {'4-combo':<72} {'n':>5}  {'PF':>6}  {'win%':>6}")
    print("  " + "-" * 110)
    for cs, m in quads[:10]:
        name = " + ".join(CRITERIA_NAMES[c] for c in cs)
        print(f"  {name:<72} {m['n']:>5}  {m['PF']:6.3f}  {m['win_rate']*100:5.1f}%")
    print()

    # Save full report
    out_path = _REPO_ROOT / "reports" / "airborne_criteria_combinations_5y.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_fires": n,
        "baseline": base,
        "per_criterion": [
            {"name": CRITERIA_NAMES[k], **metrics([r for r in all_records if r["mask"][k]])}
            for k in range(7)
        ],
        "top_pairs": [
            {"criteria": [CRITERIA_NAMES[c1], CRITERIA_NAMES[c2]], **m}
            for c1, c2, m in pairs[:30]
        ],
        "top_triples": [
            {"criteria": [CRITERIA_NAMES[c] for c in cs], **m} for cs, m in triples[:30]
        ],
        "top_quads": [
            {"criteria": [CRITERIA_NAMES[c] for c in cs], **m} for cs, m in quads[:20]
        ],
        "elapsed_sec": round(time.time() - t0, 1),
    }, indent=2, default=str), encoding="utf-8")
    print(f"wrote {out_path.relative_to(_REPO_ROOT).as_posix()}")
    return 0


def _parse(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--months", type=int, default=60)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--stop", type=float, default=0.03)
    p.add_argument("--tp", type=float, default=0.06)
    p.add_argument("--cost-bps", type=float, default=10.0)
    return p.parse_args(argv)


def main(argv=None):
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    return asyncio.run(_main(_parse(argv)))


if __name__ == "__main__":
    sys.exit(main())
