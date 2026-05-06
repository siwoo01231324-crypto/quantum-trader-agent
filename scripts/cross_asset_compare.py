#!/usr/bin/env python3
"""Cross-asset MetaLabeler comparison script.

Loads BTC (momo-btc-v2) and KIS (momo-kis-v1) manifests, builds a comparison
table, judges the hypothesis, and writes Phase A results to
docs/work/active/000097-kis-cross-validation/02_implementation.md.

Usage:
    python scripts/cross_asset_compare.py
    python scripts/cross_asset_compare.py --btc-model-dir models/momo-btc-v2
    python scripts/cross_asset_compare.py --kis-model-dir models/momo-kis-v1
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

# Windows cp949 환경에서도 유니코드 출력 허용
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from ml.reporting.cross_asset_compare import (
    CrossAssetReport,
    build_comparison_table,
    judge_hypothesis,
    render_markdown,
)

OUTPUT_PATH = REPO_ROOT / "docs/work/active/000097-kis-cross-validation/02_implementation.md"

FRONTMATTER = """\
---
type: work-done
id: 02_implementation
name: "#97 Phase A 교차 비교 결과"
status: active
---

# #97 메타라벨러 × KIS 교차 검증 — Phase A 구현 결과

> 생성: `scripts/cross_asset_compare.py` 자동 생성.
> Phase A 목적: 구조 검증 + 정직한 판정 기록. 합성 결과를 가설 채택/기각 근거로 사용 금지.

"""


def _load_manifest(model_dir: Path) -> dict | None:
    """Load the most recently modified manifest.json under model_dir."""
    candidates = sorted(model_dir.glob("*/manifest.json"), key=lambda p: p.stat().st_mtime)
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text(encoding="utf-8"))


def _report_from_manifest(
    manifest: dict,
    asset_id: str,
    strategy_id: str,
    periods_per_year: int,
) -> CrossAssetReport:
    """Build a CrossAssetReport from a saved manifest dict."""
    cv = manifest.get("cv_score", {})
    lc = manifest.get("label_config", {})
    tw = manifest.get("training_window", {})

    mean_acc = cv.get("mean_accuracy", 0.0)

    # Proxy: use mean_accuracy as a coarse PR-AUC proxy when no dedicated report
    pr_auc_proxy = mean_acc

    n_events = tw.get("n_events", 0)
    start = tw.get("start", "")
    end = tw.get("end", "")
    data_window = f"{str(start)[:7]} ~ {str(end)[:7]}" if start and end else "N/A"

    # Minimal Sharpe / DSR values from manifest (proxy; real values require equity curve)
    # OFF = baseline (always-pass), ON = meta-labeler ON
    # We derive sr_delta and dsr_delta from cv accuracy as a rough proxy:
    #   sr_on = mean_accuracy * 2 - 1  (accuracy-to-SR proxy)
    #   sr_off = 0.5 * 2 - 1 = 0.0
    sr_off = 0.0
    sr_on = max(0.0, mean_acc * 2 - 1)
    sr_delta = sr_on - sr_off

    # DSR proxy — same scale, single trial deflation ≈ 0
    dsr_off = 0.0
    dsr_on = sr_on  # n_trials=1 → DSR ≈ raw SR
    dsr_delta = dsr_on - dsr_off

    return CrossAssetReport(
        asset_id=asset_id,
        strategy_id=strategy_id,
        sr_off=sr_off,
        sr_on=sr_on,
        sr_delta=sr_delta,
        mdd_off=0.0,
        mdd_on=0.0,
        mdd_delta=0.0,
        pr_auc=pr_auc_proxy,
        dsr_off=dsr_off,
        dsr_on=dsr_on,
        dsr_delta=dsr_delta,
        n_events=n_events,
        data_window=data_window,
        periods_per_year=periods_per_year,
        n_trials=1,
        n_symbols=manifest.get("n_symbols", 1),
        rho_avg=manifest.get("rho_avg", 0.0),
        n_eff=manifest.get("n_eff", 0.0),
    )


def _synthetic_pending_report(asset_id: str, strategy_id: str, periods_per_year: int, note: str) -> CrossAssetReport:
    """Return a zero-filled report flagged as pending (manifest not found)."""
    return CrossAssetReport(
        asset_id=asset_id,
        strategy_id=strategy_id,
        sr_off=0.0,
        sr_on=0.0,
        sr_delta=0.0,
        mdd_off=0.0,
        mdd_on=0.0,
        mdd_delta=0.0,
        pr_auc=0.0,
        dsr_off=0.0,
        dsr_on=0.0,
        dsr_delta=0.0,
        n_events=0,
        data_window=f"N/A ({note})",
        periods_per_year=periods_per_year,
        n_trials=1,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cross-asset MetaLabeler comparison")
    parser.add_argument("--btc-model-dir", default=str(REPO_ROOT / "models/momo-btc-v2"))
    parser.add_argument(
        "--kis-model-dir",
        default=str(REPO_ROOT / "models/momo-kis-v1-pooled"),
        help="KIS model dir. Default matches bench_metalabeler_kis.py --manifest-dir output (#155).",
    )
    parser.add_argument("--dsr-threshold", type=float, default=0.3)
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    args = parser.parse_args(argv)

    btc_model_dir = Path(args.btc_model_dir)
    kis_model_dir = Path(args.kis_model_dir)
    output_path = Path(args.output)

    reports: list[CrossAssetReport] = []
    notes: list[str] = []

    # BTC manifest
    btc_manifest = _load_manifest(btc_model_dir) if btc_model_dir.exists() else None
    if btc_manifest is None:
        note = "BTC manifest not found locally"
        print(f"[warn] {note} - using pending placeholder for btc-usdt")
        notes.append(note)
        reports.append(_synthetic_pending_report("btc-usdt", "momo-btc-v2", 35040, note))
    else:
        reports.append(_report_from_manifest(btc_manifest, "btc-usdt", "momo-btc-v2", 35040))
        print(f"[info] Loaded BTC manifest from {btc_model_dir}")

    # KIS manifest
    kis_manifest = _load_manifest(kis_model_dir) if kis_model_dir.exists() else None
    if kis_manifest is None:
        note = "KIS manifest not found locally"
        print(f"[warn] {note} - using pending placeholder for krx-005930")
        notes.append(note)
        reports.append(_synthetic_pending_report("krx-005930", "momo-kis-v1", 6552, note))
    else:
        reports.append(_report_from_manifest(kis_manifest, "krx-005930", "momo-kis-v1", 6552))
        print(f"[info] Loaded KIS manifest from {kis_model_dir}")

    table_df = build_comparison_table(reports)
    judgment = judge_hypothesis(reports, dsr_threshold=args.dsr_threshold)
    body_md = render_markdown(table_df, judgment)

    verdict = judgment["verdict"]
    print(f"\n[result] 판정: {verdict}")
    print(f"[result] 사유: {judgment['reason']}")

    # Write output file
    output_path.parent.mkdir(parents=True, exist_ok=True)
    content = FRONTMATTER + body_md
    if notes:
        content += "\n\n---\n\n> **생성 노트**: " + "; ".join(notes) + "\n"
    output_path.write_text(content, encoding="utf-8")
    print(f"\n[done] 결과 기록: {output_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
