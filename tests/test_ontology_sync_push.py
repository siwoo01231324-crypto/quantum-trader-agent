import pytest
import responses
from pathlib import Path
from scripts.ontology_sync import push_to_graphdb


@responses.activate
def test_push_issues_clear_and_insert(tmp_path):
    tbox = tmp_path / "trading.ttl"
    tbox.write_text('@prefix qta: <http://qta/#> . qta:Strategy a <http://www.w3.org/2000/01/rdf-schema#Class> .', encoding="utf-8")
    abox = tmp_path / "instances.ttl"
    abox.write_text('@prefix inst: <http://qta/inst#> . @prefix qta: <http://qta/#> . inst:s1 a qta:Strategy .', encoding="utf-8")
    responses.add(responses.POST,
        "http://gdb:7200/repositories/qta/statements",
        status=204)
    push_to_graphdb("http://gdb:7200", "qta", tbox, abox)
    assert len(responses.calls) == 1
    body = responses.calls[0].request.body
    if isinstance(body, bytes):
        body = body.decode("utf-8")
    assert "CLEAR DEFAULT" in body.upper()
    assert "INSERT DATA" in body.upper()


def test_push_fails_if_abox_missing(tmp_path):
    tbox = tmp_path / "trading.ttl"
    tbox.write_text("@prefix qta: <http://qta/#> .", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="A-Box"):
        push_to_graphdb("http://gdb:7200", "qta", tbox, tmp_path / "nope.ttl")


def test_push_fails_if_tbox_missing(tmp_path):
    abox = tmp_path / "instances.ttl"
    abox.write_text("@prefix qta: <http://qta/#> .", encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="T-Box"):
        push_to_graphdb("http://gdb:7200", "qta", tmp_path / "nope.ttl", abox)
