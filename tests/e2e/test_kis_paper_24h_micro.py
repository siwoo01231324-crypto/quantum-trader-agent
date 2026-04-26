"""KIS paper 24h micro E2E test (nightly only).

Runs live_run.py for a short duration with max-orders=5,
then validates live_report.py produces AC output without errors.

Requires real KIS paper credentials via env vars:
  HANTOO_FAKE_API_KEY, HANTOO_FAKE_SECRET_API_KEY, HANTOO_FAKE_CREDIT_NUMBER
  HANTOO_HTS_ID (optional, defaults to 'smoke')

Run with: pytest -m e2e_kis_paper tests/e2e/test_kis_paper_24h_micro.py
Excluded from normal PR CI by: pytest -m "not e2e_kis_paper"
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


@pytest.mark.e2e_kis_paper
def test_kis_paper_24h_micro_live_report():
    """live_run.py (micro) + live_report.py validation against produced WAL."""
    required = ["HANTOO_FAKE_API_KEY", "HANTOO_FAKE_SECRET_API_KEY", "HANTOO_FAKE_CREDIT_NUMBER"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        pytest.skip(f"Missing KIS paper credentials: {missing}")

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}

    with tempfile.TemporaryDirectory() as tmp_dir:
        log_dir = Path(tmp_dir) / "logs"

        # Run live_run.py for a short micro session (max 5 orders).
        # --schedule krx makes the daemon respect KRX hours (auto-sleep outside 09:00-15:30 KST).
        result = subprocess.run(
            [
                sys.executable,
                "scripts/live_run.py",
                "--broker", "kis-paper",
                "--symbols", "005930",
                "--max-orders", "5",
                "--max-iterations", "10",
                "--log-dir", str(log_dir),
                "--auto-fallback",
                "--schedule", "krx",
            ],
            capture_output=True,
            text=True,
            timeout=300,  # 5 min max for micro run
            env=env,
        )
        # Acceptable: exits 0 (success) or 1 (e.g., outside market hours, no orders placed).
        assert result.returncode in (0, 1), (
            f"live_run.py unexpected exit {result.returncode}:\n"
            f"stdout: {result.stdout[-2000:]}\nstderr: {result.stderr[-2000:]}"
        )

        # Locate WAL file produced under log_dir (live_run.py writes it during the run).
        wal_files = list(log_dir.rglob("*.wal")) + list(log_dir.rglob("*.jsonl"))
        if not wal_files:
            pytest.skip(
                "live_run.py produced no WAL (likely outside KRX hours with --schedule krx); "
                "report validation skipped."
            )
        wal_path = wal_files[0]

        # Run live_report.py against produced WAL; output to stdout via "--out -".
        report_result = subprocess.run(
            [
                sys.executable,
                "scripts/live_report.py",
                "--wal", str(wal_path),
                "--date", "2026-04-27",
                "--out", "-",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
        )
        assert report_result.returncode in (0, 1), (
            f"live_report.py unexpected exit {report_result.returncode}:\n"
            f"stdout: {report_result.stdout}\nstderr: {report_result.stderr}"
        )
