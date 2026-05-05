"""Tests for strategy catalog frontmatter loader (#178).

Loads docs/specs/strategies/*.md frontmatters into list of dicts that the
dashboard /api/strategies endpoint can serialize directly.
"""
from __future__ import annotations

from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def specs_dir(tmp_path: Path) -> Path:
    """Synthetic docs/specs/strategies tree with 2 fake strategies."""
    d = tmp_path / "docs" / "specs" / "strategies"
    d.mkdir(parents=True)

    (d / "alpha-test.md").write_text(
        """---
type: strategy
id: alpha-test
name: Alpha Test Strategy
status: backtest
instruments: [BTCUSDT]
timeframe: 15m
uses_signals: [rsi-divergence]
risk_rules: [max-drawdown-5pct]
owner: tester
created: 2026-01-01
sharpe_bt: 1.23
sharpe_live: null
mdd_bt: -0.18
annual_return_bt: 0.24
backtest_period: 2020-01-01/2024-12-31
last_updated: 2026-05-05
tags: [momentum]
---

# Alpha Test Strategy
Body content.
""",
        encoding="utf-8",
    )

    (d / "beta-test.md").write_text(
        """---
type: strategy
id: beta-test
name: Beta Test
status: draft
instruments: [005930]
timeframe: 1d
owner: tester
created: 2026-02-01
---

# Beta Test
Minimal frontmatter.
""",
        encoding="utf-8",
    )

    # .ai.md should be ignored (not a strategy)
    (d / ".ai.md").write_text("# .ai.md (not a strategy)", encoding="utf-8")

    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_load_strategy_catalog_returns_list(specs_dir: Path):
    from src.dashboard.strategy_catalog import load_strategy_catalog
    items = load_strategy_catalog(specs_dir)
    assert isinstance(items, list)
    assert len(items) == 2  # .ai.md excluded


def test_load_strategy_catalog_skips_ai_md(specs_dir: Path):
    from src.dashboard.strategy_catalog import load_strategy_catalog
    items = load_strategy_catalog(specs_dir)
    ids = {it["id"] for it in items}
    assert ".ai" not in ids and ".ai.md" not in ids


def test_load_strategy_catalog_full_frontmatter(specs_dir: Path):
    from src.dashboard.strategy_catalog import load_strategy_catalog
    items = load_strategy_catalog(specs_dir)
    alpha = next(it for it in items if it["id"] == "alpha-test")
    assert alpha["name"] == "Alpha Test Strategy"
    assert alpha["status"] == "backtest"
    assert alpha["instruments"] == ["BTCUSDT"]
    assert alpha["timeframe"] == "15m"
    assert alpha["sharpe_bt"] == 1.23
    assert alpha["mdd_bt"] == -0.18
    assert alpha["annual_return_bt"] == 0.24
    assert alpha["last_updated"] == "2026-05-05"


def test_load_strategy_catalog_partial_frontmatter_defaults_none(specs_dir: Path):
    """Strategies without optional fields should have those keys present and None."""
    from src.dashboard.strategy_catalog import load_strategy_catalog
    items = load_strategy_catalog(specs_dir)
    beta = next(it for it in items if it["id"] == "beta-test")
    assert beta["name"] == "Beta Test"
    # Optional metric fields default to None for consistent JSON shape
    assert beta["sharpe_bt"] is None
    assert beta["mdd_bt"] is None
    assert beta["annual_return_bt"] is None
    assert beta["last_updated"] is None
    assert beta["uses_signals"] == []  # missing list → empty list
    assert beta["risk_rules"] == []


def test_load_strategy_catalog_missing_dir_returns_empty(tmp_path: Path):
    from src.dashboard.strategy_catalog import load_strategy_catalog
    items = load_strategy_catalog(tmp_path / "nonexistent")
    assert items == []


def test_load_strategy_catalog_skips_non_strategy_type(tmp_path: Path):
    """File without `type: strategy` (or wrong type) should be skipped."""
    d = tmp_path / "docs" / "specs" / "strategies"
    d.mkdir(parents=True)
    (d / "valid.md").write_text(
        "---\ntype: strategy\nid: valid\nname: V\nstatus: live\n"
        "instruments: [X]\ntimeframe: 1d\nowner: t\ncreated: 2026-01-01\n---\n",
        encoding="utf-8",
    )
    (d / "wrong-type.md").write_text(
        "---\ntype: signal\nid: wrong-type\nname: W\n---\n", encoding="utf-8",
    )
    (d / "no-frontmatter.md").write_text("# just markdown body", encoding="utf-8")

    from src.dashboard.strategy_catalog import load_strategy_catalog
    items = load_strategy_catalog(d)
    assert len(items) == 1
    assert items[0]["id"] == "valid"


def test_load_strategy_catalog_real_repo():
    """Smoke test against actual docs/specs/strategies/ — must yield ≥5 strategies."""
    from src.dashboard.strategy_catalog import load_strategy_catalog
    real_dir = Path(__file__).resolve().parent.parent / "docs" / "specs" / "strategies"
    items = load_strategy_catalog(real_dir)
    ids = {it["id"] for it in items}
    expected = {"momo-btc-v2", "momo-kis-v1", "momo-vol-filtered",
                "breakout-donchian", "meanrev-pairs"}
    assert expected.issubset(ids), f"missing strategies: {expected - ids}"
    # All real specs should now have last_updated populated post-#178 boost
    for it in items:
        if it["id"] in expected:
            assert it["last_updated"] == "2026-05-05", f"{it['id']} last_updated not boosted"
