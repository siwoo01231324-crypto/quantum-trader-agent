"""5y bench for v1.2 bidir + KST 06-12 시간 필터 — vs unfiltered baseline.

CLAUDE.md "새 전략 추가 시 필수" 5y backtest 게이트:
    PF > 1.0 AND expectancy > 0  (5y · 다중 레짐 · 비용 ≥ 10bp).

Pine v1.2 (close-기반 + 0.1% margin + ATR-적응 body) 양방향 시뮬을
``scripts/bench_live_airborne_v11_bidir.py`` 의 ``simulate_bidir`` 구조에서
가져와 (1) v1.1 절대 body 게이트를 v1.2 ATR body 로 교체, (2) KST hour 게이트
추가, 두 가지 변경. ``bench_live_scanner._replay_symbol`` 은 long-only 라
bidir 시그널 평가에 부적합 — 본 스크립트가 자체 bidir 시뮬 가짐.

비교:
- filtered: KST 06-12 fire 만 진입 (12-05 시각 fire 는 setup 만 추적, 진입 X)
- baseline: 모든 시각 fire 진입 (= 순수 v1.2 bidir 알파)

출력: ``reports/eval_live_airborne_kst_morning_5y.json``
- ``filtered`` / ``baseline`` 각각 PF/expectancy/long_PF/short_PF/trades
- ``gate_pass`` boolean (filtered PF > 1 AND filtered exp > 0)
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import importlib
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

Freq = Literal["1m", "15m", "1h", "4h"]

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))
sys.path.insert(0, str(_REPO_ROOT / "src"))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

import signals  # noqa: E402
from signals.airborne_bb_reversal import (  # noqa: E402
    DEFAULT_ATR_BODY_MULT_V11,
    DEFAULT_ATR_PERIOD_V11,
    DEFAULT_MIN_CLOSE_MARGIN_V11,
    RETRACE_RATIO,
)

logger = logging.getLogger("bench_live_airborne_kst_morning_5y")

_KST = ZoneInfo("Asia/Seoul")
_KST_MORNING_HOURS: frozenset[int] = frozenset({6, 7, 8, 9, 10, 11})

BB_WINDOW = 20
BB_STD = 2.0

SWEEP_RR = [
    (0.005, 0.010, "0.5/1.0 (1:2)"),
    (0.010, 0.020, "1.0/2.0 (1:2)"),
    (0.015, 0.030, "1.5/3.0 (1:2)"),
    (0.020, 0.040, "2.0/4.0 (1:2)"),
    (0.030, 0.060, "3.0/6.0 (1:2)"),
    (0.010, 0.030, "1.0/3.0 (1:3)"),
    (0.020, 0.060, "2.0/6.0 (1:3)"),
    (0.005, 0.020, "0.5/2.0 (1:4)"),
]

FREQ_RULE: dict[Freq, str] = {"1m": "1min", "15m": "15min", "1h": "1h", "4h": "4h"}


def _resample(df_1m: pd.DataFrame, freq: Freq) -> pd.DataFrame:
    if freq == "1m":
        return df_1m
    rule = FREQ_RULE[freq]
    agg = {"open": "first", "high": "max", "low": "min",
           "close": "last", "volume": "sum"}
    cols = [c for c in agg if c in df_1m.columns]
    return (
        df_1m[cols]
        .resample(rule, label="right", closed="right")
        .agg({k: agg[k] for k in cols})
        .dropna(subset=["close"])
    )


def _load_universe_symbols(top_n: int) -> list[str]:
    from src.portfolio.binance_universe import BINANCE_USDT_TOP30
    return list(BINANCE_USDT_TOP30)[:top_n]


def _load_panels(symbols: list[str], months: int, freq: Freq,
                 ) -> tuple[dict[str, pd.DataFrame], dict[str, tuple[str, str]]]:
    cache_dir = _REPO_ROOT / "data" / "cache" / "binance_1m"
    if not cache_dir.exists():
        logger.error("binance_1m cache missing at %s", cache_dir)
        return {}, {}
    selected: dict[str, pd.DataFrame] = {}
    coverage: dict[str, tuple[str, str]] = {}
    for sym in symbols:
        path = cache_dir / f"{sym}.parquet"
        if not path.exists():
            logger.warning("symbol %s not in cache, skipping", sym)
            continue
        p1m = pd.read_parquet(path)
        if p1m.index.tz is None:
            p1m = p1m.tz_localize("UTC")
        last_ts = p1m.index.max()
        first_ts = last_ts - pd.DateOffset(months=months)
        p1m = p1m.loc[first_ts:last_ts]
        panel = _resample(p1m, freq)
        if len(panel) < 60:
            logger.warning("%s: only %d %s bars after %dmo cut - skip",
                           sym, len(panel), freq, months)
            continue
        selected[sym] = panel
        coverage[sym] = (str(panel.index[0].date()), str(panel.index[-1].date()))
        logger.info("  %s @ %s: 1m=%d -> %d bars  [%s..%s]",
                    sym, freq, len(p1m), len(panel),
                    panel.index[0].date(), panel.index[-1].date())
        del p1m
    gc.collect()
    return selected, coverage


def _wilder_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                period: int) -> np.ndarray:
    """Pine ``ta.atr`` 동일 (RMA of True Range)."""
    n = len(close)
    atr = np.full(n, np.nan)
    if n < 2:
        return atr
    tr = np.zeros(n)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        a = high[i] - low[i]
        b = abs(high[i] - close[i - 1])
        c = abs(low[i] - close[i - 1])
        tr[i] = max(a, b, c)
    # Wilder smoothing (== EMA with alpha=1/period; pine ta.atr uses RMA)
    atr[period] = tr[1:period + 1].mean()
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
    return atr


def simulate_bidir_v12(
    panel: pd.DataFrame,
    *,
    stop: float,
    tp: float,
    cost_bps: float,
    kst_filter: bool,
    min_margin: float = DEFAULT_MIN_CLOSE_MARGIN_V11,
    atr_period: int = DEFAULT_ATR_PERIOD_V11,
    atr_body_mult: float = DEFAULT_ATR_BODY_MULT_V11,
) -> list[dict]:
    """Pine v1.2 bidir 시뮬 + 선택적 KST 시간 필터.

    ``kst_filter=True`` 면 fire 가 발생해도 KST hour 가 06-11 이 아닐 때
    진입을 *skip* (state machine 은 그대로 종료). 시간 필터 차단은 진입만 —
    이미 보유한 포지션의 stop/TP 청산은 모든 시각에 동일.
    """
    bb = signals.compute("bollinger", close=panel["close"],
                         window=BB_WINDOW, n_std=BB_STD)
    upper = bb["upper"].to_numpy()
    lower = bb["lower"].to_numpy()
    closes = panel["close"].to_numpy()
    opens = panel["open"].to_numpy()
    highs = panel["high"].to_numpy()
    lows = panel["low"].to_numpy()
    body_abs = np.abs(closes - opens)
    atr = _wilder_atr(highs, lows, closes, atr_period)

    upper_thr = upper * (1 + min_margin)
    lower_thr = lower * (1 - min_margin)
    n = len(panel)

    times = panel.index
    if times.tz is None:
        times = times.tz_localize("UTC")
    kst_hours = times.tz_convert(_KST).hour.to_numpy()

    upper_break = np.zeros(n, dtype=bool)
    lower_break = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if (np.isnan(upper_thr[i]) or np.isnan(upper_thr[i - 1])
                or np.isnan(atr[i])):
            continue
        body_ok = body_abs[i] >= atr_body_mult * atr[i]
        if not body_ok:
            continue
        if closes[i] > upper_thr[i] and closes[i - 1] <= upper_thr[i - 1]:
            upper_break[i] = True
        elif closes[i] < lower_thr[i] and closes[i - 1] >= lower_thr[i - 1]:
            lower_break[i] = True

    # v1.2 state machine
    state = 0  # 0=none, 1=long_setup, 2=short_setup
    base = np.nan
    extreme = np.nan
    fires: list[tuple[int, str, float]] = []  # (bar_index, side, entry_close)

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

    # Trade simulation w/ optional KST filter on entries
    trades = []
    in_pos = False
    pos_side = None
    pos_entry = 0.0
    pos_entry_i = 0
    fire_idx = 0
    cost = cost_bps / 10000.0

    for i in range(n):
        if in_pos:
            if pos_side == "long":
                sl_px = pos_entry * (1 - stop)
                tp_px = pos_entry * (1 + tp)
                exit_reason, exit_px = None, None
                if lows[i] <= sl_px:
                    exit_reason, exit_px = "stop_loss", sl_px
                elif highs[i] >= tp_px:
                    exit_reason, exit_px = "take_profit", tp_px
                if exit_reason:
                    ret = (exit_px / pos_entry) - 1 - 2 * cost
                    trades.append({
                        "side": "long",
                        "entry_ts": times[pos_entry_i].isoformat(),
                        "exit_ts": times[i].isoformat(),
                        "entry": pos_entry, "exit": exit_px,
                        "ret": ret, "exit_reason": exit_reason,
                    })
                    in_pos = False
            else:
                sl_px = pos_entry * (1 + stop)
                tp_px = pos_entry * (1 - tp)
                exit_reason, exit_px = None, None
                if highs[i] >= sl_px:
                    exit_reason, exit_px = "stop_loss", sl_px
                elif lows[i] <= tp_px:
                    exit_reason, exit_px = "take_profit", tp_px
                if exit_reason:
                    ret = 1 - (exit_px / pos_entry) - 2 * cost
                    trades.append({
                        "side": "short",
                        "entry_ts": times[pos_entry_i].isoformat(),
                        "exit_ts": times[i].isoformat(),
                        "entry": pos_entry, "exit": exit_px,
                        "ret": ret, "exit_reason": exit_reason,
                    })
                    in_pos = False

        if not in_pos and fire_idx < len(fires) and fires[fire_idx][0] == i:
            _, side, entry = fires[fire_idx]
            allowed = (not kst_filter) or (int(kst_hours[i]) in _KST_MORNING_HOURS)
            if allowed:
                in_pos = True
                pos_side = side
                pos_entry = entry
                pos_entry_i = i
            fire_idx += 1
        elif fire_idx < len(fires) and fires[fire_idx][0] <= i:
            while fire_idx < len(fires) and fires[fire_idx][0] <= i:
                fire_idx += 1

    return trades


def aggregate(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0, "win_rate": 0.0, "payoff": None, "PF": None,
                "exp": 0.0, "long_n": 0, "short_n": 0,
                "long_PF": None, "short_PF": None,
                "long_exp": 0.0, "short_exp": 0.0}
    rets = np.array([t["ret"] for t in trades])
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    tp_sum = float(wins.sum()) if len(wins) else 0.0
    tl_sum = float(-losses.sum()) if len(losses) else 0.0
    pf = tp_sum / tl_sum if tl_sum > 0 else None
    avg_w = float(wins.mean()) if len(wins) else 0.0
    avg_l = float(losses.mean()) if len(losses) else 0.0
    payoff = abs(avg_w / avg_l) if avg_l < 0 else None
    exp = float(rets.mean())
    win_rate = float(len(wins) / len(trades))

    def side_stats(side: str) -> dict:
        ts = [t for t in trades if t["side"] == side]
        if not ts:
            return {"n": 0, "pf": None, "exp": 0.0}
        r = np.array([t["ret"] for t in ts])
        p = float(r[r > 0].sum()); l = float(-r[r <= 0].sum())
        return {"n": len(ts),
                "pf": (p / l) if l > 0 else None,
                "exp": float(r.mean())}

    ls = side_stats("long")
    ss = side_stats("short")
    return {"trades": len(trades), "win_rate": win_rate,
            "payoff": payoff, "PF": pf, "exp": exp,
            "long_n": ls["n"], "short_n": ss["n"],
            "long_PF": ls["pf"], "short_PF": ss["pf"],
            "long_exp": ls["exp"], "short_exp": ss["exp"]}


def run_combo(panels: dict[str, pd.DataFrame], *, stop: float, tp: float,
              cost_bps: float, kst_filter: bool) -> dict:
    all_trades = []
    for sym, panel in panels.items():
        all_trades.extend(simulate_bidir_v12(
            panel, stop=stop, tp=tp, cost_bps=cost_bps, kst_filter=kst_filter,
        ))
    return aggregate(all_trades)


def _fmt_header() -> str:
    return (f"  {'label':<20} {'trades':>7} {'win':>7} {'PF':>7} {'exp':>9}  "
            f"{'long_n/PF':>14} {'short_n/PF':>14}  verdict")


def _fmt_row(label: str, m: dict) -> str:
    pf = m["PF"]; exp = m["exp"]
    verdict = "PASS" if (pf is not None and pf > 1.0 and exp > 0) else "LOSER"
    pf_t = f"{pf:6.3f}" if pf is not None else "  inf "
    lpf = f"{m['long_PF']:5.2f}" if m['long_PF'] is not None else "  -  "
    spf = f"{m['short_PF']:5.2f}" if m['short_PF'] is not None else "  -  "
    return (
        f"  {label:<20} {m['trades']:>7} {m['win_rate']*100:6.2f}% "
        f"{pf_t} {exp*100:+8.5f}%  "
        f"L={m['long_n']:>4}/{lpf} S={m['short_n']:>4}/{spf}  {verdict}"
    )


async def _main_async(args: argparse.Namespace) -> int:
    t0 = time.time()
    symbols = _load_universe_symbols(args.top_n)
    if args.freq not in FREQ_RULE:
        logger.error("unknown freq %s", args.freq)
        return 2
    panels, coverage = _load_panels(symbols, args.months, args.freq)
    if not panels:
        logger.error("no usable panels - abort.")
        return 3

    combos = SWEEP_RR if args.sweep_rr else [
        (args.stop, args.tp, f"{args.stop*100:.1f}/{args.tp*100:.1f}"),
    ]

    print("\n" + "=" * 130)
    print(f"live_airborne_bb_reversal_kst_morning  (v1.2 bidir + KST 06-12)")
    print(f"  freq={args.freq}  months={args.months}  symbols={len(panels)}  "
          f"cost={args.cost_bps:.0f}bp")
    print("=" * 130)

    filtered_rows: list[dict] = []
    baseline_rows: list[dict] = []
    for stop, tp, label in combos:
        c0 = time.time()
        ef = await asyncio.to_thread(
            run_combo, panels, stop=stop, tp=tp,
            cost_bps=args.cost_bps, kst_filter=True,
        )
        c1 = time.time()
        eb = await asyncio.to_thread(
            run_combo, panels, stop=stop, tp=tp,
            cost_bps=args.cost_bps, kst_filter=False,
        )
        c2 = time.time()
        filtered_rows.append({"label": label, "stop": stop, "tp": tp, **ef})
        baseline_rows.append({"label": label, "stop": stop, "tp": tp, **eb})
        pf_f, pf_b = ef.get("PF"), eb.get("PF")
        logger.info(
            "  %s: filtered=%s exp=%+.5f%% (%.1fs)  baseline=%s exp=%+.5f%% (%.1fs)",
            label,
            f"PF={pf_f:.3f}" if pf_f is not None else "PF=inf",
            ef["exp"] * 100, c1 - c0,
            f"PF={pf_b:.3f}" if pf_b is not None else "PF=inf",
            eb["exp"] * 100, c2 - c1,
        )

    print(f"\n[KST 06-12 morning filter]")
    print(_fmt_header())
    print("  " + "-" * 116)
    for row in sorted(filtered_rows, key=lambda r: -(r["PF"] or 0)):
        print(_fmt_row(row["label"], row))

    print(f"\n[baseline - no KST filter (24h v1.2 bidir)]")
    print(_fmt_header())
    print("  " + "-" * 116)
    for row in sorted(baseline_rows, key=lambda r: -(r["PF"] or 0)):
        print(_fmt_row(row["label"], row))

    best_filtered = max(
        filtered_rows, key=lambda r: ((r["PF"] or 0), r["exp"]),
    )
    pf = best_filtered.get("PF")
    exp = best_filtered["exp"]
    gate_pass = (pf is not None and pf > 1.0 and exp > 0)
    print()
    print("=" * 130)
    print(f"GATE (CLAUDE.md 5y PF>1 AND expectancy>0): {'PASS' if gate_pass else 'FAIL'}")
    pf_txt = f"PF={pf:.3f}" if pf is not None else "PF=inf"
    print(f"  best filtered combo: {best_filtered['label']}  {pf_txt}  "
          f"exp={exp*100:+.5f}%  trades={best_filtered['trades']}  "
          f"L={best_filtered['long_n']} S={best_filtered['short_n']}")
    print("=" * 130)

    out_path = _REPO_ROOT / "reports" / "eval_live_airborne_kst_morning_5y.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "freq": args.freq,
        "months_requested": args.months,
        "symbols_count": len(panels),
        "universe_coverage": {s: list(c) for s, c in coverage.items()},
        "cost_bps": args.cost_bps,
        "kst_entry_hours": sorted(_KST_MORNING_HOURS),
        "pine_v12_params": {
            "min_close_margin": DEFAULT_MIN_CLOSE_MARGIN_V11,
            "atr_period": DEFAULT_ATR_PERIOD_V11,
            "atr_body_mult": DEFAULT_ATR_BODY_MULT_V11,
            "bb_window": BB_WINDOW, "bb_std": BB_STD,
        },
        "filtered": filtered_rows,
        "baseline": baseline_rows,
        "best_filtered": best_filtered,
        "gate_pass": gate_pass,
        "gate_rule": "PF > 1.0 AND expectancy > 0 (CLAUDE.md 5y backtest gate)",
        "elapsed_sec": round(time.time() - t0, 1),
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str),
                        encoding="utf-8")
    print(f"\nwrote {out_path.relative_to(_REPO_ROOT).as_posix()}")
    return 0 if gate_pass else 1


def _parse(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="bench_live_airborne_kst_morning_5y")
    p.add_argument("--freq", type=str, default="1h", choices=list(FREQ_RULE.keys()))
    p.add_argument("--months", type=int, default=60)
    p.add_argument("--top-n", type=int, default=30)
    p.add_argument("--stop", type=float, default=0.03)
    p.add_argument("--tp", type=float, default=0.06)
    p.add_argument("--sweep-rr", action="store_true")
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
