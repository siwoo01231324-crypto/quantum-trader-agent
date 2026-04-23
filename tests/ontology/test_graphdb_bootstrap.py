"""Tests for graphdb_bootstrap and graphdb_client."""
import responses
import pytest
from pathlib import Path

from scripts.graphdb_bootstrap import bootstrap
from scripts.graphdb_client import wait_for_ready


@responses.activate
def test_wait_for_ready_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", status=503)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", status=503)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", json=[], status=200)
    wait_for_ready("http://gdb:7200", timeout=10)


@responses.activate
def test_bootstrap_creates_repo_and_loads_tbox(tmp_path):
    tbox = tmp_path / "trading.ttl"
    tbox.write_text(
        '@prefix qta: <http://qta/#> . qta:Strategy a <http://www.w3.org/2000/01/rdf-schema#Class> .'
    )
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", json=[], status=200)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories/qta", status=404)
    responses.add(responses.POST, "http://gdb:7200/rest/repositories", status=201)
    responses.add(responses.POST, "http://gdb:7200/repositories/qta/statements", status=204)
    responses.add(responses.GET, "http://gdb:7200/repositories/qta/size", body="1", status=200)
    assert bootstrap(endpoint="http://gdb:7200", repo="qta", tbox=tbox) == 0


@responses.activate
def test_bootstrap_idempotent_when_repo_exists(tmp_path):
    tbox = tmp_path / "trading.ttl"
    tbox.write_text('@prefix qta: <http://qta/#> .')
    responses.add(
        responses.GET, "http://gdb:7200/rest/repositories",
        json=[{"id": "qta"}], status=200
    )
    responses.add(responses.GET, "http://gdb:7200/rest/repositories/qta", status=200)
    responses.add(responses.POST, "http://gdb:7200/repositories/qta/statements", status=204)
    responses.add(responses.GET, "http://gdb:7200/repositories/qta/size", body="8", status=200)
    bootstrap(endpoint="http://gdb:7200", repo="qta", tbox=tbox)
    assert not any(
        c.request.method == "POST" and c.request.url.endswith("/rest/repositories")
        for c in responses.calls
    )
