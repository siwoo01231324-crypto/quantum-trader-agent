"""Regression — `tags` list with YAML date values broke /api/strategies.

YAML auto-parses unquoted dates like ``2026-05-23`` into ``datetime.date``.
``_normalize`` used to do ``list(v)`` on optional list fields, preserving
the raw date objects. ``starlette.responses.JSONResponse`` then choked:

    TypeError: Object of type date is not JSON serializable
    when serializing list item N → dict item 'tags' → list item M

2026-05-30: ``/api/strategies`` returned 500 on the live dashboard while
the dynamic-universe pipeline was being diagnosed.  Symptom was cosmetic
(the strategies card failed to render) but it actively obstructed the
incident postmortem.

Fix coerces each element with ``_coerce`` (datetime → ISO string).
"""
from __future__ import annotations

import datetime as _dt
import json
from pathlib import Path

from src.dashboard.strategy_catalog import _normalize, load_strategy_catalog


def test_tags_with_date_is_json_serializable():
    """YAML date in `tags` list must end up as ISO string, not date object."""
    fm = {
        "id": "demo",
        "name": "Demo",
        "status": "active",
        "instruments": ["BTCUSDT"],
        "timeframe": "1h",
        "owner": "test",
        "created": _dt.date(2026, 5, 23),
        "tags": ["foo", _dt.date(2026, 5, 23), "bar"],
    }
    item = _normalize(fm)
    # Must JSON serialize without raising.
    json.dumps(item)
    assert item["tags"] == ["foo", "2026-05-23", "bar"], (
        f"date objects in optional list fields must be ISO-coerced "
        f"(else JSONResponse 500), got tags={item['tags']!r}"
    )


def test_full_catalog_is_json_serializable():
    """Loading the real specs dir must yield a JSON-encodable list — no
    stray date object anywhere in optional list fields (tags, uses_signals,
    risk_rules).
    """
    specs = Path("docs/specs/strategies")
    if not specs.is_dir():
        # Not running from repo root — skip rather than fail.
        return
    items = load_strategy_catalog(specs)
    # JSON dump end-to-end — replicates JSONResponse path.
    json.dumps(items)
