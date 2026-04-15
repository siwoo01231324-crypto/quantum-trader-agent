#!/usr/bin/env python3
"""
shacl_validate.py — SHACL 제약 기반 고급 검증 러너

docs/ontology/instances.ttl + shapes.ttl 을 pyshacl 로 검증한다.
위반 메시지는 한국어로 출력하고, --strict 모드에서 위반 시 exit 1.

사용법:
  python scripts/shacl_validate.py                 # 기본: warn 모드 (exit 0)
  python scripts/shacl_validate.py --strict        # fail 모드 (위반 시 exit 1)
  python scripts/shacl_validate.py --data X.ttl --shapes Y.ttl

의존성:
  pip install pyshacl rdflib
"""
from __future__ import annotations

import argparse
import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

REPO_ROOT = Path(__file__).resolve().parent.parent
ONTOLOGY_DIR = REPO_ROOT / "docs" / "ontology"
DEFAULT_DATA = ONTOLOGY_DIR / "instances.ttl"
DEFAULT_SHAPES = ONTOLOGY_DIR / "shapes.ttl"
DEFAULT_ONTOLOGY = ONTOLOGY_DIR / "trading.ttl"


@dataclass
class Violation:
    """단일 SHACL 위반 결과."""

    focus_node: str
    result_path: Optional[str]
    source_shape: Optional[str]
    message: str
    severity: str

    def format(self) -> str:
        shape = self.source_shape or "UnknownShape"
        path = f" · {self.result_path}" if self.result_path else ""
        return f"[{shape}] {self.focus_node}{path}\n  → {self.message}"


def _load_graph(path: Path):
    from rdflib import Graph

    g = Graph()
    g.parse(str(path), format="turtle")
    return g


def run_shacl(
    data_path: Path = DEFAULT_DATA,
    shapes_path: Path = DEFAULT_SHAPES,
    ontology_path: Optional[Path] = DEFAULT_ONTOLOGY,
) -> list[Violation]:
    """SHACL 검증을 실행하고 위반 리스트를 반환한다.

    data / shapes 가 없으면 빈 리스트 반환 (호출 측에서 경고 처리).
    """
    try:
        from pyshacl import validate as pyshacl_validate  # type: ignore
    except ImportError:
        raise RuntimeError(
            "pyshacl 미설치. `pip install pyshacl` 실행 후 재시도."
        )

    if not data_path.exists():
        return []
    if not shapes_path.exists():
        return []

    from rdflib import Graph, Namespace

    data_graph = _load_graph(data_path)
    shapes_graph = _load_graph(shapes_path)
    ont_graph = None
    if ontology_path is not None and ontology_path.exists():
        ont_graph = _load_graph(ontology_path)

    _conforms, results_graph, _results_text = pyshacl_validate(
        data_graph=data_graph,
        shacl_graph=shapes_graph,
        ont_graph=ont_graph,
        inference="rdfs",
        advanced=True,
        allow_warnings=False,
        meta_shacl=False,
        debug=False,
    )

    return _parse_results(results_graph, shapes_graph)


def _parse_results(results_graph, shapes_graph=None) -> list[Violation]:
    """pyshacl 결과 그래프에서 ValidationResult 트리플을 Violation 으로 변환.

    source_shape 이 blank node PropertyShape 인 경우 shapes_graph 에서
    이를 `sh:property` 로 포함하는 NodeShape 을 역추적해 이름을 해석한다.
    """
    from rdflib import BNode, Graph
    from rdflib.namespace import Namespace, RDF

    SH = Namespace("http://www.w3.org/ns/shacl#")

    assert isinstance(results_graph, Graph)

    violations: list[Violation] = []
    for result in results_graph.subjects(RDF.type, SH.ValidationResult):
        focus = results_graph.value(result, SH.focusNode)
        path = results_graph.value(result, SH.resultPath)
        shape = results_graph.value(result, SH.sourceShape)
        severity = results_graph.value(result, SH.resultSeverity)
        message = results_graph.value(result, SH.resultMessage)

        resolved_shape = _resolve_shape_name(shape, shapes_graph)

        violations.append(
            Violation(
                focus_node=str(focus) if focus else "(unknown)",
                result_path=str(path) if path else None,
                source_shape=resolved_shape,
                message=str(message) if message else "(메시지 없음)",
                severity=_short_name(severity) or "Violation",
            )
        )
    return violations


def _resolve_shape_name(shape, shapes_graph) -> Optional[str]:
    """source_shape 이 IRI 면 그대로, blank node 면 enclosing NodeShape 이름 반환."""
    from rdflib import BNode

    if shape is None:
        return None
    if not isinstance(shape, BNode):
        return _short_name(shape)
    if shapes_graph is None:
        return "AnonymousShape"

    from rdflib.namespace import Namespace

    SH = Namespace("http://www.w3.org/ns/shacl#")
    for node_shape in shapes_graph.subjects(SH.property, shape):
        if not isinstance(node_shape, BNode):
            return _short_name(node_shape)
    return "AnonymousShape"


def _short_name(term) -> Optional[str]:
    """URI 를 짧은 이름으로 축약 (마지막 `#` 이후 혹은 `/` 이후)."""
    if term is None:
        return None
    s = str(term)
    if "#" in s:
        return s.rsplit("#", 1)[-1]
    if "/" in s:
        return s.rsplit("/", 1)[-1]
    return s


def main() -> int:
    parser = argparse.ArgumentParser(description="SHACL 제약 기반 온톨로지 검증")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="검증 대상 데이터 TTL")
    parser.add_argument("--shapes", type=Path, default=DEFAULT_SHAPES, help="SHACL shapes TTL")
    parser.add_argument(
        "--ontology",
        type=Path,
        default=DEFAULT_ONTOLOGY,
        help="온톨로지(T-Box) TTL — targetClass 매칭용",
    )
    parser.add_argument("--strict", action="store_true", help="위반 있으면 exit 1")
    args = parser.parse_args()

    if not args.data.exists():
        print(f"[shacl] data 파일 없음: {args.data}")
        return 0 if not args.strict else 1
    if not args.shapes.exists():
        print(f"[shacl] shapes 파일 없음: {args.shapes}")
        return 0 if not args.strict else 1

    try:
        violations = run_shacl(args.data, args.shapes, args.ontology)
    except RuntimeError as e:
        print(f"[shacl] {e}")
        return 2

    if not violations:
        print(f"[shacl] 통과 (shapes={args.shapes.name}, data={args.data.name})")
        return 0

    print(f"[shacl] {len(violations)} 위반 감지")
    for v in violations:
        print("  - " + v.format().replace("\n", "\n    "))

    if args.strict:
        print("[shacl] --strict: FAIL")
        return 1
    print("[shacl] warn 모드 — 통과 처리")
    return 0


if __name__ == "__main__":
    sys.exit(main())
