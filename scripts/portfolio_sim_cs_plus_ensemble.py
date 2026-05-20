"""Portfolio-level sim — cs_tsmom_crypto 단독 vs (cs_tsmom + Candidate-C 4-parallel) 합성.

Run cs_tsmom_crypto backtest end-to-end to get its daily PnL series, load
the 4 live-scanner BN 1d series from validate_bn_1d_v3.json, build the
Candidate-C 4-parallel ensemble, then evaluate 4 portfolios:

  1. cs_tsmom solo
  2. Candidate-C 4-parallel solo (already in robustness_bn_1d.json: SR 2.38)
  3. 50/50 cs_tsmom + Cand-C
  4. 70/30 cs_tsmom + Cand-C

Output: SR / MDD / CAGR / Calmar / correlation matrix. Tells us whether
adding the live-scanner ensemble on top of cs_tsmom provides material
diversification, or whether cs_tsmom alone is sufficient.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))
sys.path.insert(0, str(_REPO / "src"))
sys.path.insert(0, str(_REPO / "scripts"))


def _annualised_sr(s: pd.Series, periods: int = 365) -> float:
    if len(s) < 2:
        return 0.0
    std = float(s.std(ddof=1))
    return (float(s.mean()) / std) * np.sqrt(periods) if std > 0 else 0.0


def _mdd(s: pd.Series) -> float:
    if s.empty:
        return 0.0
    eq = np.cumprod(1.0 + s.to_numpy())
    return float(np.min(eq / np.maximum.accumulate(eq) - 1.0))


def _cagr(s: pd.Series, periods: int = 365) -> float:
    if s.empty:
        return 0.0
    final = float(np.prod(1.0 + s.to_numpy()))
    return final ** (periods / max(len(s), 1)) - 1.0 if final > 0 else -1.0


def _metrics(s: pd.Series, label: str, periods: int = 365) -> dict:
    sr = _annualised_sr(s, periods=periods)
    mdd = _mdd(s)
    cagr = _cagr(s, periods=periods)
    calmar = (cagr / abs(mdd)) if mdd < 0 else float("inf")
    return {
        "label": label, "n_days": int(len(s)),
        "sr": sr, "mdd": mdd, "cagr": cagr, "calmar": calmar,
    }


def _print_metrics(m: dict) -> None:
    print(f"  {m['label']:<42}  n={m['n_days']:>5}  SR={m['sr']:>+6.3f}  "
          f"MDD={m['mdd']*100:>+7.2f}%  CAGR={m['cagr']*100:>+7.2f}%  "
          f"Calmar={m['calmar']:>+6.2f}")


def main() -> int:
    t0 = time.time()

    # ─── 1) cs_tsmom_crypto daily series 산출 ─────────────────────
    print("[1/4] Running cs_tsmom_crypto backtest (5-10 min)...", flush=True)
    import bench_cs_tsmom_crypto as bn
    universe = bn.fetch_top_universe(bn.DEFAULT_UNIVERSE_SIZE)
    panels = bn.fetch_universe(universe, bn.DEFAULT_START, bn.DEFAULT_END,
                               refresh=False, max_workers=3)
    panels = {s: df for s, df in panels.items() if len(df) > bn.DEFAULT_LONG_LB}
    closes, quote_vol = bn.build_panels(panels)
    weights = bn.cs_tsmom_signals(
        closes, quote_vol,
        long_lb=bn.DEFAULT_LONG_LB, skip_lb=bn.DEFAULT_SKIP_LB,
        top_n=bn.DEFAULT_TOP_N, min_quote_vol=1e7,
        rebal_freq=bn.DEFAULT_REBAL,
    )
    btc = closes.get("BTCUSDT")
    weights = bn.apply_btc_crash_guard(weights, btc, lb=bn.DEFAULT_LONG_LB,
                                       dd_threshold=bn.DEFAULT_DD_GUARD)
    bt = bn.backtest(weights, closes, cost_bps=bn.DEFAULT_COST_BPS)
    cs_ret = bt["ret"]
    # eval_start 이후만 사용 (warmup 제외)
    eval_start = pd.Timestamp(bn.BACKTEST_START)
    if cs_ret.index.tz is not None:
        cs_ret.index = cs_ret.index.tz_localize(None)
    cs_ret = cs_ret[cs_ret.index >= eval_start]
    print(f"  cs_tsmom daily series: {len(cs_ret)} days "
          f"[{cs_ret.index[0].date()}..{cs_ret.index[-1].date()}]", flush=True)

    # ─── 2) 후보 C series 산출 (validate_bn_1d_v3 의 4 sub 합성) ─
    print("[2/4] Loading 4 live-scanner sub series + computing Candidate-C ...",
          flush=True)
    sub_data = json.loads(
        (_REPO / "reports/validate_bn_1d_v3.json").read_text()
    )["daily_series"]
    weights_c = {
        "live_rsi_oversold_volume_spike": 0.30,
        "live_breakout_with_atr_stop":    0.30,
        "live_bb_lower_bounce":           0.20,
        "live_oversold_with_divergence":  0.20,
    }
    sub_series: dict[str, pd.Series] = {}
    for sid, w in weights_c.items():
        d = sub_data[sid]
        if not d["dates"]:
            sub_series[sid] = pd.Series(dtype=float); continue
        s = pd.Series(d["rets"], index=pd.to_datetime(d["dates"]))
        if s.index.tz is not None:
            s.index = s.index.tz_localize(None)
        sub_series[sid] = s

    # 4-parallel weighted equal ensemble (각자 weight 비율로 PnL 기여) × half-kelly
    idx_c = pd.DatetimeIndex(sorted(set().union(
        *[set(s.index) for s in sub_series.values() if not s.empty]
    )))
    cand_c = pd.Series(0.0, index=idx_c)
    for sid, w in weights_c.items():
        cand_c = cand_c.add(
            sub_series[sid].reindex(idx_c, fill_value=0.0) * w, fill_value=0.0,
        )
    cand_c = cand_c * 0.5  # half-kelly
    print(f"  Candidate-C series: {len(cand_c)} days "
          f"[{cand_c.index[0].date()}..{cand_c.index[-1].date()}]", flush=True)

    # ─── 3) 공통 인덱스 정렬 ────────────────────────────────────
    common = cs_ret.index.intersection(cand_c.index)
    print(f"[3/4] Aligning on common dates: {len(common)} days", flush=True)
    cs_aligned = cs_ret.reindex(common, fill_value=0.0)
    cand_aligned = cand_c.reindex(common, fill_value=0.0)

    # ─── 4) Portfolio combinations ──────────────────────────────
    print("[4/4] Computing portfolio metrics...", flush=True)
    portfolios = [
        ("cs_tsmom_crypto solo",            cs_aligned),
        ("Candidate-C 4-parallel × hk solo", cand_aligned),
        ("50/50 cs_tsmom + Cand-C",         0.5 * cs_aligned + 0.5 * cand_aligned),
        ("70/30 cs_tsmom + Cand-C",         0.7 * cs_aligned + 0.3 * cand_aligned),
        ("30/70 cs_tsmom + Cand-C",         0.3 * cs_aligned + 0.7 * cand_aligned),
    ]
    corr = float(cs_aligned.corr(cand_aligned)) if len(common) >= 2 else float("nan")

    print()
    print("=" * 100)
    print("PORTFOLIO-LEVEL SIM (cs_tsmom_crypto vs Candidate-C 4-parallel)")
    print("=" * 100)
    print(f"common range: {common[0].date()} .. {common[-1].date()}  "
          f"({len(common)} days)")
    print(f"correlation(cs_tsmom, Cand-C) = {corr:+.4f}")
    print("-" * 100)
    for label, s in portfolios:
        _print_metrics(_metrics(s, label))
    print("=" * 100)
    print(f"elapsed: {time.time()-t0:.1f}s")

    # JSON dump
    out_path = _REPO / "reports/portfolio_cs_plus_cand_c.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out = {
        "common_start": str(common[0].date()),
        "common_end": str(common[-1].date()),
        "n_days": len(common),
        "correlation_cs_vs_cand_c": corr,
        "portfolios": [
            {**_metrics(s, label)} for label, s in portfolios
        ],
    }
    out_path.write_text(json.dumps(out, indent=2, default=float))
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
