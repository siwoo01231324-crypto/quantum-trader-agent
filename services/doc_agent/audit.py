"""감사 로그 — 생성기 실행 시 `docs/work/agent-runs/` 에 실행 기록 남기기."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def log_run(generator: str, payload: dict[str, Any], *, root: Path) -> Path:
    """실행 로그 1개 기록. 실패해도 호출자에 예외를 전파하지 않는다."""
    try:
        ts = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d-%H%M%S")
        log_dir = root / "docs" / "work" / "agent-runs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / f"{ts}-{generator}.log"
        record = {"ts": ts, "generator": generator, **payload}
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — 로깅 실패는 무시
        return Path()
