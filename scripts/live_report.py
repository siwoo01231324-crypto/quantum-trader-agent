#!/usr/bin/env python3
"""KIS Phase 2 Live Report — WAL → AC 6개 자동 검증 리포트 — Issue #105 Stage 5.3.

사용법:
    python scripts/live_report.py --wal logs/live/run_id/wal.jsonl --date 2026-04-27
    python scripts/live_report.py --wal logs/live/run_id/wal.jsonl  (오늘 날짜 기본값)
    python scripts/live_report.py --wal ... --out docs/work/active/000105-phase2-paper-live/reports/2026-04-27.md

Exit gate 검증 (Phase 2 PR 머지 게이트):
    AC1: smoke test green (별도 확인 필요)
    AC2: distinct(date) 거래일 ≥ 20
    AC3: placed_total ≥ 100 AND filled_total ≥ placed * 0.95
    AC4: tracking_error p95 < 0.5%
    AC5: kill_switch trip event count ≥ 3
    AC6: broker_ws_reconnect{broker="kis"} ≥ 1

Halt 트리거 평가 (R1~R5):
    R1: KIS API 5xx rate > 10% (15분 윈도우) → halt + alert
    R2: fill missing ≥ KIS_FILL_MISSING_HALT_THRESHOLD (default 1, Architect note #5)
    R3: tracking_error > 0.5% 5분 연속
    R4: 토큰 재발급 실패 연속 3회
    R5: 잔고 불일치 > 1%

LLM은 이 스크립트를 직접 호출하거나 라이브 결정에 개입하지 않는다 (CLAUDE.md 불변식 #6).
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger("live_report")


# ---------------------------------------------------------------------------
# WAL event helpers
# ---------------------------------------------------------------------------

def _count_events(events: list, event_type: str) -> int:
    return sum(1 for e in events if e.event_type == event_type)


def _distinct_trading_dates(events: list) -> set[date]:
    dates: set[date] = set()
    for e in events:
        if e.event_type in ("order_acked", "order_filled", "tracking_sample"):
            try:
                dt = datetime.fromisoformat(e.ts)
                dates.add(dt.astimezone(timezone.utc).date())
            except (ValueError, AttributeError):
                pass
    return dates


def _count_placed(events: list) -> int:
    return _count_events(events, "order_acked")


def _count_filled(events: list) -> int:
    return _count_events(events, "order_filled")


def _count_fill_missing(events: list) -> int:
    return _count_events(events, "fill_anomaly")


def _count_kill_switch_trips(events: list) -> int:
    return _count_events(events, "kill_switch_tripped")


def _count_ws_reconnects(events: list) -> int:
    return _count_events(events, "ws_reconnected")


def _count_token_failures(events: list) -> int:
    return _count_events(events, "token_refresh_failed")


# ---------------------------------------------------------------------------
# AC evaluation
# ---------------------------------------------------------------------------

def evaluate_ac(
    events: list,
    tracking_error_p95: Optional[float] = None,
) -> dict[str, object]:
    """Evaluate all 6 AC criteria from WAL events.

    Returns dict mapping AC label → {passed: bool, value: ..., threshold: ...}.
    """
    trading_dates = _distinct_trading_dates(events)
    placed = _count_placed(events)
    filled = _count_filled(events)
    ks_trips = _count_kill_switch_trips(events)
    ws_reconnects = _count_ws_reconnects(events)

    filled_ratio = (filled / placed) if placed > 0 else 0.0
    te_p95 = tracking_error_p95 if tracking_error_p95 is not None else float("nan")

    return {
        "AC1_smoke": {
            "label": "AC1: KIS 모의계좌 smoke test green (별도 확인)",
            "passed": None,  # must be confirmed manually
            "note": "scripts/kis_paper_smoke.py 결과 수동 확인",
        },
        "AC2_trading_days": {
            "label": "AC2: 거래일 ≥ 20",
            "passed": len(trading_dates) >= 20,
            "value": len(trading_dates),
            "threshold": 20,
        },
        "AC3_orders": {
            "label": "AC3: placed ≥ 100 AND filled ≥ placed * 0.95",
            "passed": placed >= 100 and filled_ratio >= 0.95,
            "value": f"placed={placed}, filled={filled}, ratio={filled_ratio:.2%}",
            "threshold": "placed≥100, ratio≥0.95",
        },
        "AC4_tracking_error": {
            "label": "AC4: tracking_error p95 < 0.5%",
            "passed": (not pd.isna(te_p95)) and te_p95 < 0.005,
            "value": f"{te_p95:.4%}" if not pd.isna(te_p95) else "N/A",
            "threshold": "< 0.5%",
        },
        "AC5_kill_switch": {
            "label": "AC5: kill_switch trip ≥ 3종 확인",
            "passed": ks_trips >= 3,
            "value": ks_trips,
            "threshold": 3,
        },
        "AC6_ws_reconnect": {
            "label": "AC6: WS 재연결 ≥ 1회 확인",
            "passed": ws_reconnects >= 1,
            "value": ws_reconnects,
            "threshold": 1,
        },
    }


# ---------------------------------------------------------------------------
# Halt trigger evaluation (R1~R5)
# ---------------------------------------------------------------------------

def evaluate_halt_triggers(
    events: list,
    *,
    fill_missing_threshold: int = 1,
    tracking_error_p95: Optional[float] = None,
) -> dict[str, object]:
    fill_missing = _count_fill_missing(events)
    token_failures = _count_token_failures(events)

    return {
        "R1_5xx_rate": {
            "label": "R1: KIS 5xx rate > 10% (15분 윈도우)",
            "note": "Prometheus 메트릭 기반 — WAL 에서 직접 산출 불가. 패널 2 참조",
            "value": "N/A (Prometheus)",
        },
        "R2_fill_missing": {
            "label": f"R2: 체결 누락 ≥ {fill_missing_threshold}건",
            "triggered": fill_missing >= fill_missing_threshold,
            "value": fill_missing,
            "threshold": fill_missing_threshold,
        },
        "R3_tracking_error": {
            "label": "R3: tracking_error > 0.5% 5분 연속",
            "note": "5분 window 판단은 Prometheus alert rule 기반. WAL p95 참조치만 제공",
            "value": f"{tracking_error_p95:.4%}" if tracking_error_p95 is not None and not pd.isna(tracking_error_p95) else "N/A",
        },
        "R4_token_failure": {
            "label": "R4: 토큰 재발급 실패 연속 3회",
            "triggered": token_failures >= 3,
            "value": token_failures,
            "threshold": 3,
        },
        "R5_balance_diff": {
            "label": "R5: 잔고 불일치 > 1%",
            "note": "잔고 불일치는 KIS REST 조회 vs WAL 계산 비교 — 별도 reconciler 필요",
            "value": "N/A",
        },
    }


# ---------------------------------------------------------------------------
# Tracking error from WAL
# ---------------------------------------------------------------------------

def _compute_tracking_error_from_wal(events: list) -> Optional[float]:
    """Extract tracking_sample events and compute p95 error."""
    try:
        from src.live.tracking_error import aggregate_from_wal
        from src.live.wal import WAL
        # Use in-memory computation via aggregate_from_wal
        report = aggregate_from_wal(events)
        return report.p95 if report.sample_count > 0 else None
    except Exception as err:
        logger.warning("Could not compute tracking error from WAL: %s", err)
        return None


# ---------------------------------------------------------------------------
# Markdown report renderer
# ---------------------------------------------------------------------------

def render_report_md(
    report_date: str,
    events: list,
    ac_results: dict,
    halt_results: dict,
) -> str:
    lines: list[str] = []
    lines.append(f"# Phase 2 KIS 모의계좌 운영 리포트 — {report_date}")
    lines.append("")
    lines.append("> 자동 생성: scripts/live_report.py | Issue #105")
    lines.append("")
    lines.append("## AC Exit Gate 검증")
    lines.append("")
    lines.append("| AC | 항목 | 결과 | 값 | 임계 |")
    lines.append("|-----|------|------|-----|------|")
    for key, ac in ac_results.items():
        passed = ac.get("passed")
        if passed is None:
            mark = "⬜"
        elif passed:
            mark = "PASS"
        else:
            mark = "FAIL"
        value = ac.get("value", ac.get("note", ""))
        threshold = ac.get("threshold", "")
        lines.append(f"| {key} | {ac['label']} | {mark} | {value} | {threshold} |")
    lines.append("")

    overall_passed = all(
        ac["passed"] for ac in ac_results.values()
        if ac.get("passed") is not None
    )
    lines.append(f"**PR 머지 게이트**: {'PASS (수동 AC1 확인 필요)' if overall_passed else 'FAIL — 미달 항목 있음'}")
    lines.append("")

    lines.append("## Halt 트리거 상태 (R1~R5)")
    lines.append("")
    for key, r in halt_results.items():
        triggered = r.get("triggered")
        note = r.get("note", "")
        value = r.get("value", "")
        if triggered is True:
            mark = "TRIGGERED"
        elif triggered is False:
            mark = "OK"
        else:
            mark = "N/A"
        lines.append(f"- **{key}** {r['label']}: {mark} (값: {value}) {note}")
    lines.append("")

    lines.append("## WAL 이벤트 요약")
    lines.append("")
    event_counts: dict[str, int] = {}
    for e in events:
        event_counts[e.event_type] = event_counts.get(e.event_type, 0) + 1
    for et, cnt in sorted(event_counts.items()):
        lines.append(f"- `{et}`: {cnt}건")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Issue #105 Phase 2 KIS 모의계좌 운영 리포트 생성"
    )
    parser.add_argument("--wal", type=str, required=True, help="WAL JSONL 경로")
    parser.add_argument(
        "--date", type=str,
        default=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        help="리포트 날짜 (YYYY-MM-DD, default: 오늘)",
    )
    parser.add_argument(
        "--out", type=str, default="-",
        help="출력 마크다운 경로 (- for stdout)",
    )
    parser.add_argument(
        "--log-level", type=str, default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    from src.live.wal import replay

    args = _parse_args(argv)
    logging.basicConfig(level=args.log_level)

    fill_missing_threshold = int(
        os.environ.get("KIS_FILL_MISSING_HALT_THRESHOLD", "1")
    )

    events, corruptions = replay(Path(args.wal))
    if corruptions:
        logger.warning("WAL had %d corruption(s)", len(corruptions))

    te_p95 = _compute_tracking_error_from_wal(events)
    ac_results = evaluate_ac(events, tracking_error_p95=te_p95)
    halt_results = evaluate_halt_triggers(
        events,
        fill_missing_threshold=fill_missing_threshold,
        tracking_error_p95=te_p95,
    )

    md = render_report_md(args.date, events, ac_results, halt_results)

    if args.out == "-":
        print(md)
    else:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(md, encoding="utf-8")
        logger.info("Report written to %s", out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
