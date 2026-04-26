from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ACCURACY_DRIFT_THRESHOLD = 0.05
SHARPE_DRIFT_THRESHOLD = 0.3


@dataclass
class DriftReport:
    triggered: bool
    reason: str
    accuracy_delta: float | None
    sharpe_delta: float | None


def compare(
    prev_manifest_path: Path | None,
    new_manifest_path: Path,
    bench_path: Path | None = None,
    *,
    accuracy_threshold: float = ACCURACY_DRIFT_THRESHOLD,
    sharpe_threshold: float = SHARPE_DRIFT_THRESHOLD,
) -> DriftReport:
    if prev_manifest_path is None:
        return DriftReport(triggered=False, reason="first-run", accuracy_delta=None, sharpe_delta=None)

    new_manifest = json.loads(new_manifest_path.read_text(encoding="utf-8"))
    prev_manifest = json.loads(prev_manifest_path.read_text(encoding="utf-8"))

    try:
        new_acc = new_manifest["cv_score"]["mean_accuracy"]
    except (KeyError, TypeError):
        raise ValueError(f"invalid manifest: cv_score.mean_accuracy missing in {new_manifest_path}")

    try:
        prev_acc = prev_manifest["cv_score"]["mean_accuracy"]
    except (KeyError, TypeError):
        raise ValueError(f"invalid manifest: cv_score.mean_accuracy missing in {prev_manifest_path}")

    accuracy_delta = float(prev_acc - new_acc)

    sharpe_delta: float | None = None
    if bench_path is not None:
        bench = json.loads(bench_path.read_text(encoding="utf-8"))
        if "prev" in bench and "new" in bench:
            try:
                prev_sharpe = bench["prev"]["on"]["sharpe"]
                new_sharpe = bench["new"]["on"]["sharpe"]
                sharpe_delta = float(prev_sharpe - new_sharpe)
            except (KeyError, TypeError):
                sharpe_delta = None
        else:
            # single-run format: compare against prev_manifest.latest_bench_sharpe
            try:
                new_sharpe = bench["on"]["sharpe"]
                prev_sharpe = prev_manifest.get("latest_bench_sharpe")
                if prev_sharpe is not None:
                    sharpe_delta = float(prev_sharpe - new_sharpe)
            except (KeyError, TypeError):
                sharpe_delta = None

    triggers: list[str] = []
    if accuracy_delta >= accuracy_threshold:
        triggers.append(f"accuracy_delta={accuracy_delta:.4f} >= {accuracy_threshold}")
    if sharpe_delta is not None and sharpe_delta >= sharpe_threshold:
        triggers.append(f"sharpe_delta={sharpe_delta:.4f} >= {sharpe_threshold}")

    if triggers:
        return DriftReport(
            triggered=True,
            reason="; ".join(triggers),
            accuracy_delta=accuracy_delta,
            sharpe_delta=sharpe_delta,
        )

    reason_parts: list[str] = [f"accuracy_delta={accuracy_delta:.4f}"]
    if sharpe_delta is not None:
        reason_parts.append(f"sharpe_delta={sharpe_delta:.4f}")
    return DriftReport(
        triggered=False,
        reason="no drift (" + ", ".join(reason_parts) + ")",
        accuracy_delta=accuracy_delta,
        sharpe_delta=sharpe_delta,
    )
