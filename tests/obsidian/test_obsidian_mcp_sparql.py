"""SPARQL endpoint fallback 테스트.

3-layer defense (local rdflib / env endpoint / ctx endpoint) 와 안전 쿼리 필터를 검증한다.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
import responses as responses_lib

import services.obsidian_mcp.tools as mod
from services.obsidian_mcp.tools import VaultContext, sparql

FIXTURE_VAULT = Path(__file__).parent.parent / "fixtures" / "obsidian_vault"

SELECT_QUERY = """
PREFIX qta: <https://siwoo.dev/qta/ontology#>
SELECT ?s WHERE { ?s a qta:Strategy . }
"""

SPARQL_RESULTS_JSON = {
    "head": {"vars": ["s"]},
    "results": {
        "bindings": [
            {"s": {"type": "uri", "value": "https://siwoo.dev/qta/ontology#momo-btc-v2"}}
        ]
    },
}


@pytest.fixture
def local_ctx(monkeypatch):
    monkeypatch.delenv("QTA_SPARQL_ENDPOINT", raising=False)
    return VaultContext(
        vault_root=FIXTURE_VAULT.resolve(),
        allowed_paths=[],
        write_mode="dry-run",
        sparql_endpoint=None,
    )


def test_sparql_local_when_nothing_configured(local_ctx):
    pytest.importorskip("rdflib")
    result = sparql(local_ctx, SELECT_QUERY)
    assert result["ok"] is True
    assert result["source"] == "local-rdflib"


@responses_lib.activate
def test_sparql_remote_via_env(monkeypatch):
    endpoint = "http://graphdb-env:7200/repositories/qta"
    monkeypatch.setenv("QTA_SPARQL_ENDPOINT", endpoint)
    responses_lib.add(
        responses_lib.POST,
        endpoint,
        json=SPARQL_RESULTS_JSON,
        status=200,
    )
    ctx = VaultContext(
        vault_root=FIXTURE_VAULT.resolve(),
        allowed_paths=[],
        write_mode="dry-run",
        sparql_endpoint=None,
    )
    result = sparql(ctx, SELECT_QUERY)
    assert result["ok"] is True
    assert result["source"] == "remote-http"
    assert result["endpoint"] == endpoint
    assert len(result["bindings"]) == 1


@responses_lib.activate
def test_sparql_ctx_overrides_env(monkeypatch):
    env_endpoint = "http://graphdb-env:7200/repositories/qta"
    ctx_endpoint = "http://ctx:7200/repositories/qta-ctx"
    monkeypatch.setenv("QTA_SPARQL_ENDPOINT", env_endpoint)
    responses_lib.add(
        responses_lib.POST,
        ctx_endpoint,
        json=SPARQL_RESULTS_JSON,
        status=200,
    )
    ctx = VaultContext(
        vault_root=FIXTURE_VAULT.resolve(),
        allowed_paths=[],
        write_mode="dry-run",
        sparql_endpoint=ctx_endpoint,
    )
    result = sparql(ctx, SELECT_QUERY)
    assert result["ok"] is True
    assert result["source"] == "remote-http"
    assert result["endpoint"] == ctx_endpoint


def test_sparql_blocks_update(local_ctx):
    with pytest.raises(ValueError, match="SELECT|ASK|DESCRIBE|CONSTRUCT"):
        sparql(local_ctx, "INSERT DATA { <http://a> <http://b> <http://c> }")


def test_sparql_static_check_no_statements_path():
    source = inspect.getsource(mod.sparql)
    assert "/statements" not in source
