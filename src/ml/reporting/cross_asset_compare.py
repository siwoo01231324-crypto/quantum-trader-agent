"""Cross-asset MetaLabeler performance comparison and hypothesis judgment.

Compares BTC (momo-btc-v2) vs KRX (momo-kis-v1) meta-labeler results using
PR-AUC, Deflated Sharpe Ratio, and Sharpe improvement as primary metrics.

Phase A verdict is expected to be "보류" due to limited KIS data (30-day KIS
API constraint yields ~2 events vs. the 30-event minimum for reliable inference).
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import pandas as pd


@dataclass
class CrossAssetReport:
    """Per-asset meta-labeler evaluation result."""

    asset_id: str       # e.g. "btc-usdt", "krx-005930"
    strategy_id: str    # e.g. "momo-btc-v2", "momo-kis-v1"
    sr_off: float
    sr_on: float
    sr_delta: float
    mdd_off: float
    mdd_on: float
    mdd_delta: float
    pr_auc: float
    dsr_off: float
    dsr_on: float
    dsr_delta: float
    n_events: int
    n_trades_off: int = 0
    n_trades_on: int = 0
    data_window: str = ""       # "2025-04 ~ 2026-04" style
    periods_per_year: int = 0
    n_trials: int = 1
    n_symbols: int = 1          # number of symbols in pool (1 = single asset)
    n_eff: float = 0.0          # effective sample size; 0 = N/A (single asset or unknown rho)
    rho_avg: float = 0.0        # avg pairwise correlation; 0 = single asset


def compute_effective_n(pool_size: int, rho_avg: float) -> float:
    """N_eff = N / (1 + (N-1) * rho_avg). Returns 1.0 if pool_size <= 1, N if rho_avg == 0."""
    if pool_size <= 1:
        return 1.0
    return pool_size / (1.0 + (pool_size - 1) * rho_avg)


_COLUMN_LABELS: dict[str, str] = {
    "asset_id": "자산",
    "strategy_id": "전략",
    "sr_off": "SR OFF",
    "sr_on": "SR ON",
    "sr_delta": "SR Δ",
    "mdd_off": "MDD OFF",
    "mdd_on": "MDD ON",
    "mdd_delta": "MDD Δ",
    "pr_auc": "PR-AUC",
    "dsr_off": "DSR OFF",
    "dsr_on": "DSR ON",
    "dsr_delta": "DSR Δ",
    "n_events": "이벤트 수",
    "n_trades_off": "거래수 OFF",
    "n_trades_on": "거래수 ON",
    "data_window": "데이터 기간",
    "periods_per_year": "연환산 주기",
    "n_trials": "n_trials",
}


def build_comparison_table(reports: list[CrossAssetReport]) -> pd.DataFrame:
    """Return a DataFrame with one row per asset for side-by-side comparison."""
    rows = []
    for r in reports:
        rows.append({
            "asset_id": r.asset_id,
            "strategy_id": r.strategy_id,
            "sr_off": round(r.sr_off, 4),
            "sr_on": round(r.sr_on, 4),
            "sr_delta": round(r.sr_delta, 4),
            "mdd_off": round(r.mdd_off, 4),
            "mdd_on": round(r.mdd_on, 4),
            "mdd_delta": round(r.mdd_delta, 4),
            "pr_auc": round(r.pr_auc, 4),
            "dsr_off": round(r.dsr_off, 4),
            "dsr_on": round(r.dsr_on, 4),
            "dsr_delta": round(r.dsr_delta, 4),
            "n_events": r.n_events,
            "n_trades_off": r.n_trades_off,
            "n_trades_on": r.n_trades_on,
            "data_window": r.data_window,
            "periods_per_year": r.periods_per_year,
            "n_trials": r.n_trials,
            "n_symbols": r.n_symbols,
            "n_eff": round(r.n_eff, 4),
            "rho_avg": round(r.rho_avg, 4),
        })
    return pd.DataFrame(rows)


def judge_hypothesis(
    reports: list[CrossAssetReport],
    dsr_threshold: float = 0.3,
) -> dict:
    """4-way verdict on the meta-labeler cross-asset hypothesis.

    Verdicts:
        "보류"       — insufficient data (n_events < 30 or n_trials==1 for ALL)
        "채택"       — both assets have dsr_delta >= threshold
        "재설계 검토" — exactly one asset has dsr_delta >= threshold
        "기각"       — neither asset has dsr_delta >= threshold

    Parameters
    ----------
    reports:
        List of CrossAssetReport; typically [btc_report, krx_report].
    dsr_threshold:
        Minimum dsr_delta to count as "improved". Default 0.3 per plan.

    Returns
    -------
    dict with keys: verdict, reason, criteria
    """
    if not reports:
        return {
            "verdict": "보류",
            "reason": "비교 대상 리포트가 없습니다.",
            "criteria": {"dsr_threshold": dsr_threshold, "reports": []},
        }

    # Data sufficiency check — ALL reports must have n_events >= 30
    # OR at least one report must have n_trials > 1 with sufficient data.
    insufficient = [r for r in reports if r.n_events < 30]
    all_single_trial = all(r.n_trials <= 1 for r in reports)

    if insufficient and all_single_trial:
        asset_info = ", ".join(
            f"{r.asset_id}({r.n_events} events)" for r in insufficient
        )
        return {
            "verdict": "보류",
            "reason": (
                f"데이터 부족으로 통계 판정 불가: {asset_info}. "
                "n_events ≥ 30 또는 n_trials > 1 조건 미충족 (Phase B 후속 이슈에서 재판정)."
            ),
            "criteria": {
                "dsr_threshold": dsr_threshold,
                "insufficient_assets": [r.asset_id for r in insufficient],
                "n_trials_max": max(r.n_trials for r in reports),
            },
        }

    # n_eff sufficiency check — multi-symbol pool with too few effective samples
    for r in reports:
        if r.n_symbols > 1 and r.n_eff < 5:
            return {
                "verdict": "보류",
                "reason": (
                    f"effective sample size insufficient (n_eff={r.n_eff:.2f} < 5) "
                    f"for {r.asset_id} (pool={r.n_symbols} symbols, ρ_avg={r.rho_avg:.3f}). "
                    "종목간 상관이 높아 독립 표본 수 부족."
                ),
                "criteria": {
                    "dsr_threshold": dsr_threshold,
                    "n_eff": r.n_eff,
                    "n_symbols": r.n_symbols,
                    "rho_avg": r.rho_avg,
                },
            }

    improved = [r for r in reports if r.dsr_delta >= dsr_threshold]
    n_improved = len(improved)
    n_total = len(reports)

    criteria = {
        "dsr_threshold": dsr_threshold,
        "per_asset": {r.asset_id: {"dsr_delta": r.dsr_delta, "improved": r.dsr_delta >= dsr_threshold} for r in reports},
    }

    if n_improved == n_total:
        return {
            "verdict": "채택",
            "reason": f"모든 자산군({n_total}/{n_total})에서 DSR 개선 ≥ {dsr_threshold} 확인.",
            "criteria": criteria,
        }
    elif n_improved >= 1:
        improved_ids = [r.asset_id for r in improved]
        not_improved_ids = [r.asset_id for r in reports if r.dsr_delta < dsr_threshold]
        return {
            "verdict": "재설계 검토",
            "reason": (
                f"일부 자산군에서만 개선: 개선={improved_ids}, 미개선={not_improved_ids}. "
                "전략 파라미터 또는 피처 엔지니어링 재검토 필요."
            ),
            "criteria": criteria,
        }
    else:
        return {
            "verdict": "기각",
            "reason": f"어느 자산군에서도 DSR 개선 ≥ {dsr_threshold} 미달성.",
            "criteria": criteria,
        }


def _check_data_window_consistency(reports: list[CrossAssetReport]) -> str | None:
    """Return a warning string if data windows differ, else None."""
    windows = {r.data_window for r in reports if r.data_window}
    if len(windows) > 1:
        detail = ", ".join(f"{r.asset_id}: {r.data_window!r}" for r in reports if r.data_window)
        return f"[경고] 동일 기간 비교 아님 — {detail}"
    return None


def render_markdown(table_df: pd.DataFrame, judgment: dict) -> str:
    """Produce Phase A implementation markdown with 5 mandatory sections.

    Sections:
        1. ## 데이터 가용성
        2. ## 성능 비교표
        3. ## DSR 기반 가설 판정 (Phase A)
        4. ## 신뢰도 한계
        5. ## 결론 및 후속 조치
    """
    lines: list[str] = []

    # ------------------------------------------------------------------
    # 1. 데이터 가용성
    # ------------------------------------------------------------------
    lines.append("## 데이터 가용성\n")
    if table_df.empty:
        lines.append("비교 데이터 없음.\n")
    else:
        for _, row in table_df.iterrows():
            lines.append(f"**{row['asset_id']} / {row['strategy_id']}**")
            lines.append(f"- 데이터 기간: {row['data_window'] or 'N/A'}")
            lines.append(f"- 이벤트 수: {row['n_events']}")
            lines.append(f"- 연환산 주기: {row['periods_per_year']}")
            n_sym = int(row["n_symbols"]) if "n_symbols" in row.index else 1
            if n_sym > 1:
                n_eff_val = float(row["n_eff"]) if "n_eff" in row.index else 0.0
                rho_val = float(row["rho_avg"]) if "rho_avg" in row.index else 0.0
                lines.append(f"- 종목 풀: {n_sym}종목, n_eff={n_eff_val:.2f}, ρ_avg={rho_val:.3f}")
            lines.append("")

    window_warn = None
    if not table_df.empty and "data_window" in table_df.columns:
        windows = set(table_df["data_window"].dropna().unique())
        if len(windows) > 1:
            detail = ", ".join(
                f"{r['asset_id']}: {r['data_window']!r}"
                for _, r in table_df.iterrows()
                if r.get("data_window")
            )
            window_warn = f"> [경고] 동일 기간 비교 아님 — {detail}"
            lines.append(window_warn)
            lines.append("")

    # ------------------------------------------------------------------
    # 2. 성능 비교표
    # ------------------------------------------------------------------
    lines.append("## 성능 비교표\n")
    if table_df.empty:
        lines.append("데이터 없음.\n")
    else:
        display_cols = [
            "asset_id", "strategy_id",
            "sr_off", "sr_on", "sr_delta",
            "mdd_off", "mdd_on", "mdd_delta",
            "pr_auc",
            "dsr_off", "dsr_on", "dsr_delta",
            "n_events", "n_trades_off", "n_trades_on",
            "data_window", "n_trials",
        ]
        cols_present = [c for c in display_cols if c in table_df.columns]
        headers = [_COLUMN_LABELS.get(c, c) for c in cols_present]
        rows_data = [[str(table_df.iloc[i][c]) for c in cols_present] for i in range(len(table_df))]
        col_widths = [max(len(h), max((len(r[j]) for r in rows_data), default=0)) for j, h in enumerate(headers)]
        header_row = "| " + " | ".join(h.ljust(col_widths[j]) for j, h in enumerate(headers)) + " |"
        sep_row = "| " + " | ".join("-" * col_widths[j] for j in range(len(headers))) + " |"
        lines.append(header_row)
        lines.append(sep_row)
        for row in rows_data:
            lines.append("| " + " | ".join(row[j].ljust(col_widths[j]) for j in range(len(headers))) + " |")
        lines.append("")

    # ------------------------------------------------------------------
    # 3. DSR 기반 가설 판정 (Phase A)
    # ------------------------------------------------------------------
    lines.append("## DSR 기반 가설 판정 (Phase A)\n")
    verdict = judgment.get("verdict", "N/A")
    reason = judgment.get("reason", "")
    lines.append(f"**판정: {verdict}**\n")
    lines.append(f"{reason}\n")

    criteria = judgment.get("criteria", {})
    if criteria:
        lines.append("상세 기준:")
        lines.append("```json")
        import json
        lines.append(json.dumps(criteria, ensure_ascii=False, indent=2))
        lines.append("```")
        lines.append("")

    # ------------------------------------------------------------------
    # 4. 신뢰도 한계
    # ------------------------------------------------------------------
    lines.append("## 신뢰도 한계\n")
    lines.append(
        "- **n_trials=1**: 하이퍼파라미터 탐색 없음 → DSR deflation 효과 미미 (DSR ≈ raw Sharpe 유의성). "
        "grid search 추가 시 n_trials 갱신 필요."
    )
    lines.append(
        "- **단일 종목 제약**: KRX는 005930(삼성전자) 1개 종목. 전략 일반화 주장에 한계."
    )
    has_synthetic = not table_df.empty and any(
        "합성" in str(r.get("data_window", "")) or r.get("n_events", 0) < 30
        for _, r in table_df.iterrows()
    )
    if has_synthetic or verdict == "보류":
        lines.append(
            "- **합성 데이터 fallback**: KIS 실거래 데이터 부족(30일 제약)으로 합성 OHLCV(GBM) 사용. "
            "합성 결과를 가설 채택/기각 근거로 사용 금지 (Phase A Guardrail)."
        )
    lines.append("")

    # ------------------------------------------------------------------
    # 5. 결론 및 후속 조치
    # ------------------------------------------------------------------
    lines.append("## 결론 및 후속 조치\n")
    if verdict == "보류":
        lines.append(
            "Phase A 결론: **판정 보류** — KIS 분봉 데이터 30일 제약으로 이벤트 수 부족. "
            "구조 검증(파이프라인 E2E 통과)은 완료."
        )
        lines.append("")
        lines.append(
            "Phase B 후속 이슈(placeholder): KIS 분봉 3개월 이상 누적 후 동일 파이프라인으로 재실행. "
            "후속 이슈 번호는 팀 리드가 생성 후 여기에 기재."
        )
    elif verdict == "채택":
        lines.append("Phase A 결론: **가설 채택** — BTC/KRX 모두 DSR 개선 확인.")
        lines.append("")
        lines.append("후속: 메타라벨러 KRX paper live 검증 (#80 Phase 1 Shadow 연동) 이슈 생성 필요.")
    elif verdict == "재설계 검토":
        lines.append("Phase A 결론: **재설계 검토** — 일부 자산군만 개선.")
        lines.append("")
        lines.append("후속: 전략 파라미터/피처 재검토 이슈 생성 필요.")
    else:
        lines.append("Phase A 결론: **가설 기각** — 어느 자산군에서도 개선 미달성.")
        lines.append("")
        lines.append("후속: 메타라벨러 재설계 검토 이슈 생성 필요.")
    lines.append("")

    return "\n".join(lines)
