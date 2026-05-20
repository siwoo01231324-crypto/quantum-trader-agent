"""Walk-forward + Ensemble + Half-Kelly post-analysis for live-scanner DSR runs.

Reads ``reports/validate_{universe}_{bar}_v3.json`` (validate_live_scanners.py
output with daily_series), then:

  1. Walk-forward — split full calendar into N year-windows; for each strategy
     report SR / PF / expectancy / MDD per window. Robust iff metrics positive
     in ≥80% of windows.
  2. Ensemble — combine 4 strategies equal-weight (or weighted), report combined
     SR + MDD vs individual best.
  3. Half-Kelly — scale daily returns by 0.5; report combined MDD reduction.

Pure post-processing — no replay, no panel load. Runs in seconds.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

STRATEGIES = [
    "live_bb_lower_bounce",
    "live_rsi_oversold_volume_spike",
    "live_oversold_with_divergence",
    "live_breakout_with_atr_stop",
]


def _series_from_dump(d: dict) -> pd.Series:
    if not d.get("dates"):
        return pd.Series(dtype=float)
    return pd.Series(d["rets"], index=pd.to_datetime(d["dates"]))


def _annualised_sr(s: pd.Series) -> float:
    if len(s) < 2:
        return 0.0
    std = float(s.std(ddof=1))
    return (float(s.mean()) / std) * np.sqrt(252) if std > 0 else 0.0


def _mdd(s: pd.Series) -> float:
    if s.empty:
        return 0.0
    eq = np.cumprod(1.0 + s.to_numpy())
    if eq.size == 0:
        return 0.0
    return float(np.min(eq / np.maximum.accumulate(eq) - 1.0))


def _pf_exp(s: pd.Series) -> tuple[float, float, int]:
    if s.empty:
        return 0.0, 0.0, 0
    pos = s[s > 0].sum()
    neg = s[s < 0].sum()
    pf = (float(pos) / abs(float(neg))) if neg != 0 else float("inf")
    exp = float(s.mean())
    n_nonzero = int((s != 0).sum())
    return pf, exp, n_nonzero


def _walk_forward(series: dict[str, pd.Series]) -> dict:
    """Split each series by calendar year and report per-year metrics."""
    all_idx = pd.DatetimeIndex(sorted(set().union(
        *[set(s.index) for s in series.values() if not s.empty]
    )))
    if len(all_idx) == 0:
        return {}
    years = sorted(all_idx.year.unique())
    out = {}
    for sid in STRATEGIES:
        s = series.get(sid, pd.Series(dtype=float))
        per_year = []
        for y in years:
            sub = s[s.index.year == y] if not s.empty else pd.Series(dtype=float)
            pf, exp, nz = _pf_exp(sub)
            per_year.append({
                "year": int(y),
                "n_days": int(len(sub)),
                "n_active": nz,
                "sr_ann": _annualised_sr(sub),
                "pf": pf,
                "exp_per_day": exp,
                "mdd": _mdd(sub),
                "final_equity": float(np.prod(1.0 + sub.to_numpy())) if not sub.empty else 1.0,
            })
        # Consistency: fraction of years with PF>1 AND exp>0
        ok = sum(1 for y in per_year if y["n_active"] > 0 and y["pf"] > 1.0 and y["exp_per_day"] > 0)
        active = sum(1 for y in per_year if y["n_active"] > 0)
        out[sid] = {"per_year": per_year, "n_ok_years": ok, "n_active_years": active}
    return out


def _ensemble(series: dict[str, pd.Series], weights: dict[str, float] | None = None
              ) -> pd.Series:
    """Equal-weight (or given) ensemble daily return series."""
    nonempty = {k: v for k, v in series.items() if not v.empty}
    if not nonempty:
        return pd.Series(dtype=float)
    idx = pd.DatetimeIndex(sorted(set().union(*[set(v.index) for v in nonempty.values()])))
    if weights is None:
        weights = {k: 1.0 / len(nonempty) for k in nonempty}
    out = pd.Series(0.0, index=idx)
    for sid, s in nonempty.items():
        w = weights.get(sid, 0.0)
        if w == 0.0:
            continue
        out = out.add(s.reindex(idx, fill_value=0.0) * w, fill_value=0.0)
    return out


def _fmt_pct(x: float) -> str:
    if not np.isfinite(x):
        return "  inf  "
    return f"{x*100:+7.2f}%"


def _print_walkforward(wf: dict, title: str) -> None:
    print(f"\n=== Walk-Forward {title} ===")
    if not wf:
        print("  (empty)")
        return
    # Header: years
    years = sorted({yy["year"] for v in wf.values() for yy in v["per_year"]})
    hdr = f"  {'strategy':<36}" + "".join(f"{y:>10}" for y in years) + f"  {'consistency':>14}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for sid, v in wf.items():
        by_y = {y["year"]: y for y in v["per_year"]}
        row = f"  {sid:<36}"
        for y in years:
            yi = by_y.get(y)
            if yi is None or yi["n_active"] == 0:
                row += f"{'  -  ':>10}"
            else:
                # show PF abbreviation
                pf = yi["pf"]
                ok = "+" if (pf > 1.0 and yi["exp_per_day"] > 0) else "-"
                pf_s = f"{pf:.2f}" if np.isfinite(pf) else "inf"
                row += f"{ok}{pf_s:>9}"
        cons = f"{v['n_ok_years']}/{v['n_active_years']}"
        row += f"  {cons:>14}"
        print(row)
    print("  legend: per-year PF (+OK / -LOSE).  consistency = years with PF>1 ∧ exp>0 / active years")


def _print_ensemble(label: str, s: pd.Series, scale: float = 1.0) -> dict:
    if scale != 1.0:
        s = s * scale
    sr = _annualised_sr(s)
    mdd = _mdd(s)
    pf, exp, _ = _pf_exp(s)
    eq_final = float(np.prod(1.0 + s.to_numpy())) if not s.empty else 1.0
    cagr = (eq_final ** (252.0 / max(len(s), 1)) - 1.0) if eq_final > 0 else -1.0
    print(f"  {label:<48} SR={sr:>+6.3f}  MDD={mdd*100:>+7.2f}%  "
          f"PF={pf:>5.3f}  exp/day={exp*100:>+7.4f}%  CAGR≈{cagr*100:>+7.2f}%  "
          f"final_eq={eq_final:>7.3f}")
    return {"sr": sr, "mdd": mdd, "pf": pf, "exp_per_day": exp,
            "cagr_approx": cagr, "final_equity": eq_final, "n_days": int(len(s))}


def _analyze_one(path: Path, label: str, output_path: Path) -> dict:
    data = json.loads(path.read_text())
    series = {sid: _series_from_dump(d) for sid, d in data["daily_series"].items()}

    # Walk-forward
    wf = _walk_forward(series)
    _print_walkforward(wf, label)

    # Ensemble + Half-Kelly
    print(f"\n=== Risk-Operationalization {label} (cost {data['cost_bps']}bp) ===")
    out = {"walk_forward": wf, "ops": {}}
    # 1) Equal-weight ensemble (all 4)
    ens_all = _ensemble(series)
    out["ops"]["ensemble_all_4_equal"] = _print_ensemble("ensemble (all 4, equal weight)", ens_all)
    # 2) Equal-weight ensemble — DSR-pass subset only (BN 1d: rsi+breakout. KRX: all 4.)
    pass_strats = [sid for sid, st in data["per_strategy"].items()
                   if st.get("psr", 0) >= 0.95 and st.get("dsr", 0) >= 0.95]
    if pass_strats and len(pass_strats) != len(series):
        ens_pass = _ensemble({s: series[s] for s in pass_strats})
        out["ops"][f"ensemble_dsr_pass_{len(pass_strats)}"] = _print_ensemble(
            f"ensemble (DSR-pass only: {','.join(s.split('_',1)[1] for s in pass_strats)})",
            ens_pass,
        )
    # 3) Half-Kelly on full ensemble
    out["ops"]["ensemble_all_half_kelly"] = _print_ensemble(
        "ensemble (all 4, equal) × half-kelly (0.5)", ens_all, scale=0.5,
    )
    # 4) Best individual for baseline
    best_sid = max(series, key=lambda k: _annualised_sr(series[k]))
    out["ops"][f"best_individual_{best_sid}"] = _print_ensemble(
        f"best individual: {best_sid}", series[best_sid],
    )
    # 5) Best individual × half-kelly
    out["ops"][f"best_individual_{best_sid}_half_kelly"] = _print_ensemble(
        f"best individual × half-kelly", series[best_sid], scale=0.5,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out, indent=2, default=float))
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--krx", default="reports/validate_krx_1d_v3.json")
    p.add_argument("--binance", default="reports/validate_bn_1d_v3.json")
    args = p.parse_args(argv)

    repo = Path(__file__).resolve().parents[1]
    for label, path in [("KRX 1d", repo / args.krx), ("Binance 1d", repo / args.binance)]:
        if not path.exists():
            print(f"[skip] {label}: {path} not found")
            continue
        print(f"\n{'='*100}\n{label}\n{'='*100}")
        out_json = repo / "reports" / f"robustness_{path.stem.split('_',1)[1].rsplit('_v',1)[0]}.json"
        _analyze_one(path, label, out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
