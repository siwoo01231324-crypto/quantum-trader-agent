"""ontology_sync.py 기본 단위 테스트."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytest.importorskip("frontmatter")
pytest.importorskip("rdflib")

import ontology_sync  # noqa: E402
from rdflib import Graph, Namespace, RDF  # noqa: E402

QTA = Namespace("https://siwoo.dev/qta/ontology#")
INST = Namespace("https://siwoo.dev/qta/instance/")


def _write_strategy(tmp_path: Path) -> Path:
    docs = tmp_path / "docs" / "specs" / "strategies"
    docs.mkdir(parents=True)
    f = docs / "momo-btc-v2.md"
    f.write_text(
        """---
type: strategy
id: momo-btc-v2
name: BTC Momentum v2
status: live
instruments: [BTCUSDT]
timeframe: 15m
uses_signals: [rsi-divergence]
risk_rules: [max-drawdown-5pct]
owner: siwoo
created: 2026-04-14
---

Body [[rsi-divergence]].
""",
        encoding="utf-8",
    )
    return tmp_path / "docs"


def test_build_graph_parses_strategy_frontmatter(tmp_path):
    docs_dir = _write_strategy(tmp_path)
    g, processed = ontology_sync.build_graph(docs_dir=docs_dir)

    assert "strategy:momo-btc-v2" in processed
    subj = INST["momo-btc-v2"]
    assert (subj, RDF.type, QTA.Strategy) in g
    # status=live → LiveStrategy 서브클래스 타입 부여
    assert (subj, RDF.type, QTA.LiveStrategy) in g
    # usesSignal 링크 생성
    assert (subj, QTA.usesSignal, INST["rsi-divergence"]) in g
    # appliesRule 링크 생성
    assert (subj, QTA.appliesRule, INST["max-drawdown-5pct"]) in g
    # tradesOn 링크 생성
    assert (subj, QTA.tradesOn, INST["BTCUSDT"]) in g


def test_serialize_produces_turtle(tmp_path):
    docs_dir = _write_strategy(tmp_path)
    g, _ = ontology_sync.build_graph(docs_dir=docs_dir)
    ttl = g.serialize(format="turtle")
    assert "qta:Strategy" in ttl
    assert "momo-btc-v2" in ttl
    # round-trip 파싱 가능
    g2 = Graph()
    g2.parse(data=ttl, format="turtle")
    assert len(g2) >= 1
