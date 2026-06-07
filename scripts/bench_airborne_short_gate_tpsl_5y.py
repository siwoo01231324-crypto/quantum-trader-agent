"""#380 검증용 임시 — SHORT airborne 24h vs 게이트 + 진입파라미터/TP·SL 매트릭스 5y.
hour_sweep 모듈 함수 재활용. 결과: reports/_tmp_gate_matrix_5y.json + 콘솔표.
"""
from __future__ import annotations
import sys, json, importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "src"))

spec = importlib.util.spec_from_file_location(
    "hs", str(ROOT / "scripts" / "airborne_short_whitelist_hour_sweep.py"))
hs = importlib.util.module_from_spec(spec); spec.loader.exec_module(hs)

GATE = {1, 2, 3, 6, 7, 8, 23}
CACHE = ROOT / "data" / "cache" / "binance_1h"
symbols = sorted(p.stem for p in CACHE.glob("*.parquet"))
print(f"universe: {len(symbols)} symbols (cached 1h)", flush=True)

# 진입파라미터 × TP/SL 추출 config (각자 1패스)
ENTRY_CFG = {
    "relaxed(0.4/0.6)": dict(retrace=0.4, atr_body_mult=0.6),
    "hardoos(0.6/0.3)": dict(retrace=0.6, atr_body_mult=0.3),
}
TPSL_CFG = {"tp1/sl0.5": (0.01, 0.005), "tp6/sl3": (0.06, 0.03)}


def run_extract(entry, sl, tp):
    hs.ENTRY = dict(retrace=entry["retrace"], bb_window=20, bb_std=2.0,
                    min_margin=0.001, atr_body_mult=entry["atr_body_mult"], atr_period=14)
    hs.SL = sl; hs.TP = tp
    fires = []
    for i, sym in enumerate(symbols):
        panel = hs.load_panel(sym, months=72)
        if panel is None:
            continue
        fund = hs.load_funding(sym)
        try:
            fires += hs.extract_short_fires(sym, panel, fund)
        except Exception as e:
            print(f"  skip {sym}: {e}", flush=True)
        if (i + 1) % 30 == 0:
            print(f"    ...{i+1}/{len(symbols)} fires={len(fires)}", flush=True)
    return fires


def period_of(year):
    if 2021 <= year <= 2023: return "train21-23"
    if 2024 <= year <= 2025: return "test24-25"
    return "other"


def summarize(fires, gate_only):
    buckets = {"train21-23": [], "test24-25": [], "full": []}
    for f in fires:
        if gate_only and f["kst_hour"] not in GATE:
            continue
        r = f["ret_funded"]; p = period_of(f["year"])
        if p in buckets: buckets[p].append(r)
        buckets["full"].append(r)
    return {k: hs.agg(v) for k, v in buckets.items()}


results = {}
for ename, entry in ENTRY_CFG.items():
    for tname, (tp, sl) in TPSL_CFG.items():
        key = f"{ename} | {tname}"
        print(f"\n>>> 추출: {key}", flush=True)
        fires = run_extract(entry, sl, tp)
        print(f"    총 SHORT fires: {len(fires)}", flush=True)
        results[key] = {
            "24h": summarize(fires, gate_only=False),
            "gate{1,2,3,6,7,8,23}": summarize(fires, gate_only=True),
        }

out = ROOT / "reports" / "_tmp_gate_matrix_5y.json"
out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

# 콘솔 표
print("\n\n================ 결과 (PF / exp%/거래 / sumR% / win% / n) ================")
for key, gv in results.items():
    print(f"\n### {key}")
    for gate_label, periods in gv.items():
        print(f"  [{gate_label}]")
        for per, a in periods.items():
            pf = a["PF"]; pf = f"{pf:.3f}" if pf else "inf/NA"
            print(f"    {per:11s} PF={pf:>7s}  exp={a['exp']*100:+.3f}%  "
                  f"sumR={a['sum_R']*100:+7.1f}%  win={a['win_rate']*100:4.0f}%  n={a['n']}")
print(f"\nsaved: {out}", flush=True)
