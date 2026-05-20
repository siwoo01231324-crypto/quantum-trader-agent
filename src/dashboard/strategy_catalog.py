"""Strategy catalog frontmatter loader (#178).

Reads docs/specs/strategies/*.md frontmatters and returns a normalized list
of dicts ready for JSON serialization in the /api/strategies endpoint.

Optional fields default to None so the JSON shape is uniform across strategies.
"""
from __future__ import annotations

import datetime as _dt
import logging
import re
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Required + optional keys per docs/schemas/note-schemas.md Strategy section.
_REQUIRED = ("id", "name", "status", "instruments", "timeframe", "owner", "created")
_OPTIONAL_LIST = ("uses_signals", "risk_rules", "tags")
_OPTIONAL_SCALAR = (
    "sharpe_bt",
    "sharpe_live",
    "mdd_bt",
    "annual_return_bt",
    "backtest_period",
    "last_updated",
    "summary_ko",
    # 출구 룰 % — 카드에 손절/익절/트레일링 표시 (2026-05-20).
    "stop_loss_pct",
    "take_profit_pct",
    "trailing_stop_pct",
)

_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---", re.DOTALL)


def _parse_frontmatter(text: str) -> dict | None:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        data = yaml.safe_load(m.group(1))
    except yaml.YAMLError as err:
        logger.warning("strategy_catalog yaml parse failed: %s", err)
        return None
    return data if isinstance(data, dict) else None


def _coerce(value):
    """YAML loads dates as datetime.date — coerce to ISO string for JSON safety."""
    if isinstance(value, (_dt.datetime, _dt.date)):
        return value.isoformat()
    return value


def _normalize(fm: dict) -> dict:
    out: dict = {}
    for k in _REQUIRED:
        out[k] = _coerce(fm.get(k))
    for k in _OPTIONAL_LIST:
        v = fm.get(k)
        out[k] = list(v) if isinstance(v, list) else []
    for k in _OPTIONAL_SCALAR:
        out[k] = _coerce(fm.get(k, None))
    return out


def load_strategy_catalog(specs_dir: Path | str) -> list[dict]:
    """Load all strategy specs as a list of normalized dicts.

    Files without `type: strategy` are skipped. Files without YAML frontmatter
    are skipped. `.ai.md` is skipped automatically (no `type: strategy`).

    Returns empty list if specs_dir does not exist.
    """
    p = Path(specs_dir)
    if not p.is_dir():
        return []

    items: list[dict] = []
    for md in sorted(p.glob("*.md")):
        if md.name.startswith("."):
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError as err:
            logger.warning("strategy_catalog read failed for %s: %s", md, err)
            continue
        fm = _parse_frontmatter(text)
        if fm is None:
            continue
        if fm.get("type") != "strategy":
            continue
        items.append(_normalize(fm))
    return items


# Pattern for `- id: <id>` entries in production.yaml — handles indentation
# and optional quotes. Commented detection re-anchors with leading `#`.
_PROD_ACTIVE_RE = re.compile(r"^\s*-\s+id:\s*['\"]?([a-zA-Z0-9_-]+)['\"]?\s*$")
_PROD_COMMENT_RE = re.compile(r"^\s*#\s*-\s+id:\s*['\"]?([a-zA-Z0-9_-]+)['\"]?\s*$")


def load_production_status(yaml_path: Path | str) -> dict[str, str]:
    """Return {strategy_id: "active" | "commented"} from production.yaml.

    Active = uncommented `- id: <id>` under strategies. Commented = `# - id: <id>`.
    Missing-from-yaml strategies are absent from the returned dict (caller should
    treat as "absent" / draft-only). File missing → empty dict (no crash).
    """
    p = Path(yaml_path)
    if not p.is_file():
        return {}
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as err:
        logger.warning("production.yaml read failed: %s", err)
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _PROD_ACTIVE_RE.match(line)
        if m:
            out[m.group(1)] = "active"
            continue
        m = _PROD_COMMENT_RE.match(line)
        if m:
            # Don't overwrite an active entry with a stray commented duplicate.
            out.setdefault(m.group(1), "commented")
    return out
