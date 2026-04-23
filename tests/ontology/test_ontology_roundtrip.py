import re
import pytest
from pathlib import Path
from rdflib import Graph
from rdflib.compare import graph_diff, to_isomorphic

TTL = Path("docs/ontology/trading.ttl")
GOLDEN = Path("tests/fixtures/ontology/trading_after_protege.ttl")


def _used_prefixes(ttl_path: Path) -> set[str]:
    """Prefixes that are actually referenced by at least one triple."""
    raw = ttl_path.read_text("utf-8")
    declared = set(re.findall(r"@prefix\s+(\w+):", raw))
    used = set()
    for p in declared:
        if re.search(rf"(?<!@prefix ){p}:\w", raw):
            used.add(p)
    return used


@pytest.mark.xfail(not GOLDEN.exists(), reason="awaiting human-generated Protégé fixture — see docs/onboarding/protege-setup.md")
def test_protege_roundtrip_preserves_trading_triples():
    """Every triple in trading.ttl must survive Protégé round-trip.
    Protégé may ADD benign RDF vocabulary (e.g. xsd:date a rdfs:Datatype);
    additive normalization is acceptable. Loss is not."""
    g_src = to_isomorphic(Graph().parse(TTL))
    g_after = to_isomorphic(Graph().parse(GOLDEN))
    _, only_src, _ = graph_diff(g_src, g_after)
    assert len(only_src) == 0, f"Protégé dropped {len(only_src)} triples: {list(only_src)[:3]}"


@pytest.mark.xfail(not GOLDEN.exists(), reason="awaiting Protégé fixture")
def test_used_prefixes_preserved():
    """Prefixes referenced by triples in trading.ttl must survive round-trip.
    Protégé may elide unused prefix declarations — lossless normalization."""
    used_before = _used_prefixes(TTL)
    raw_after = GOLDEN.read_text("utf-8")
    for p in used_before:
        assert re.search(rf"@prefix\s+{p}:", raw_after), f"Protégé dropped used @prefix {p}"


@pytest.mark.xfail(not GOLDEN.exists(), reason="awaiting Protégé fixture")
def test_comments_preserved():
    count = lambda p: len(re.findall(r'rdfs:comment\s+"', p.read_text("utf-8")))
    assert count(GOLDEN) >= count(TTL), "Protégé dropped rdfs:comment lines"
