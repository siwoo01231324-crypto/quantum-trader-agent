"""Unit tests for src/ml/reporting/cross_asset_compare.py."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ml.reporting.cross_asset_compare import (
    CrossAssetReport,
    build_comparison_table,
    judge_hypothesis,
    render_markdown,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_report(
    asset_id: str = "btc-usdt",
    strategy_id: str = "momo-btc-v2",
    sr_off: float = 0.5,
    sr_on: float = 0.9,
    sr_delta: float = 0.4,
    dsr_off: float = 0.4,
    dsr_on: float = 0.75,
    dsr_delta: float = 0.35,
    pr_auc: float = 0.65,
    n_events: int = 50,
    n_trials: int = 1,
    data_window: str = "2025-04 ~ 2026-04",
    periods_per_year: int = 35040,
) -> CrossAssetReport:
    return CrossAssetReport(
        asset_id=asset_id,
        strategy_id=strategy_id,
        sr_off=sr_off,
        sr_on=sr_on,
        sr_delta=sr_delta,
        mdd_off=0.1,
        mdd_on=0.08,
        mdd_delta=-0.02,
        pr_auc=pr_auc,
        dsr_off=dsr_off,
        dsr_on=dsr_on,
        dsr_delta=dsr_delta,
        n_events=n_events,
        n_trials=n_trials,
        data_window=data_window,
        periods_per_year=periods_per_year,
    )


# ---------------------------------------------------------------------------
# judge_hypothesis — 4 verdict scenarios
# ---------------------------------------------------------------------------

class TestJudgeHypothesis:
    def test_verdict_accept_both_improved(self):
        """Both assets meet DSR threshold → 채택."""
        btc = _make_report(asset_id="btc-usdt", dsr_delta=0.35, n_events=50)
        krx = _make_report(asset_id="krx-005930", strategy_id="momo-kis-v1", dsr_delta=0.40, n_events=50)
        result = judge_hypothesis([btc, krx], dsr_threshold=0.3)
        assert result["verdict"] == "채택"
        assert "DSR" in result["reason"] or "자산군" in result["reason"]

    def test_verdict_redesign_one_improved(self):
        """Exactly one asset meets threshold → 재설계 검토."""
        btc = _make_report(asset_id="btc-usdt", dsr_delta=0.40, n_events=50)
        krx = _make_report(asset_id="krx-005930", strategy_id="momo-kis-v1", dsr_delta=0.10, n_events=50)
        result = judge_hypothesis([btc, krx], dsr_threshold=0.3)
        assert result["verdict"] == "재설계 검토"
        assert "btc-usdt" in result["reason"] or "개선" in result["reason"]

    def test_verdict_reject_none_improved(self):
        """No asset meets threshold → 기각."""
        btc = _make_report(asset_id="btc-usdt", dsr_delta=0.05, n_events=50)
        krx = _make_report(asset_id="krx-005930", strategy_id="momo-kis-v1", dsr_delta=0.10, n_events=50)
        result = judge_hypothesis([btc, krx], dsr_threshold=0.3)
        assert result["verdict"] == "기각"

    def test_verdict_pending_insufficient_data(self):
        """n_events < 30 and n_trials == 1 → 보류."""
        btc = _make_report(asset_id="btc-usdt", dsr_delta=0.40, n_events=95, n_trials=1)
        krx = _make_report(
            asset_id="krx-005930", strategy_id="momo-kis-v1",
            dsr_delta=0.40, n_events=2, n_trials=1,
        )
        result = judge_hypothesis([btc, krx], dsr_threshold=0.3)
        assert result["verdict"] == "보류"
        assert "데이터 부족" in result["reason"] or "n_events" in result["reason"]

    def test_verdict_pending_all_insufficient(self):
        """All assets have n_events < 30 → 보류 regardless of dsr_delta."""
        btc = _make_report(asset_id="btc-usdt", dsr_delta=0.99, n_events=5, n_trials=1)
        krx = _make_report(asset_id="krx-005930", dsr_delta=0.99, n_events=3, n_trials=1)
        result = judge_hypothesis([btc, krx], dsr_threshold=0.3)
        assert result["verdict"] == "보류"

    def test_criteria_returned(self):
        """criteria dict is always present."""
        btc = _make_report(n_events=50)
        result = judge_hypothesis([btc])
        assert "criteria" in result
        assert "dsr_threshold" in result["criteria"]


# ---------------------------------------------------------------------------
# build_comparison_table — column structure
# ---------------------------------------------------------------------------

class TestBuildComparisonTable:
    def test_column_structure(self):
        """DataFrame must contain all expected columns."""
        btc = _make_report(asset_id="btc-usdt")
        krx = _make_report(asset_id="krx-005930", strategy_id="momo-kis-v1")
        df = build_comparison_table([btc, krx])

        required_cols = {
            "asset_id", "strategy_id",
            "sr_off", "sr_on", "sr_delta",
            "mdd_off", "mdd_on", "mdd_delta",
            "pr_auc",
            "dsr_off", "dsr_on", "dsr_delta",
            "n_events", "n_trades_off", "n_trades_on",
            "data_window", "periods_per_year", "n_trials",
        }
        assert required_cols.issubset(set(df.columns)), (
            f"Missing columns: {required_cols - set(df.columns)}"
        )

    def test_row_count(self):
        reports = [_make_report(asset_id=f"asset-{i}") for i in range(3)]
        df = build_comparison_table(reports)
        assert len(df) == 3

    def test_empty_list(self):
        df = build_comparison_table([])
        assert df.empty


# ---------------------------------------------------------------------------
# render_markdown — section header presence
# ---------------------------------------------------------------------------

class TestRenderMarkdown:
    def _get_table_and_judgment(self, verdict: str = "보류"):
        btc = _make_report(asset_id="btc-usdt", n_events=95, n_trials=1, dsr_delta=0.35)
        krx = _make_report(asset_id="krx-005930", n_events=2, n_trials=1, dsr_delta=0.35)
        df = build_comparison_table([btc, krx])
        judgment = judge_hypothesis([btc, krx])
        return df, judgment

    def test_five_mandatory_sections(self):
        """All 5 required section headers must appear in rendered markdown."""
        df, judgment = self._get_table_and_judgment()
        md = render_markdown(df, judgment)

        required_headers = [
            "## 데이터 가용성",
            "## 성능 비교표",
            "## DSR 기반 가설 판정 (Phase A)",
            "## 신뢰도 한계",
            "## 결론 및 후속 조치",
        ]
        for header in required_headers:
            assert header in md, f"Missing section: {header!r}"

    def test_verdict_in_output(self):
        """The judgment verdict string must appear in the rendered markdown."""
        btc = _make_report(asset_id="btc-usdt", dsr_delta=0.40, n_events=50)
        krx = _make_report(asset_id="krx-005930", dsr_delta=0.35, n_events=50)
        df = build_comparison_table([btc, krx])
        judgment = judge_hypothesis([btc, krx])
        md = render_markdown(df, judgment)
        assert judgment["verdict"] in md

    def test_empty_df_renders_without_error(self):
        """render_markdown must not crash with an empty DataFrame."""
        judgment = {"verdict": "보류", "reason": "no data", "criteria": {}}
        md = render_markdown(build_comparison_table([]), judgment)
        assert "## 데이터 가용성" in md


# ---------------------------------------------------------------------------
# Data window consistency warning
# ---------------------------------------------------------------------------

class TestMultiSymbolScenario:
    def test_multi_symbol_pool_info_in_markdown(self):
        """When n_symbols > 1, render_markdown shows pool info row."""
        from ml.reporting.cross_asset_compare import compute_effective_n
        pool_size = 10
        rho = 0.2
        n_eff = compute_effective_n(pool_size, rho)
        report = CrossAssetReport(
            asset_id="krx-pool-10",
            strategy_id="momo-kis-v1-pooled",
            sr_off=0.3, sr_on=0.7, sr_delta=0.4,
            mdd_off=0.1, mdd_on=0.08, mdd_delta=-0.02,
            pr_auc=0.62,
            dsr_off=0.3, dsr_on=0.65, dsr_delta=0.35,
            n_events=300,
            n_trials=1,
            data_window="2026-03 ~ 2026-04",
            periods_per_year=98280,
            n_symbols=pool_size,
            n_eff=n_eff,
            rho_avg=rho,
        )
        df = build_comparison_table([report])
        judgment = judge_hypothesis([report], dsr_threshold=0.3)
        md = render_markdown(df, judgment)
        assert "종목 풀" in md
        assert "10종목" in md
        assert "ρ_avg" in md

    def test_single_symbol_no_pool_info_in_markdown(self):
        """When n_symbols == 1 (default), no pool info row in markdown."""
        report = CrossAssetReport(
            asset_id="btc-usdt",
            strategy_id="momo-btc-v2",
            sr_off=0.5, sr_on=0.9, sr_delta=0.4,
            mdd_off=0.1, mdd_on=0.08, mdd_delta=-0.02,
            pr_auc=0.65,
            dsr_off=0.4, dsr_on=0.75, dsr_delta=0.35,
            n_events=50,
            n_trials=1,
            data_window="2025-04 ~ 2026-04",
            periods_per_year=35040,
        )
        df = build_comparison_table([report])
        judgment = judge_hypothesis([report], dsr_threshold=0.3)
        md = render_markdown(df, judgment)
        assert "종목 풀" not in md


class TestDataWindowWarning:
    def test_different_windows_warning_in_markdown(self):
        """When data_window values differ, a warning must appear in rendered markdown."""
        btc = _make_report(asset_id="btc-usdt", data_window="2025-04 ~ 2026-04", n_events=50)
        krx = _make_report(
            asset_id="krx-005930", strategy_id="momo-kis-v1",
            data_window="2026-03 ~ 2026-04", n_events=50,
        )
        df = build_comparison_table([btc, krx])
        judgment = judge_hypothesis([btc, krx])
        md = render_markdown(df, judgment)
        assert "동일 기간 비교 아님" in md

    def test_same_window_no_warning(self):
        """When data_window values are identical, no window-mismatch warning."""
        btc = _make_report(asset_id="btc-usdt", data_window="2025-04 ~ 2026-04", n_events=50)
        krx = _make_report(
            asset_id="krx-005930", strategy_id="momo-kis-v1",
            data_window="2025-04 ~ 2026-04", n_events=50,
        )
        df = build_comparison_table([btc, krx])
        judgment = judge_hypothesis([btc, krx])
        md = render_markdown(df, judgment)
        assert "동일 기간 비교 아님" not in md
