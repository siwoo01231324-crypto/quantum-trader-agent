"""Filter sweep — Pine v1.2 bidir + (BBW regime / settlement skip / 4h EMA /
midline TP / R/R 조정) 조합을 5y 에 평가.

[[reports/eval_live_airborne_kst_morning_5y.json]] 에서 단순 KST 시간 필터로는
PF=0.906 천장임이 확인됨. 웹 리서치 (web-research-specialist agent) 가 제안한
구조적 필터 5종 평가:

1. **BBW regime gate** — Bollinger Bandwidth (=2σ/SMA) 의 250봉 rolling
   percentile. 진입 후보가 P25 미만 (squeeze → breakout 위험) 또는 P75 초과
   (이미 확장 → mean-rev 약함) 면 차단.
2. **Settlement skip** — UTC 00:00/08:00/16:00 ±30 min 차단 (Binance funding
   settlement 직전후 price action 왜곡).
3. **4h EMA(50) counter-trend** — 1h 봉을 4h 로 resample 후 EMA(50). long 은
   close < EMA50 일 때만 (downtrend 의 일시 bounce 만 진입), short 은 반대.
4. **Midline TP** — TP 를 +6% 가 아니라 BB midline (SMA(20)) 까지 (mean-rev
   가 정의상 midline 까지가 자연 target).
5. **R/R 1:1.5** — stop/TP 를 3%/4.5% 로 좁힘 (PF arithmetic 개선).

각 필터 단독 + 조합 (BBW+settlement, +1.5 R/R 등) 약 8 개 시나리오를 같은
panel/cost 로 평가.

Output:
- console table
- ``reports/airborne_filter_sweep_5y.json``
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
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

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
logger = logging.getLogger("bench_airborne_filter_sweep_5y")

_KST = ZoneInfo("Asia/Seoul")
BB_WINDOW = 20
BB_STD = 2.0


def _wilder_atr(high, low, close, period):
    return bench._wilder_atr(high, low, close, period)


def _bbw_percentile(panel: pd.DataFrame, window: int = 250) -> np.ndarray:
    """Bollinger Bandwidth percentile (0–1) rolling window. NaN 까지 보존."""
    bb = signals.compute("bollinger", close=panel["close"],
                         window=BB_WINDOW, n_std=BB_STD)
    width = (bb["upper"] - bb["lower"]).to_numpy()
    n = len(width)
    out = np.full(n, np.nan)
    for i in range(window, n):
        w = width[i - window:i + 1]
        valid = w[~np.isnan(w)]
        if len(valid) < window // 2:
            continue
        cur = width[i]
        if np.isnan(cur):
            continue
        # rank percentile (0..1)
        out[i] = (valid < cur).sum() / len(valid)
    return out


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    out = np.full(len(arr), np.nan)
    if len(arr) < period:
        return out
    alpha = 2.0 / (period + 1)
    # seed with SMA at index period-1
    sma = np.nanmean(arr[:period])
    out[period - 1] = sma
    for i in range(period, len(arr)):
        prev = out[i - 1]
        if np.isnan(prev):
            out[i] = arr[i]
        else:
            out[i] = alpha * arr[i] + (1 - alpha) * prev
    return out


def _ema50_4h_on_1h(panel: pd.DataFrame) -> np.ndarray:
    """1h panel index 에 정합되는 4h EMA(50) close 시리즈."""
    if panel.index.tz is None:
        panel = panel.tz_localize("UTC")
    p4h = panel["close"].resample("4h", label="right", closed="right").last().dropna()
    ema_4h = pd.Series(_ema(p4h.to_numpy(), 50), index=p4h.index)
    # reindex 로 1h 인덱스에 forward-fill — 4h 봉 닫히기 전 1h 봉은 직전 4h close 사용.
    aligned = ema_4h.reindex(panel.index, method="ffill")
    return aligned.to_numpy()


def _is_settlement_skip(times: pd.DatetimeIndex) -> np.ndarray:
    """UTC 00/08/16:00 ±30min 차단."""
    if times.tz is None:
        times = times.tz_localize("UTC")
    utc_h = times.tz_convert("UTC").hour.to_numpy()
    utc_m = times.tz_convert("UTC").minute.to_numpy()
    in_window = np.zeros(len(times), dtype=bool)
    for h in (0, 8, 16):
        # ±30 min around hh:00 → previous hour's 30-59 min OR this hour's 0-30 min
        in_window |= (utc_h == h) & (utc_m <= 30)
        prev_h = (h - 1) % 24
        in_window |= (utc_h == prev_h) & (utc_m >= 30)
    return in_window


def simulate_with_filters(
    panel: pd.DataFrame,
    *,
    stop: float,
    tp: float,
    cost_bps: float,
    bbw_regime: bool = False,
    bbw_lo: float = 0.25,
    bbw_hi: float = 0.75,
    settlement_skip: bool = False,
    counter_trend_4h: bool = False,
    midline_tp: bool = False,
    side: str = "both",  # "both" / "long" / "short"
) -> list[dict]:
    """Pine v1.2 bidir + 옵션 필터. side='long' 이면 short 차단 (역도)."""
    bb = signals.compute("bollinger", close=panel["close"],
                         window=BB_WINDOW, n_std=BB_STD)
    upper = bb["upper"].to_numpy()
    lower = bb["lower"].to_numpy()
    midline = bb["middle"].to_numpy() if "middle" in bb else (
        (upper + lower) / 2.0)
    closes = panel["close"].to_numpy()
    opens = panel["open"].to_numpy()
    highs = panel["high"].to_numpy()
    lows = panel["low"].to_numpy()
    body_abs = np.abs(closes - opens)
    atr = _wilder_atr(highs, lows, closes, DEFAULT_ATR_PERIOD_V11)
    upper_thr = upper * (1 + DEFAULT_MIN_CLOSE_MARGIN_V11)
    lower_thr = lower * (1 - DEFAULT_MIN_CLOSE_MARGIN_V11)
    n = len(panel)

    # ── 필터 precompute ──
    times = panel.index
    if times.tz is None:
        times = times.tz_localize("UTC")
    bbw_pct = _bbw_percentile(panel, 250) if bbw_regime else None
    settlement = _is_settlement_skip(times) if settlement_skip else None
    ema4h_50 = _ema50_4h_on_1h(panel) if counter_trend_4h else None

    # ── breakout detection ──
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

    # ── state machine ──
    state = 0
    base = np.nan
    extreme = np.nan
    fires: list[tuple[int, str, float]] = []
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

    # ── trade sim w/ filter gates on entry ──
    trades = []
    in_pos = False
    pos_side = None
    pos_entry = 0.0
    pos_entry_i = 0
    pos_tp_px = 0.0
    pos_sl_px = 0.0
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
                    trades.append({
                        "side": "long", "entry_ts": times[pos_entry_i].isoformat(),
                        "exit_ts": times[i].isoformat(),
                        "entry": pos_entry, "exit": exit_px,
                        "ret": ret, "exit_reason": exit_reason,
                    })
                    in_pos = False
            else:
                if highs[i] >= pos_sl_px:
                    exit_reason, exit_px = "stop_loss", pos_sl_px
                elif lows[i] <= pos_tp_px:
                    exit_reason, exit_px = "take_profit", pos_tp_px
                if exit_reason:
                    ret = 1 - (exit_px / pos_entry) - 2 * cost
                    trades.append({
                        "side": "short", "entry_ts": times[pos_entry_i].isoformat(),
                        "exit_ts": times[i].isoformat(),
                        "entry": pos_entry, "exit": exit_px,
                        "ret": ret, "exit_reason": exit_reason,
                    })
                    in_pos = False

        if not in_pos and fire_idx < len(fires) and fires[fire_idx][0] == i:
            _, fire_side, entry = fires[fire_idx]
            allow = True
            if side == "long" and fire_side != "long":
                allow = False
            elif side == "short" and fire_side != "short":
                allow = False
            if allow and bbw_regime:
                p = bbw_pct[i]
                if np.isnan(p) or p < bbw_lo or p > bbw_hi:
                    allow = False
            if allow and settlement_skip and settlement[i]:
                allow = False
            if allow and counter_trend_4h:
                e = ema4h_50[i]
                if np.isnan(e):
                    allow = False
                elif fire_side == "long" and entry >= e:
                    allow = False  # long 은 close < ema50 일 때만
                elif fire_side == "short" and entry <= e:
                    allow = False
            if allow:
                in_pos = True
                pos_side = fire_side
                pos_entry = entry
                pos_entry_i = i
                # TP/SL 가격 — midline_tp 면 TP 는 midline 까지, SL 은 기존
                if fire_side == "long":
                    pos_sl_px = entry * (1 - stop)
                    if midline_tp and not np.isnan(midline[i]):
                        pos_tp_px = max(midline[i], entry * (1 + 0.005))
                    else:
                        pos_tp_px = entry * (1 + tp)
                else:
                    pos_sl_px = entry * (1 + stop)
                    if midline_tp and not np.isnan(midline[i]):
                        pos_tp_px = min(midline[i], entry * (1 - 0.005))
                    else:
                        pos_tp_px = entry * (1 - tp)
            fire_idx += 1
        elif fire_idx < len(fires) and fires[fire_idx][0] <= i:
            while fire_idx < len(fires) and fires[fire_idx][0] <= i:
                fire_idx += 1
    return trades


def aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "PF": None, "exp": None, "win_rate": None,
                "long_n": 0, "short_n": 0, "long_PF": None, "short_PF": None}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]; losses = rets[rets <= 0]
    tp = float(wins.sum()); tl = float(-losses.sum())
    pf = (tp / tl) if tl > 0 else None
    exp = float(rets.mean())
    win = float(len(wins) / len(trades))
    def _sp(side):
        ts = [t for t in trades if t["side"] == side]
        if not ts: return None
        r = np.array([t["ret"] for t in ts])
        p = float(r[r > 0].sum()); l = float(-r[r <= 0].sum())
        return (p / l) if l > 0 else None
    return {"trades": len(trades), "PF": pf, "exp": exp, "win_rate": win,
            "long_n": sum(1 for t in trades if t["side"] == "long"),
            "short_n": sum(1 for t in trades if t["side"] == "short"),
            "long_PF": _sp("long"), "short_PF": _sp("short")}


SCENARIOS = [
    # (name, kwargs)
    ("baseline (3%/6%)", dict()),
    ("R/R 1:1.5 (2%/3%)", dict(stop=0.02, tp=0.03)),
    ("R/R 1:1 (2%/2%)", dict(stop=0.02, tp=0.02)),
    ("BBW regime P25-P75", dict(bbw_regime=True)),
    ("BBW P40-P80",          dict(bbw_regime=True, bbw_lo=0.40, bbw_hi=0.80)),
    ("settlement skip",      dict(settlement_skip=True)),
    ("4h EMA50 counter-trend", dict(counter_trend_4h=True)),
    ("midline TP",           dict(midline_tp=True)),
    ("BBW + settlement",     dict(bbw_regime=True, settlement_skip=True)),
    ("BBW + 4h counter-trend", dict(bbw_regime=True, counter_trend_4h=True)),
    ("BBW + settlement + R/R 1:1.5",
        dict(bbw_regime=True, settlement_skip=True, stop=0.02, tp=0.03)),
    ("BBW + counter-trend + R/R 1:1.5",
        dict(bbw_regime=True, counter_trend_4h=True, stop=0.02, tp=0.03)),
    ("BBW + counter-trend + settlement + R/R 1:1.5",
        dict(bbw_regime=True, counter_trend_4h=True, settlement_skip=True,
             stop=0.02, tp=0.03)),
    ("ALL + midline TP",
        dict(bbw_regime=True, counter_trend_4h=True, settlement_skip=True,
             midline_tp=True, stop=0.02, tp=0.03)),
]


def run_scenario(panels: dict[str, pd.DataFrame], name: str, kw: dict,
                 cost_bps: float, base_stop: float, base_tp: float) -> dict:
    stop = kw.get("stop", base_stop)
    tp = kw.get("tp", base_tp)
    filter_kw = {k: v for k, v in kw.items() if k not in ("stop", "tp")}
    all_trades = []
    for sym, panel in panels.items():
        all_trades.extend(simulate_with_filters(
            panel, stop=stop, tp=tp, cost_bps=cost_bps, **filter_kw,
        ))
    m = aggregate(all_trades)
    return {"name": name, "stop": stop, "tp": tp, **filter_kw, **m}


def _fmt_row(r: dict) -> str:
    pf = r["PF"]; exp = r["exp"]
    pf_t = f"{pf:6.3f}" if pf is not None else "  -   "
    verdict = "PASS" if (pf is not None and pf > 1.0 and exp > 0) else "LOSER"
    long_pf = f"{r['long_PF']:5.2f}" if r['long_PF'] is not None else "  -  "
    short_pf = f"{r['short_PF']:5.2f}" if r['short_PF'] is not None else "  -  "
    return (
        f"  {r['name']:<48} {r['trades']:>6}  {pf_t}  {exp*100:+7.4f}%  "
        f"L={r['long_n']:>5}/{long_pf}  S={r['short_n']:>5}/{short_pf}  {verdict}"
    )


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    symbols = bench._load_universe_symbols(args.top_n)
    panels, coverage = bench._load_panels(symbols, args.months, "1h")
    if not panels:
        return 3
    print("\n" + "=" * 130)
    print(f"airborne filter sweep — v1.2 bidir base + 옵션 필터 조합")
    print(f"  months={args.months}  symbols={len(panels)}  cost={args.cost_bps:.0f}bp")
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

    print(f"\n  {'scenario':<48} {'trades':>6}  {'PF':>6}  {'exp':>8}  "
          f"{'long_n/PF':>13}  {'short_n/PF':>14}  verdict")
    print("  " + "-" * 124)
    for r in sorted(results, key=lambda x: -(x["PF"] or 0)):
        print(_fmt_row(r))

    best = max(results, key=lambda r: ((r["PF"] or 0), r["exp"] or 0))
    pass_count = sum(
        1 for r in results
        if (r["PF"] is not None and r["PF"] > 1.0 and (r["exp"] or 0) > 0)
    )
    print()
    print("=" * 130)
    pf_t = f"{best['PF']:.3f}" if best['PF'] is not None else "-"
    print(f"BEST: {best['name']}  PF={pf_t}  exp={(best['exp'] or 0)*100:+.5f}%  "
          f"n={best['trades']}  | scenarios with PF>1+exp>0: {pass_count}/{len(results)}")
    print("=" * 130)

    out_path = _REPO_ROOT / "reports" / "airborne_filter_sweep_5y.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "months": args.months, "symbols_count": len(panels),
        "cost_bps": args.cost_bps, "base_stop": args.stop, "base_tp": args.tp,
        "scenarios": results,
        "best": best,
        "pass_count": pass_count,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str),
                        encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(_REPO_ROOT).as_posix()}")
    return 0


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bench_airborne_filter_sweep_5y")
    p.add_argument("--months", type=int, default=60)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--stop", type=float, default=0.03)
    p.add_argument("--tp", type=float, default=0.06)
    p.add_argument("--cost-bps", type=float, default=10.0)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = _parse(argv)
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    sys.exit(main())
