"""One-shot: cand-c-2026-05-20-live-* 4 spec md 에 scalping sweep 결과 기록.

reports/sweep_candc_scalping_*.json (가장 최신) 를 읽고 각 strategy 의 best
combo 를 추출 → spec md frontmatter 업데이트 + 본문 verdict 섹션 추가.

규칙:
- PF>1 AND exp>0 통과 조합 있음 → status: candidate, verdict_5y='passed: PF=...'
- PF>1 통과 없음 → status: rejected, verdict_5y='rejected: best PF=...'
- 항상 sharpe_bt/mdd_bt/pf/exp/sl/tp/trail 기록

자동 enable 금지 (production.yaml 변경은 US-06 별도 단계, 사용자 승인 후).
"""
from __future__ import annotations

import glob
import io
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
SPEC_BY_SID = {
    "live_rsi_oversold_volume_spike":
        "cand-c-2026-05-20-live-rsi-oversold-volume-spike.md",
    "live_breakout_with_atr_stop":
        "cand-c-2026-05-20-live-breakout-with-atr-stop.md",
    "live_bb_lower_bounce":
        "cand-c-2026-05-20-live-bb-lower-bounce.md",
    "live_oversold_with_divergence":
        "cand-c-2026-05-20-live-oversold-with-divergence.md",
}


def _load_latest_sweep() -> dict:
    """가장 최신 sweep JSON 로드. iter 1, iter 2, ..., 5y 순으로 우선순위."""
    paths = sorted(
        glob.glob(str(ROOT / "reports" / "sweep_candc_scalping_*.json")),
        key=lambda p: Path(p).stat().st_mtime,
        reverse=True,
    )
    if not paths:
        raise SystemExit("no reports/sweep_candc_scalping_*.json found")
    print(f"using: {paths[0]}")
    return json.loads(Path(paths[0]).read_text(encoding="utf-8"))


def _best_per_strategy(results: list[dict]) -> dict[str, dict]:
    """각 strategy 의 PF 최고 combo 추출 (error 행 제외)."""
    by_sid: dict[str, dict] = {}
    for r in results:
        sid = r.get("strategy_id")
        if not sid or "error" in r:
            continue
        pf = r.get("profit_factor")
        if pf is None:
            continue
        cur = by_sid.get(sid)
        if cur is None or pf > cur.get("profit_factor", -1e9):
            by_sid[sid] = r
    return by_sid


def _patch_frontmatter(fm: str, updates: dict) -> str:
    for key, val in updates.items():
        pat = re.compile(r"^(" + re.escape(key) + r":)[^\n]*", re.M)
        if pat.search(fm):
            fm = pat.sub(rf"\1 {val}", fm, count=1)
        else:
            fm = fm.rstrip() + f"\n{key}: {val}"
    return fm


def _verdict_section(best: dict, sweep_meta: dict) -> str:
    pf = best.get("profit_factor")
    exp = best.get("expectancy") or 0.0
    pf_str = f"{pf:.3f}" if pf is not None else "—"
    exp_str = f"{exp * 100:+.4f}%"
    passed = pf is not None and pf > 1.0 and exp > 0
    verdict = "PASSED" if passed else "REJECTED"
    sl = best.get("stop_loss_pct")
    tp = best.get("take_profit_pct")
    trail = best.get("trailing_stop_pct")
    today = date.today().isoformat()
    bench_aggr = "bench_live_scanner._aggregate"
    return (
        f"\n\n## Scalping reparam sweep 결과 ({today})\n\n"
        f"**{verdict}.** 견고지표 PF/expectancy 기준.\n\n"
        "| 지표 | best combo |\n|---|---|\n"
        f"| Profit Factor | **{pf_str}** |\n"
        f"| 기대값/거래 | **{exp_str}** |\n"
        f"| 거래수 | {int(best.get('trades', 0)):,} |\n"
        f"| 승률 | {(best.get('win_rate') or 0) * 100:.1f}% |\n"
        f"| stop_loss_pct | {sl} |\n"
        f"| take_profit_pct | {tp} |\n"
        f"| trailing_stop_pct | {trail if trail not in (None, 1.0) else 'OFF'} |\n\n"
        f"조건: period={sweep_meta.get('period')} universe={sweep_meta.get('universe')} "
        f"n_symbols={sweep_meta.get('n_symbols')} cost_bps={sweep_meta.get('cost_bps')}.\n\n"
        f"벤치 Sharpe ({best.get('sharpe_bench')}) 는 일별평균+(252/n_days) 투영 집계 산물로 "
        f"PF<1 일 경우 부호 모순. **결정은 PF·expectancy 만으로**.\n\n"
        f"원자료: `reports/sweep_candc_scalping_*.json`.\n"
    )


def main() -> int:
    sweep = _load_latest_sweep()
    results = sweep.get("results", [])
    bests = _best_per_strategy(results)
    print(f"strategies with results: {len(bests)} / {len(SPEC_BY_SID)}")

    today = date.today().isoformat()
    for sid, fname in SPEC_BY_SID.items():
        spec = ROOT / "docs" / "specs" / "strategies" / fname
        if not spec.exists():
            print(f"MISSING spec: {fname}")
            continue
        best = bests.get(sid)
        if best is None:
            print(f"  {sid}: no results — skipping")
            continue
        txt = spec.read_text(encoding="utf-8")
        m = re.match(r"^---\n(.*?)\n---\n(.*)$", txt, re.S)
        if not m:
            print(f"  {sid}: no frontmatter — skipping")
            continue
        fm, body = m.group(1), m.group(2)
        pf = best.get("profit_factor") or 0.0
        exp = best.get("expectancy") or 0.0
        passed = pf > 1.0 and exp > 0
        status = "candidate" if passed else "rejected"
        fm = _patch_frontmatter(fm, {
            "status": status,
            "profit_factor_bt": round(pf, 4),
            "expectancy_bt": round(exp, 6),
            "sharpe_bt": round(best.get("sharpe_bench") or 0.0, 3),
            "mdd_bt": round(best.get("mdd_bench") or 0.0, 4),
            "trades_bt": int(best.get("trades", 0)),
            "last_updated": today,
            "verdict_5y": f'"{ "passed" if passed else "rejected"}: PF={pf:.3f}, expectancy={exp*100:.4f}%/trade (scalping sweep {today})"',
        })
        section_marker = f"## Scalping reparam sweep 결과 ({today})"
        if section_marker not in body:
            body = body.rstrip() + _verdict_section(best, sweep)
        spec.write_text("---\n" + fm + "\n---\n" + body, encoding="utf-8")
        verdict = "PASS" if passed else "rej"
        print(f"  {sid}: status={status}  PF={pf:.3f} exp={exp*100:+.4f}%  ({verdict})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
