"""Weekly MetaLabeler retrain orchestrator (Issue #95).

Orchestrates a single strategy retrain cycle:
  1. Load OHLCV (real lake or --synthetic)
  2. train_and_save → models/<strategy>/<YYYYMMDD>/
  3. (--bench) bench_metalabeler_btc.py subprocess → bench_result.json
  4. drift_detector.compare → DriftReport
  5. drift triggered → alerts.notify('warn', ...)
  6. latest.json updated (only when drift=False)
  7. models/<strategy>/retrain_logs/<YYYYMMDD>.md written

Exit codes: 0=ok, 2=drift_detected, 1=fatal_failure
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

WORKTREE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WORKTREE / "src"))

from ml.drift_detector import compare as drift_compare  # noqa: E402
from ml.retrain_pipeline import load_ohlcv_from_lake, train_and_save  # noqa: E402
from observability.alerts import notify  # noqa: E402



# ---------------------------------------------------------------------------
# Synthetic data helper (mirrors bench_metalabeler_btc._make_synthetic_ohlcv)
# ---------------------------------------------------------------------------

def _make_synthetic_ohlcv(n: int = 5000, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100.0 + np.cumsum(rng.standard_normal(n) * 0.3)
    closes = np.maximum(closes, 1.0)
    opens = closes * (1 + rng.standard_normal(n) * 0.001)
    highs = np.maximum(closes, opens) * (1 + np.abs(rng.standard_normal(n) * 0.002))
    lows = np.minimum(closes, opens) * (1 - np.abs(rng.standard_normal(n) * 0.002))
    volumes = np.abs(rng.standard_normal(n) * 1000 + 5000)
    index = pd.date_range("2024-01-01", periods=n, freq="15min")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
        index=index,
    )


# ---------------------------------------------------------------------------
# Log writer
# ---------------------------------------------------------------------------

def _write_retrain_log(
    log_dir: Path,
    date_str: str,
    strategy_id: str,
    output_dir: Path | None,
    cv_report: dict | None,
    drift_report,
    bench_result: dict | None,
    failure_log: str | None,
) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{date_str}.md"

    lines = [
        f"---",
        f"date: {date_str}",
        f"strategy: {strategy_id}",
        f"---",
        f"",
        f"# 재학습 로그 — {date_str} ({strategy_id})",
        f"",
    ]

    if cv_report is not None:
        lines += [
            "## CV 결과",
            "",
            "| 지표 | 값 |",
            "| --- | --- |",
            f"| mean_accuracy | {cv_report.get('mean_accuracy', 'N/A'):.4f} |",
            f"| std_accuracy | {cv_report.get('std_accuracy', 'N/A'):.4f} |",
            f"| n_folds | {cv_report.get('n_folds', 'N/A')} |",
            "",
        ]

    if bench_result is not None:
        on = bench_result.get("on", {})
        off = bench_result.get("off", {})
        lines += [
            "## 벤치마크 결과",
            "",
            "| 지표 | on | off |",
            "| --- | --- | --- |",
            f"| sharpe | {on.get('sharpe', 'N/A')} | {off.get('sharpe', 'N/A')} |",
            f"| win_rate | {on.get('win_rate', 'N/A')} | {off.get('win_rate', 'N/A')} |",
            "",
        ]

    if drift_report is not None:
        lines += [
            "## 드리프트 감지",
            "",
            f"- triggered: {drift_report.triggered}",
            f"- reason: {drift_report.reason}",
            f"- accuracy_delta: {drift_report.accuracy_delta}",
            f"- sharpe_delta: {drift_report.sharpe_delta}",
            "",
        ]

    if output_dir is not None:
        lines += [
            "## 아티팩트",
            "",
            f"- output_dir: `{output_dir}`",
            "",
        ]

    if failure_log:
        lines += [
            "## 실행 환경 실패 로그",
            "",
            "```",
            failure_log.strip(),
            "```",
            "",
            "> retry 필요",
            "",
        ]

    log_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[log] retrain log written: {log_path}")
    return log_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Monthly MetaLabeler retrain orchestrator")
    parser.add_argument("--strategy-id", default="momo-btc-v2")
    parser.add_argument("--lake-dir", type=Path, default=WORKTREE / "lake")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--interval", default="15m")
    parser.add_argument("--prev-manifest", type=Path, default=None,
                        help="Path to previous manifest.json for drift comparison")
    parser.add_argument("--output-root", type=Path, default=WORKTREE / "models",
                        help="Root dir; artifacts saved to <output-root>/<strategy-id>/<YYYYMMDD>/")
    parser.add_argument("--train-frac", type=float, default=0.7)
    parser.add_argument("--holding-bars", type=int, default=24)
    parser.add_argument("--tp-sigma", type=float, default=2.0)
    parser.add_argument("--sl-sigma", type=float, default=1.5)
    parser.add_argument("--costs-bps", type=float, default=4.0)
    parser.add_argument("--bench", action="store_true",
                        help="Run bench_metalabeler_btc.py after training")
    parser.add_argument("--synthetic", action="store_true",
                        help="Use synthetic OHLCV data (for testing/e2e without real lake)")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y%m%d")
    failure_log_parts: list[str] = []
    post_train_failure = False
    output_dir: Path | None = None
    cv_report: dict | None = None
    drift_report = None
    bench_result: dict | None = None
    log_dir = args.output_root / args.strategy_id / "retrain_logs"

    # --- Step 1: Load OHLCV ---
    try:
        if args.synthetic:
            print("[data] using synthetic OHLCV (--synthetic flag)")
            ohlcv = _make_synthetic_ohlcv()
        else:
            ohlcv = load_ohlcv_from_lake(args.lake_dir, args.symbol, args.interval)
    except Exception:
        tb = traceback.format_exc()
        failure_log_parts.append(f"[STEP 1 - data load FAILED]\n{tb}")
        print(tb, file=sys.stderr)
        _write_retrain_log(log_dir, date_str, args.strategy_id, None, None, None, None,
                           "\n\n".join(failure_log_parts))
        return 1

    # --- Step 2: train_and_save ---
    try:
        candidate_dir = args.output_root / args.strategy_id / date_str
        if candidate_dir.exists():
            # Fallback to timestamp suffix to avoid collision
            ts_suffix = now.strftime("%Y%m%d-%H%M%S")
            candidate_dir = args.output_root / args.strategy_id / ts_suffix

        artifact, cv_report = train_and_save(
            strategy_id=args.strategy_id,
            ohlcv=ohlcv,
            output_dir=candidate_dir,
            train_frac=args.train_frac,
            holding_bars=args.holding_bars,
            tp_sigma=args.tp_sigma,
            sl_sigma=args.sl_sigma,
            costs_bps=args.costs_bps,
        )
        output_dir = artifact.output_dir

        # AC gate
        if cv_report["mean_accuracy"] < 0.55:
            print(f"[WARN] CV mean accuracy {cv_report['mean_accuracy']:.4f} < 0.55")
            failure_log_parts.append(
                f"[AC GATE] CV mean accuracy {cv_report['mean_accuracy']:.4f} < 0.55 — deployment not recommended"
            )

    except Exception:
        tb = traceback.format_exc()
        failure_log_parts.append(f"[STEP 2 - train_and_save FAILED]\n{tb}")
        print(tb, file=sys.stderr)
        _write_retrain_log(log_dir, date_str, args.strategy_id, None, cv_report, None, None,
                           "\n\n".join(failure_log_parts))
        return 1

    # --- Step 3: (optional) bench ---
    bench_path: Path | None = None
    if args.bench:
        try:
            bench_out = output_dir / "bench_result.json"
            bench_script = WORKTREE / "scripts/bench_metalabeler_btc.py"
            bench_cmd = [sys.executable, str(bench_script),
                         "--model-path", str(output_dir),
                         "--output-md", str(output_dir / "bench_report.md")]
            if not args.synthetic:
                bench_cmd += ["--data-path", str(args.lake_dir)]
                # Limit bench to last 6 months for CI tractability (~9K bars
                # vs 35K). Drift detection still meaningful: recent regime
                # change is more relevant than aggregate over 13 months.
                from datetime import timedelta as _td
                bench_start = (datetime.now(timezone.utc) - _td(days=180)).strftime("%Y-%m-%d")
                bench_cmd += ["--data-start", bench_start]
            # Stream bench output to our stdout in real-time so CI shows
            # progress (capture_output blocks until subprocess finishes,
            # making slow backtests look like silent hangs).
            print("[bench] starting subprocess (streaming output)...", flush=True)
            proc = subprocess.Popen(
                bench_cmd,
                cwd=str(WORKTREE),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            captured: list[str] = []
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    print(f"  [bench] {line}", end="", flush=True)
                    captured.append(line)
                proc.wait(timeout=1500)  # 25 min hard cap
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                post_train_failure = True
                failure_log_parts.append("[STEP 3 - bench TIMEOUT (>25 min)]")
                print("[bench] TIMEOUT — killed", flush=True)

            bench_stdout = "".join(captured)
            # Wrap into a "result" shape so existing code below stays simple.
            class _R:
                returncode = proc.returncode if proc.returncode is not None else 1
                stdout = bench_stdout
                stderr = ""
            result = _R()
            # bench exit codes: 0=AC4 PASS, 2=AC4 FAIL (verdict, expected),
            #   1=environment failure (real failure to surface).
            print(f"[bench] exit={result.returncode}", flush=True)
            if result.returncode == 1:
                post_train_failure = True
                failure_log_parts.append(
                    f"[STEP 3 - bench environment FAILED (exit 1)]\n"
                    f"stdout: {result.stdout}"
                )
            else:
                # Parse bench stdout for "ON Sharpe: <val>" so drift_detector can compare.
                # Format produced by bench_metalabeler_btc.py:
                #   "  OFF Sharpe: -2.16  |  ON Sharpe: -1.13  |  delta: +1.04"
                on_match = re.search(r"ON Sharpe:\s+([-\d.]+)", result.stdout)
                off_match = re.search(r"OFF Sharpe:\s+([-\d.]+)", result.stdout)
                if on_match:
                    bench_result = {"on": {"sharpe": float(on_match.group(1))}}
                    if off_match:
                        bench_result["off"] = {"sharpe": float(off_match.group(1))}
                    bench_out.write_text(
                        json.dumps(bench_result, indent=2), encoding="utf-8"
                    )
                    bench_path = bench_out
                print(f"[bench] completed, exit={result.returncode}")
        except Exception:
            post_train_failure = True
            tb = traceback.format_exc()
            failure_log_parts.append(f"[STEP 3 - bench EXCEPTION]\n{tb}")
            print(f"[warn] bench failed: {tb}", file=sys.stderr)

    # --- Step 4: drift detection ---
    try:
        drift_report = drift_compare(
            prev_manifest_path=args.prev_manifest,
            new_manifest_path=output_dir / "manifest.json",
            bench_path=bench_path,
        )
        print(f"[drift] triggered={drift_report.triggered}, reason={drift_report.reason}")
    except Exception:
        post_train_failure = True
        tb = traceback.format_exc()
        failure_log_parts.append(f"[STEP 4 - drift detection FAILED]\n{tb}")
        print(tb, file=sys.stderr)

    # --- Step 5: alert on drift ---
    if drift_report is not None and drift_report.triggered:
        try:
            notify(
                "warn",
                f"metalabeler drift: {args.strategy_id}",
                body=drift_report.reason,
                fields={
                    "accuracy_delta": str(drift_report.accuracy_delta),
                    "sharpe_delta": str(drift_report.sharpe_delta),
                    "output_dir": str(output_dir),
                },
            )
        except Exception:
            tb = traceback.format_exc()
            print(f"[warn] alert failed: {tb}", file=sys.stderr)

    # --- Step 6: update latest.json (only when drift=False) ---
    if drift_report is None or not drift_report.triggered:
        try:
            latest_path = args.output_root / args.strategy_id / "latest.json"
            latest_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                git_sha = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=str(WORKTREE), capture_output=True, text=True, check=True,
                ).stdout.strip()
            except Exception:
                git_sha = "unknown"

            latest = {
                "version": date_str,
                "trained_at": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
                "git_sha": git_sha,
                "cv_mean_accuracy": cv_report["mean_accuracy"] if cv_report else None,
                "drift_triggered": False,
                "approved": True,
                "artifact_dir": str(output_dir),
            }
            latest_path.write_text(json.dumps(latest, indent=2), encoding="utf-8")
            print(f"[latest] updated: {latest_path}")
        except Exception:
            post_train_failure = True
            tb = traceback.format_exc()
            failure_log_parts.append(f"[STEP 6 - latest.json update FAILED]\n{tb}")
            print(tb, file=sys.stderr)

    # --- Step 7: write retrain log ---
    _write_retrain_log(
        log_dir=log_dir,
        date_str=date_str,
        strategy_id=args.strategy_id,
        output_dir=output_dir,
        cv_report=cv_report,
        drift_report=drift_report,
        bench_result=bench_result,
        failure_log="\n\n".join(failure_log_parts) if failure_log_parts else None,
    )

    # --- Step 8: always-on completion notification ---
    try:
        if output_dir is None or post_train_failure:
            level = "critical"
            title_prefix = "❌ 학습 실패"
        elif drift_report is not None and drift_report.triggered:
            level = "warn"
            title_prefix = "⚠️ 드리프트 감지"
        else:
            level = "info"
            title_prefix = "✅ 학습 완료"

        cv_acc = cv_report.get("mean_accuracy") if cv_report else None
        drift_status = drift_report.reason if drift_report else "n/a"
        notify(
            level,
            f"{title_prefix}: {args.strategy_id} ({date_str})",
            body=(
                f"CV accuracy: {cv_acc:.4f}\n" if cv_acc is not None else ""
            ) + f"Drift: {drift_status}",
            fields={
                "output_dir": str(output_dir) if output_dir else "n/a",
                "post_train_failure": str(post_train_failure),
            },
        )
    except Exception:
        print(f"[warn] completion notification failed:\n{traceback.format_exc()}", file=sys.stderr)

    # --- Exit code ---
    if output_dir is None or post_train_failure:
        return 1
    if drift_report is not None and drift_report.triggered:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
