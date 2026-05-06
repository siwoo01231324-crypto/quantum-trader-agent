"""Smoke tests for scripts/bench_iranyi_full_stack.py.

Tests that --smoke mode:
  - completes within 30 seconds
  - writes output JSON with required fields
  - variant_registry_sha256 is deterministic
  - cv_split_hash, git_commit, python_version, cv_params, gate_params all present

No real data required.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from scripts.bench_iranyi_full_stack import (
    VARIANT_REGISTRY,
    variant_registry_sha256,
    main,
)

REQUIRED_TOP_LEVEL_FIELDS = {
    "schema_version",
    "issue",
    "git_commit",
    "python_version",
    "variant_registry_sha256",
    "cv_params",
    "cv_split_hash",
    "gate_params",
    "variants",
    "timestamp",
}


# ---------------------------------------------------------------------------
# Registry integrity tests
# ---------------------------------------------------------------------------


def test_variant_registry_has_d0_to_d9():
    assert set(VARIANT_REGISTRY.keys()) == {"D0", "D1", "D2", "D3", "D4", "D5", "D6", "D7", "D8", "D9"}


def test_variant_registry_sha256_is_deterministic():
    sha1 = variant_registry_sha256()
    sha2 = variant_registry_sha256()
    assert sha1 == sha2
    assert len(sha1) == 64  # hex SHA-256


def test_variant_registry_sha256_is_64_hex():
    sha = variant_registry_sha256()
    assert all(c in "0123456789abcdef" for c in sha)


def test_variant_registry_d0_is_4h():
    assert VARIANT_REGISTRY["D0"]["tf"] == "4h"


def test_variant_registry_d6_to_d9_are_5m():
    for vid in ("D6", "D7", "D8", "D9"):
        assert VARIANT_REGISTRY[vid]["tf"] == "5m", f"{vid} should be 5m"


# ---------------------------------------------------------------------------
# Smoke run tests (≤30 seconds)
# ---------------------------------------------------------------------------


@pytest.fixture()
def smoke_output(tmp_path) -> dict:
    """Run --smoke and return parsed JSON output."""
    t0 = time.monotonic()
    rc = main(["--smoke", "--variants", "D0", "--output-dir", str(tmp_path)])
    elapsed = time.monotonic() - t0
    assert rc == 0, f"--smoke exited with {rc}"
    assert elapsed < 30.0, f"--smoke took {elapsed:.1f}s (> 30s limit)"
    out_file = tmp_path / "bench_output_full_stack.json"
    assert out_file.exists(), "Output JSON not written"
    return json.loads(out_file.read_text(encoding="utf-8"))


def test_smoke_required_fields(smoke_output):
    missing = REQUIRED_TOP_LEVEL_FIELDS - set(smoke_output.keys())
    assert not missing, f"Missing required fields: {missing}"


def test_smoke_variant_registry_sha256_present(smoke_output):
    assert "variant_registry_sha256" in smoke_output
    assert len(smoke_output["variant_registry_sha256"]) == 64


def test_smoke_cv_params_complete(smoke_output):
    cp = smoke_output["cv_params"]
    assert "n_splits" in cp
    assert "embargo_frac" in cp
    assert "cscv_n_S" in cp


def test_smoke_gate_params_complete(smoke_output):
    gp = smoke_output["gate_params"]
    assert "DSR_min" in gp
    assert "PBO_max" in gp
    assert "OOS_MDD_max" in gp
    assert "monthly_hit_rate_min" in gp
    assert "min_n_trades_per_variant" in gp


def test_smoke_cv_split_hash_present(smoke_output):
    assert "cv_split_hash" in smoke_output
    h = smoke_output["cv_split_hash"]
    assert isinstance(h, str) and len(h) > 0


def test_smoke_git_commit_present(smoke_output):
    assert "git_commit" in smoke_output
    assert isinstance(smoke_output["git_commit"], str)


def test_smoke_python_version_present(smoke_output):
    assert "python_version" in smoke_output
    v = smoke_output["python_version"]
    assert v.startswith("3.")


def test_smoke_variants_list_nonempty(smoke_output):
    assert isinstance(smoke_output["variants"], list)
    assert len(smoke_output["variants"]) >= 1


def test_smoke_d0_variant_present(smoke_output):
    variant_ids = [v["variant_id"] for v in smoke_output["variants"]]
    assert "D0" in variant_ids


def test_smoke_variant_has_required_keys(smoke_output):
    required = {"variant_id", "status", "n_trades"}
    for v in smoke_output["variants"]:
        missing = required - set(v.keys())
        assert not missing, f"Variant {v.get('variant_id')} missing keys: {missing}"


# ---------------------------------------------------------------------------
# Multi-variant smoke run
# ---------------------------------------------------------------------------


def test_smoke_all_4h_variants(tmp_path):
    t0 = time.monotonic()
    rc = main(["--smoke", "--variants", "D0,D1,D2,D3,D4,D5", "--output-dir", str(tmp_path)])
    elapsed = time.monotonic() - t0
    assert rc == 0
    assert elapsed < 30.0, f"Multi-variant smoke took {elapsed:.1f}s"
    out = json.loads((tmp_path / "bench_output_full_stack.json").read_text())
    variant_ids = {v["variant_id"] for v in out["variants"]}
    assert {"D0", "D1", "D2", "D3", "D4", "D5"} == variant_ids
