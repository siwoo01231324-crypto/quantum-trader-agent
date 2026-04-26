from __future__ import annotations

import json
import pytest
from pathlib import Path

from src.ml.drift_detector import compare, DriftReport


def _manifest(mean_accuracy: float, extra: dict | None = None, tmp_path: Path = None) -> Path:
    data = {
        "cv_score": {"mean_accuracy": mean_accuracy, "std_accuracy": 0.01, "n_folds": 5},
        "git_sha": "abc1234",
        "trained_at": "2026-01-01T00:00:00Z",
    }
    if extra:
        data.update(extra)
    p = tmp_path / f"manifest_{mean_accuracy}.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _bench(prev_sharpe: float | None, new_sharpe: float, tmp_path: Path, dual: bool = True) -> Path:
    if dual and prev_sharpe is not None:
        data = {"prev": {"on": {"sharpe": prev_sharpe}}, "new": {"on": {"sharpe": new_sharpe}}}
    else:
        data = {"on": {"sharpe": new_sharpe}}
    p = tmp_path / "bench.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


# Test 1: prev=None → first-run, not triggered
def test_first_run(tmp_path):
    new = _manifest(0.60, tmp_path=tmp_path)
    report = compare(None, new)
    assert report.triggered is False
    assert report.reason == "first-run"
    assert report.accuracy_delta is None
    assert report.sharpe_delta is None


# Test 2: accuracy identical (delta=0)
def test_accuracy_no_change(tmp_path):
    prev = _manifest(0.60, tmp_path=tmp_path)
    new_path = tmp_path / "manifest_new.json"
    new_path.write_text(json.dumps({"cv_score": {"mean_accuracy": 0.60}, "git_sha": "x", "trained_at": "t"}), encoding="utf-8")
    report = compare(prev, new_path)
    assert report.triggered is False
    assert report.accuracy_delta == pytest.approx(0.0)


# Test 3: accuracy drops 5%p (0.55→0.50) → triggered
def test_accuracy_drop_5pp_triggers(tmp_path):
    prev = _manifest(0.55, tmp_path=tmp_path)
    new_path = tmp_path / "manifest_new.json"
    new_path.write_text(json.dumps({"cv_score": {"mean_accuracy": 0.50}, "git_sha": "x", "trained_at": "t"}), encoding="utf-8")
    report = compare(prev, new_path)
    assert report.triggered is True
    assert report.accuracy_delta == pytest.approx(0.05)
    assert "accuracy_delta" in report.reason


# Test 4: accuracy drops 4%p → not triggered
def test_accuracy_drop_4pp_no_trigger(tmp_path):
    prev = _manifest(0.60, tmp_path=tmp_path)
    new_path = tmp_path / "manifest_new.json"
    new_path.write_text(json.dumps({"cv_score": {"mean_accuracy": 0.56}, "git_sha": "x", "trained_at": "t"}), encoding="utf-8")
    report = compare(prev, new_path)
    assert report.triggered is False
    assert report.accuracy_delta == pytest.approx(0.04)


# Test 5: sharpe drops 0.3 (bench provided, dual format) → triggered
def test_sharpe_drop_03_triggers(tmp_path):
    prev = _manifest(0.60, tmp_path=tmp_path)
    new_path = tmp_path / "manifest_new.json"
    new_path.write_text(json.dumps({"cv_score": {"mean_accuracy": 0.60}, "git_sha": "x", "trained_at": "t"}), encoding="utf-8")
    bench = _bench(1.5, 1.2, tmp_path, dual=True)
    report = compare(prev, new_path, bench)
    assert report.triggered is True
    assert report.sharpe_delta == pytest.approx(0.3)
    assert "sharpe_delta" in report.reason


# Test 6: both accuracy and sharpe OK → not triggered
def test_both_ok_no_trigger(tmp_path):
    prev = _manifest(0.60, tmp_path=tmp_path)
    new_path = tmp_path / "manifest_new.json"
    new_path.write_text(json.dumps({"cv_score": {"mean_accuracy": 0.58}, "git_sha": "x", "trained_at": "t"}), encoding="utf-8")
    bench = _bench(1.5, 1.4, tmp_path, dual=True)
    report = compare(prev, new_path, bench)
    assert report.triggered is False
    assert report.accuracy_delta == pytest.approx(0.02)
    assert report.sharpe_delta == pytest.approx(0.1)


# Test 7: manifest missing cv_score → ValueError
def test_missing_cv_score_raises(tmp_path):
    prev = _manifest(0.60, tmp_path=tmp_path)
    bad_new = tmp_path / "bad_manifest.json"
    bad_new.write_text(json.dumps({"git_sha": "x", "trained_at": "t"}), encoding="utf-8")
    with pytest.raises(ValueError, match="invalid manifest"):
        compare(prev, bad_new)
