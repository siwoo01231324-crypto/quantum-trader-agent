#!/usr/bin/env python3
"""
ontology_sync.py — Obsidian 프론트매터 → RDF 인스턴스 동기화

docs/ 하위의 모든 `.md` 파일을 읽어 프론트매터의 `type` 에 따라
`docs/ontology/instances.ttl` 에 RDF 인스턴스를 생성한다.

사용법:
  python scripts/ontology_sync.py --check    # 변경 감지만 (파일 쓰지 않음)
  python scripts/ontology_sync.py --write    # instances.ttl 재생성

의존성:
  pip install python-frontmatter rdflib PyYAML
"""
from __future__ import annotations

import argparse
import io
import os
import sys
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

try:
    import frontmatter  # type: ignore
    from rdflib import Graph, Literal, Namespace, RDF, URIRef
    from rdflib.namespace import RDFS, XSD
except ImportError as e:
    print(f"[ontology_sync] 의존성 누락: {e}")
    print("  설치: pip install python-frontmatter rdflib PyYAML")
    sys.exit(2)

sys.path.insert(0, str(Path(__file__).parent))
from graphdb_client import sparql_update  # noqa: E402


QTA = Namespace("https://siwoo.dev/qta/ontology#")
INST = Namespace("https://siwoo.dev/qta/instance/")

TYPE_TO_CLASS = {
    "strategy": QTA.Strategy,
    "signal": QTA.Signal,
    "risk-rule": QTA.RiskRule,
    "instrument": QTA.Instrument,
    "backtest": QTA.Backtest,
    "incident": QTA.Incident,
    "postmortem": QTA.PostMortem,
    "ml-model": QTA.MLModel,
}

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
OUT_TTL = REPO_ROOT / "docs" / "ontology" / "instances.ttl"
TRADING_TTL = REPO_ROOT / "docs" / "ontology" / "trading.ttl"


def _iri(id_: str) -> URIRef:
    return INST[id_]


def _add_common(g: Graph, subj: URIRef, fm: dict, rdf_class: URIRef) -> None:
    g.add((subj, RDF.type, rdf_class))
    if "name" in fm and fm["name"] is not None:
        g.add((subj, RDFS.label, Literal(str(fm["name"]))))


def _add_strategy(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.Strategy)
    if fm.get("status"):
        g.add((subj, QTA.status, Literal(str(fm["status"]))))
        if str(fm["status"]) == "live":
            g.add((subj, RDF.type, QTA.LiveStrategy))
    if fm.get("timeframe"):
        g.add((subj, QTA.timeframe, Literal(str(fm["timeframe"]))))
    if fm.get("sharpe_bt") is not None:
        try:
            g.add((subj, QTA.sharpeBt, Literal(float(fm["sharpe_bt"]), datatype=XSD.decimal)))
        except (TypeError, ValueError):
            pass
    for sig in fm.get("uses_signals") or []:
        g.add((subj, QTA.usesSignal, _iri(str(sig))))
    for rr in fm.get("risk_rules") or []:
        g.add((subj, QTA.appliesRule, _iri(str(rr))))
    for inst in fm.get("instruments") or []:
        g.add((subj, QTA.tradesOn, _iri(str(inst))))


def _add_signal(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.Signal)
    if fm.get("lookback") is not None:
        g.add((subj, QTA.lookback, Literal(int(fm["lookback"]), datatype=XSD.integer)))
    if fm.get("source_model"):
        g.add((subj, QTA.derivedFromModel, _iri(str(fm["source_model"]))))


def _add_risk_rule(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.RiskRule)
    if fm.get("severity"):
        sev = str(fm["severity"])
        g.add((subj, QTA.severity, Literal(sev)))
        if sev == "critical":
            g.add((subj, RDF.type, QTA.CriticalRule))
    if fm.get("scope"):
        g.add((subj, QTA.scope, Literal(str(fm["scope"]))))
    if fm.get("threshold") is not None:
        try:
            g.add((subj, QTA.threshold, Literal(float(fm["threshold"]), datatype=XSD.decimal)))
        except (TypeError, ValueError):
            pass


def _add_instrument(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.Instrument)
    if fm.get("asset_class"):
        g.add((subj, QTA.assetClass, Literal(str(fm["asset_class"]))))
    if fm.get("venue"):
        g.add((subj, QTA.venue, Literal(str(fm["venue"]))))


def _add_backtest(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.Backtest)
    if fm.get("strategy"):
        g.add((subj, QTA.backtestOf, _iri(str(fm["strategy"]))))
    metrics = fm.get("metrics") or {}
    if isinstance(metrics, dict) and metrics.get("sharpe") is not None:
        try:
            g.add((subj, QTA.sharpeRatio, Literal(float(metrics["sharpe"]), datatype=XSD.decimal)))
        except (TypeError, ValueError):
            pass
    period = fm.get("period")
    if isinstance(period, list) and len(period) == 2:
        start, end = period
        if start is not None:
            g.add((subj, QTA.periodStart, Literal(str(start), datatype=XSD.date)))
        if end is not None:
            g.add((subj, QTA.periodEnd, Literal(str(end), datatype=XSD.date)))


def _add_incident(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.Incident)
    if fm.get("occurred"):
        g.add((subj, QTA.occurred, Literal(str(fm["occurred"]), datatype=XSD.dateTime)))
    if fm.get("severity"):
        g.add((subj, QTA.severity, Literal(str(fm["severity"]))))
    for rr in fm.get("violated_rules") or []:
        g.add((subj, QTA.violatesRule, _iri(str(rr))))
    for s in fm.get("affected_strategies") or []:
        g.add((subj, QTA.affectsStrategy, _iri(str(s))))
    if fm.get("postmortem"):
        g.add((subj, QTA.hasPostMortem, _iri(str(fm["postmortem"]))))


def _add_postmortem(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.PostMortem)
    if fm.get("incident"):
        g.add((subj, QTA.postMortemOf, _iri(str(fm["incident"]))))
    if fm.get("status"):
        g.add((subj, QTA.status, Literal(str(fm["status"]))))
    for ai in fm.get("action_items") or []:
        g.add((subj, QTA.hasActionItem, Literal(str(ai))))


def _add_mlmodel(g: Graph, subj: URIRef, fm: dict) -> None:
    _add_common(g, subj, fm, QTA.MLModel)


DISPATCH = {
    "strategy": _add_strategy,
    "signal": _add_signal,
    "risk-rule": _add_risk_rule,
    "instrument": _add_instrument,
    "backtest": _add_backtest,
    "incident": _add_incident,
    "postmortem": _add_postmortem,
    "ml-model": _add_mlmodel,
}


def build_graph(docs_dir: Path = DOCS_DIR) -> tuple[Graph, list[str]]:
    """docs/ 하위 전체 md 파일을 읽어 인스턴스 그래프를 만든다."""
    g = Graph()
    g.bind("qta", QTA)
    g.bind("inst", INST)
    g.bind("rdfs", RDFS)

    processed: list[str] = []
    if not docs_dir.exists():
        return g, processed

    for md_path in sorted(docs_dir.rglob("*.md")):
        if ".obsidian" in md_path.parts:
            continue
        try:
            post = frontmatter.load(md_path)
        except Exception:
            continue
        fm = post.metadata or {}
        t = fm.get("type")
        if not t or t not in DISPATCH:
            continue
        id_ = fm.get("id") or md_path.stem
        subj = _iri(str(id_))
        DISPATCH[t](g, subj, fm)
        processed.append(f"{t}:{id_}")

    return g, processed


def push_to_graphdb(endpoint: str, repo: str, tbox: Path, abox: Path) -> None:
    if not tbox.exists():
        raise FileNotFoundError(f"T-Box missing: {tbox}")
    if not abox.exists():
        raise FileNotFoundError(f"A-Box missing: {abox} — run --write first")
    merged = Graph()
    merged.parse(tbox, format="turtle")
    merged.parse(abox, format="turtle")
    nt_body = merged.serialize(format="nt").strip()
    update_query = f"CLEAR DEFAULT ;\nINSERT DATA {{ {nt_body} }}"
    sparql_update(endpoint, repo, update_query)
    print(f"[ontology_sync] pushed {len(merged)} triples to {endpoint}/repositories/{repo} (default graph)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Obsidian 프론트매터 → RDF 동기화")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="diff 감지만, 파일 쓰지 않음")
    mode.add_argument("--write", action="store_true", help="instances.ttl 재생성")
    mode.add_argument("--push-graphdb", action="store_true",
                      help="Push T-Box + A-Box to GraphDB via CLEAR+INSERT atomic SPARQL Update")
    parser.add_argument("--endpoint",
                        default=os.environ.get("QTA_SPARQL_ENDPOINT", "http://localhost:7200"),
                        help="GraphDB base endpoint (default from QTA_SPARQL_ENDPOINT env or http://localhost:7200)")
    parser.add_argument("--repo", default="qta", help="GraphDB repository name")
    args = parser.parse_args()

    if args.push_graphdb:
        push_to_graphdb(args.endpoint, args.repo, TRADING_TTL, OUT_TTL)
        return 0

    g, processed = build_graph()
    print(f"[ontology_sync] {len(processed)} 인스턴스 처리")

    new_ttl = g.serialize(format="turtle")

    if args.check:
        if OUT_TTL.exists():
            current = OUT_TTL.read_text(encoding="utf-8")
            if current.strip() == new_ttl.strip():
                print("[ontology_sync] up-to-date")
                return 0
            print("[ontology_sync] instances.ttl 이 프론트매터와 불일치 — --write 필요")
            return 1
        print(f"[ontology_sync] {OUT_TTL} 없음 — --write 필요")
        return 1

    OUT_TTL.parent.mkdir(parents=True, exist_ok=True)
    OUT_TTL.write_text(new_ttl, encoding="utf-8")
    print(f"[ontology_sync] wrote {OUT_TTL} ({len(processed)} 인스턴스)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
