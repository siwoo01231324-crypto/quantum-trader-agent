"""Unit tests for dashboard patch notes page (2026-05-26 v0.6.0)."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.dashboard.patch_notes import (
    load_patch_notes,
    render_patch_notes_page,
)


class TestLoadPatchNotes:
    def test_default_path_loads_repo_index(self):
        # Repo docs/patch-notes/index.yaml must parse and contain at least
        # the v0.6.0 release that ships with this change.
        versions, error = load_patch_notes()
        assert error is None
        assert len(versions) >= 1
        first = versions[0]
        assert first["version"] == "v0.6.0"
        assert first["date"] == "2026-05-26"
        assert "items" in first and len(first["items"]) > 0
        assert "tags" in first and len(first["tags"]) > 0

    def test_missing_file_yields_empty(self, tmp_path: Path):
        versions, error = load_patch_notes(tmp_path / "nope.yaml")
        assert versions == []
        assert error is None

    def test_malformed_yaml_returns_error(self, tmp_path: Path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("versions:\n  - {oops:\n", encoding="utf-8")
        versions, error = load_patch_notes(bad)
        assert versions == []
        assert error is not None
        assert error  # truthy error string

    def test_versions_must_be_list(self, tmp_path: Path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("versions: hello\n", encoding="utf-8")
        versions, error = load_patch_notes(bad)
        assert versions == []
        assert error is not None
        assert "list" in error.lower()


class TestRenderPatchNotesPage:
    def test_renders_entry_content(self):
        versions = [{
            "version": "v9.9.9",
            "date": "2099-01-01",
            "title": "Test entry",
            "tags": ["feature", "safety"],
            "summary": "Synthetic test summary.",
            "items": ["Item A", "Item B"],
            "refs": ["docs/foo.md"],
        }]
        html_out = render_patch_notes_page(versions=versions, error=None)
        assert "v9.9.9" in html_out
        assert "Test entry" in html_out
        assert "Item A" in html_out
        assert "Item B" in html_out
        assert "docs/foo.md" in html_out
        assert "feature" in html_out
        assert "safety" in html_out

    def test_empty_list_shows_placeholder(self):
        html_out = render_patch_notes_page(versions=[], error=None)
        assert "patch-notes" in html_out or "패치노트가 아직 없습니다" in html_out

    def test_error_shows_warning(self):
        html_out = render_patch_notes_page(versions=[], error="boom")
        assert "boom" in html_out
        assert "pn-err" in html_out

    def test_escapes_html_in_entries(self):
        versions = [{
            "version": "v1.0.0",
            "date": "2026-01-01",
            "title": "<script>alert(1)</script>",
            "tags": ["fix"],
            "summary": "Has <html> entities & ampersand.",
            "items": ['<img onerror="x">'],
            "refs": [],
        }]
        html_out = render_patch_notes_page(versions=versions, error=None)
        # Raw tags must not appear executable in output.
        assert "<script>alert(1)</script>" not in html_out
        assert "&lt;script&gt;" in html_out
        assert "&amp;" in html_out


class TestRouteRegistration:
    def test_patch_notes_route_registered_on_app(self):
        from src.dashboard.app import DashboardState, create_app
        state = DashboardState()
        app = create_app(state)
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/patch-notes" in paths
