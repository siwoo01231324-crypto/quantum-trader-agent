"""KIS paper smoke E2E test (nightly only).

Requires real KIS paper credentials via env vars:
  HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY, HANTOO_FAKE_CREDIT_NUMBER
  HANTOO_HTS_ID (optional, smoke uses REST only — defaults to 'smoke')

Run with: pytest -m e2e_kis_paper tests/e2e/test_kis_paper_smoke.py
Excluded from normal PR CI by: pytest -m "not e2e_kis_paper"
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest


@pytest.mark.e2e_kis_paper
def test_kis_paper_smoke_exit_zero():
    """scripts/kis_paper_smoke.py exits 0 with real KIS paper credentials."""
    required = ["HANTOO_FAKE_API_KEY", "HANTOO_FAKE_SECRET_API_KEY", "HANTOO_FAKE_CREDIT_NUMBER"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing KIS paper credentials: {missing}")

    result = subprocess.run(
        [sys.executable, "scripts/kis_paper_smoke.py"],
        capture_output=True,
        text=True,
        timeout=120,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    assert result.returncode == 0, (
        f"smoke test failed (exit {result.returncode}):\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "Smoke test PASSED" in result.stdout
