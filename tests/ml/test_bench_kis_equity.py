"""Tests for bench_metalabeler_kis.py equity-curve metrics (#154).

Sortino, periods_per_year resolution, OOF-probability based ON filter,
DSR delta + verdict, and main() smoke.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Ensure both worktree root (for `from scripts.x`) and src/ (for `from ml.x`)
# are importable. retrain_pipeline imports `from ml.scoring`, not `from src.ml.scoring`.
WORKTREE = Path(__file__).resolve().parents[2]
for _p in (str(WORKTREE), str(WORKTREE / "src")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from scripts import bench_metalabeler_kis as bench  # noqa: E402


# ---------------------------------------------------------------------------
# _sortino
# ---------------------------------------------------------------------------

class TestSortino:
    def test_sortino_with_downside_returns_positive(self) -> None:
        rets = pd.Series([0.02, -0.01, 0.03, -0.005, 0.01], dtype=float)
        assert bench._sortino(rets, periods_per_year=252) > 0.0

    def test_sortino_no_downside_returns_zero(self) -> None:
        rets = pd.Series([0.01, 0.02, 0.005], dtype=float)
        assert bench._sortino(rets, periods_per_year=252) == 0.0

    def test_sortino_empty_returns_zero(self) -> None:
        assert bench._sortino(pd.Series([], dtype=float), periods_per_year=252) == 0.0


# ---------------------------------------------------------------------------
# _resolve_periods_per_year
# ---------------------------------------------------------------------------

class TestResolvePeriodsPerYear:
    def test_explicit_overrides_interval(self) -> None:
        assert bench._resolve_periods_per_year(interval="1m", explicit=12345) == 12345

    def test_auto_1m_is_98280(self) -> None:
        assert bench._resolve_periods_per_year(interval="1m", explicit=None) == 98280

    def test_auto_15m_is_6552(self) -> None:
        assert bench._resolve_periods_per_year(interval="15m", explicit=None) == 6552

    def test_auto_unknown_falls_back_to_15m(self) -> None:
        # 미지원 interval 은 KRX_PERIODS_PER_YEAR(15m, 6552) 로 폴백
        assert bench._resolve_periods_per_year(interval="5m", explicit=None) == 6552


# ---------------------------------------------------------------------------
# _compute_verdict
# ---------------------------------------------------------------------------

class TestVerdict:
    def test_pass_when_dsr_high_and_neff_ok(self) -> None:
        v = bench._compute_verdict(dsr_on=0.5, n_eff=10.0)
        assert v.startswith("PASS")

    def test_hold_when_low_neff(self) -> None:
        v = bench._compute_verdict(dsr_on=0.99, n_eff=1.0)
        assert v.startswith("HOLD") and "n_eff" in v

    def test_hold_when_dsr_below_threshold(self) -> None:
        v = bench._compute_verdict(dsr_on=0.1, n_eff=10.0)
        assert v.startswith("HOLD")


# ---------------------------------------------------------------------------
# _oof_filter
# ---------------------------------------------------------------------------

class TestOOFFilter:
    def test_high_threshold_filters_all(self) -> None:
        oof = pd.Series([0.4, 0.45, 0.49], index=["a", "b", "c"])
        idx = bench._oof_filter(oof, threshold=0.5)
        assert list(idx) == []

    def test_low_threshold_keeps_all(self) -> None:
        oof = pd.Series([0.6, 0.7, 0.8], index=["a", "b", "c"])
        idx = bench._oof_filter(oof, threshold=0.5)
        assert sorted(idx) == ["a", "b", "c"]

    def test_mixed_threshold_keeps_only_above(self) -> None:
        oof = pd.Series([0.4, 0.6, 0.7, 0.3], index=["a", "b", "c", "d"])
        idx = bench._oof_filter(oof, threshold=0.5)
        assert sorted(idx) == ["b", "c"]


# ---------------------------------------------------------------------------
# _build_oof_series
# ---------------------------------------------------------------------------

class TestBuildOOFSeries:
    def test_uses_test_event_idx(self) -> None:
        cv_result = {
            "folds": [
                {"fold": 0, "test_event_idx": ["a", "b"], "y_prob": np.array([0.7, 0.2])},
                {"fold": 1, "test_event_idx": ["c"], "y_prob": np.array([0.9])},
                {"fold": 2, "skipped": True},
            ]
        }
        s = bench._build_oof_series(cv_result)
        assert sorted(s.index) == ["a", "b", "c"]
        assert s.loc["a"] == pytest.approx(0.7)
        assert s.loc["c"] == pytest.approx(0.9)

    def test_empty_when_all_skipped(self) -> None:
        cv_result = {"folds": [{"skipped": True}]}
        s = bench._build_oof_series(cv_result)
        assert len(s) == 0

    def test_skips_folds_without_test_event_idx(self) -> None:
        # 후방 호환 — test_event_idx 없는 fold 는 무시 (실제로는 모두 가져야 함)
        cv_result = {
            "folds": [
                {"fold": 0, "y_prob": np.array([0.5])},  # no test_event_idx
                {"fold": 1, "test_event_idx": ["a"], "y_prob": np.array([0.8])},
            ]
        }
        s = bench._build_oof_series(cv_result)
        assert list(s.index) == ["a"]


# ---------------------------------------------------------------------------
# main() smoke — synthetic GBM fallback (lake missing)
# ---------------------------------------------------------------------------

class TestMainSmoke:
    def test_main_outputs_expected_keys(self, tmp_path: Path) -> None:
        out_json = tmp_path / "bench_kis.json"
        rc = bench.main([
            "--lake-dir", str(tmp_path / "no_lake"),
            "--symbol", "005930",
            "--interval", "1m",
            "--metalabeler-threshold", "0.5",
            "--output-json", str(out_json),
        ])
        # synthetic n=2000 GBM 기본값으로 events 보통 발생 → exit 0
        # events 0건이면 exit 3; 그 경우 JSON 미생성이라 키 검사 skip
        assert rc in (0, 3)
        if rc != 0:
            pytest.skip(f"smoke exited with rc={rc} (no events) — synthetic 변동")
        data = json.loads(out_json.read_text(encoding="utf-8"))
        for key in (
            "sr_off", "sr_on",
            "sharpe_off", "sharpe_on",         # alias
            "sortino_off", "sortino_on",       # 신규
            "mdd_off", "mdd_on",
            "dsr_off", "dsr_on", "dsr_delta",  # delta 신규
            "verdict", "n_eff",                # 자동 판정
            "metalabeler_threshold",
            "periods_per_year",
            "n_events_off", "n_events_on",
        ):
            assert key in data, f"missing key: {key}"
        assert data["periods_per_year"] == 98280
        assert data["metalabeler_threshold"] == pytest.approx(0.5)
        # alias 일치
        assert data["sharpe_off"] == pytest.approx(data["sr_off"])
        assert data["sharpe_on"] == pytest.approx(data["sr_on"])
        # delta = on - off
        assert data["dsr_delta"] == pytest.approx(data["dsr_on"] - data["dsr_off"], abs=1e-9)
