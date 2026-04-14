"""doc_agent — LLM 에이전트가 구조화된 초안 노트(.draft.md)를 생성한다.

공개 API:
    generate_backtest_draft(bt_result_json: dict) -> Path
    generate_incident_draft(event: dict) -> Path
    generate_postmortem_draft(incident_id: str) -> Path
"""
from .generators import (
    generate_backtest_draft,
    generate_incident_draft,
    generate_postmortem_draft,
)

__all__ = [
    "generate_backtest_draft",
    "generate_incident_draft",
    "generate_postmortem_draft",
]
