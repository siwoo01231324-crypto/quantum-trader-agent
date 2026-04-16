# 01_plan.md — #55 Protégé + GraphDB 연동

> 작성: 2026-04-16
> Consensus: Planner → Architect (SOUND-WITH-REVISIONS) → Critic (APPROVE with fixes applied)

---

## AC 체크리스트
- [ ] `docker compose -f infra/graphdb/docker-compose.yml up` 으로 GraphDB 기동
- [ ] `python scripts/graphdb_bootstrap.py` 로 repo 생성 + TTL 로드 성공
- [ ] `python scripts/ontology_sync.py --push-graphdb` 로 instances.ttl 업로드
- [ ] 브라우저 workbench 에서 `live_strategies.rq` 실행 결과 확인
- [ ] MCP SPARQL 도구가 환경변수 분기로 GraphDB 엔드포인트 사용 가능
- [ ] Protégé 에서 `trading.ttl` 로드해 클래스 트리 시각 편집 후 diff 최소
- [ ] `protege-setup.md`, `graphdb-ops.md` 작성
- [ ] CI 스모크 테스트 통과

## 개발 체크리스트
- [ ] 테스트 코드 포함 (pytest + `responses` HTTP mock)
- [ ] `infra/.ai.md`, `infra/graphdb/.ai.md` 신규 생성 + `docs/runbooks/.ai.md` 수정
- [ ] 불변식 위반 없음 (`scripts/check_invariants.py --strict`)

## 선행 조건
- #54 (SHACL) 머지 완료 ✓ — GraphDB SHACL 플러그인 활용 가능 (follow-up)
- #51 (Obsidian MCP) 머지 완료 ✓ — `services/obsidian_mcp/tools.py:sparql()` 확장 대상

---

## 구현 계획

### RALPLAN-DR Summary (SHORT)

#### Principles
1. **LLM-safe boundary 유지** — GraphDB 는 query/edit 도구만. 주문·리스크 경로 불변.
2. **Local-first fallback** — `SPARQL_ENDPOINT`/`ctx.sparql_endpoint` 미설정 시 기존 rdflib 경로로 자동 사용. Docker 안 돌려도 개발 가능.
3. **File is SOT** — `docs/ontology/trading.ttl` + `instances.ttl` 이 정답. GraphDB 는 derived index.
4. **TDD on IO** — 모든 새 HTTP 코드는 `responses` 로 Red 먼저. 라이브 GraphDB 는 CI smoke 에서만.
5. **Lossless TTL round-trip** — Protégé 저장 후에도 `@prefix`, `rdfs:comment` 보존. pytest 로 회귀 차단.

#### Decision Drivers
1. AC 본문이 "GraphDB", "workbench" 를 명시 — 대체 시 AC 재작성 비용 발생
2. GitHub Actions `services:` 에서 GraphDB 가 ≤90s 내 기동 가능 (이미지 크기 + healthcheck 고려)
3. Protégé 가 파일 기반 편집이라 서버 선택과 독립 — GraphDB 선택은 AC 충족에 직접 기여

#### Viable Options

**Option A — Ontotext GraphDB Free 10.6.4 [DEFAULT, 채택]**
| Pros | Cons |
|---|---|
| AC 본문 명시와 일치 (스펙 드리프트 0) | Ontotext EULA (비 Apache, 상업 친화적이나 OSI 아님) |
| `http://localhost:7200` Workbench UI — AC4 직결 | ~1.2GB 이미지, CI 풀 cold-start ~30-60s |
| SHACL validation 내장 — #54 synergy (follow-up) | Free 2-core query 제한 (vault 규모에서 무관) |
| REST `/repositories/{id}/statements` bulk upload | Repo 생성 시 `repo-config.ttl` 필요 (one-time) |

**Option B — Apache Jena Fuseki 5.x [문서 전용, CI 제외]**
| Pros | Cons |
|---|---|
| Apache 2.0 라이선스 | Workbench UI 빈약 — AC4 충족 약함 |
| ~200MB 이미지, CI 훨씬 빠름 | SHACL UI 없음 (우린 `pyshacl` 사용 중) |
| TDB2 파일 기반 스토리지 — SOT 원칙 친화 | 리포지토리 isolation 모델 없음 |

**Architect 대안 제기**: Fuseki 가 Apache + 12x 작은 이미지 + file-SOT 친화로 원칙 정렬이 더 강함. 반박 — AC4 Workbench 명시 + `docs/ontology/queries/*.rq` 3개 파일에 `GRAPH`/`FROM` 없음 (GraphDB Free 는 union default 로 설정 가능, Fuseki 도 가능하지만 Workbench 약점이 tiebreak). **Fuseki 는 untested 로 docker-compose 파일을 만들지 않고 runbook 문서화만** (NB2 fix).

**거부된 대안**: Blazegraph (archived), Stardog (라이선스), Oxigraph (Workbench 없음), in-memory rdflib (영속성/UI 없음).

#### ADR

**Decision**: GraphDB Free 10.6.4 채택. A-Box 는 **default graph** 에 배치 (B1 fix option a) — 기존 `.rq` 파일 3개 (`live_strategies.rq`, `critical_violations.rq`, `strategy_without_tests.rq`) 무수정. `scripts/ontology_sync.py --push-graphdb` 는 `CLEAR DEFAULT; INSERT DATA { ... }` SPARQL Update 로 원자적 교체. MCP `sparql()` 는 `ctx.sparql_endpoint` > 환경변수 `QTA_SPARQL_ENDPOINT` > 로컬 rdflib 순 폴백 (NB7 fix).

**Drivers**: (1) AC 명시, (2) CI docker service 호환, (3) vault 규모 (~100 triples) 에서 Free 제약 무관.

**Alternatives considered**: Fuseki (runbook 문서화만), Blazegraph/Stardog/Oxigraph (결격).

**Why chosen**: 8개 AC 전체를 그대로 만족 + Workbench UX + T-Box/A-Box 단일 default graph 유지로 기존 코드·쿼리 호환성 100%.

**Consequences**:
- (+) 모든 AC 재작성 없이 충족. 기존 `.rq` 파일 무수정.
- (+) MCP SELECT/ASK 가드 3계층 방어 (엔드포인트 + `Graph.query()` + regex).
- (−) 비 Apache EULA (repo 는 바이너리 배포 없음 — 라이선스 전파 없음).
- (−) +30s CI cold-start (follow-up: `actions/cache`).

**Follow-ups**:
1. Single-txn `CLEAR DEFAULT; INSERT DATA` 최적화 (`scripts/_graphdb_client.py` 내) — Phase 3 에서 바로 적용.
2. `check_invariants.py` 에 `.ai.md` 존재 확인 추가.
3. GraphDB basic-auth 지원 (prod 배포 시).
4. CI cold-start 측정 후 `actions/cache` 레이어 캐싱.
5. SHACL 검증을 `pyshacl` → GraphDB 내장 플러그인으로 통합.
6. Protégé headless CI 체크 (주간 workflow).

---

### Requirements Summary

| AC | 산출물 | 검증 |
|----|---|---|
| 1 | `infra/graphdb/docker-compose.yml` + `.ai.md` | `curl -sf http://localhost:7200/rest/repositories` → 200 (≤60s) |
| 2 | `scripts/graphdb_bootstrap.py` | `curl .../repositories/qta/size` ≥ 8 (T-Box classes) |
| 3 | `scripts/ontology_sync.py --push-graphdb` | `ASK { ?s a qta:Strategy }` → true (default graph) |
| 4 | Workbench 에서 `live_strategies.rq` | 브라우저 수동 + CI curl `application/sparql-results+json` bindings ≥ 1 |
| 5 | `tools.py:sparql()` env-var 분기 | pytest: mock HTTP 200 → remote 경로, unset → rdflib 경로 |
| 6 | `protege-setup.md` + round-trip 테스트 | `tests/test_ontology_roundtrip.py` 통과 (rdflib `isomorphic()` + prefix/comment 카운트) |
| 7 | `graphdb-ops.md`, `protege-setup.md` | `check_invariants.py --strict` 통과 (프론트매터 `type` 필드) |
| 8 | `.github/workflows/graphdb-smoke.yml` | PR green |

---

### Phase 1 — Protégé round-trip + onboarding (S)

**Goal**: Protégé 5.6 가 `trading.ttl` 저장 후 prefix/comment 보존 확인. 재발 시 CI 차단.

**Create**:
- `tests/test_ontology_roundtrip.py` — `pytest.xfail` 플래그로 시작 (NB3 fix: 골든 픽스처 수동 생성 전까지 Phase 2-5 진행 차단 방지)
- `tests/fixtures/ontology/trading_after_protege.ttl` — 사람이 Protégé 에서 저장 후 커밋 (사전 고정된 버전에서)
- `docs/onboarding/protege-setup.md` — frontmatter `type: onboarding`, `id: protege-setup`

**Modify**: 없음 (trading.ttl 은 건드리지 않음 — 주석 센티넬 추가 제안 폐기)

**TDD Red**:
```python
# tests/test_ontology_roundtrip.py
import re, pytest
from pathlib import Path
from rdflib import Graph
from rdflib.compare import isomorphic

TTL = Path("docs/ontology/trading.ttl")
GOLDEN = Path("tests/fixtures/ontology/trading_after_protege.ttl")

@pytest.mark.xfail(not GOLDEN.exists(), reason="awaiting human-generated Protégé fixture")
def test_protege_roundtrip_isomorphic():
    assert isomorphic(Graph().parse(TTL), Graph().parse(GOLDEN))

@pytest.mark.xfail(not GOLDEN.exists(), reason="awaiting Protégé fixture")
def test_prefixes_preserved():
    raw_after = GOLDEN.read_text("utf-8")
    for p in ("qta:", "inst:", "rdfs:", "xsd:"):
        assert f"@prefix {p}" in raw_after

@pytest.mark.xfail(not GOLDEN.exists(), reason="awaiting Protégé fixture")
def test_comments_preserved():
    count = lambda p: len(re.findall(r'rdfs:comment\s+"', p.read_text("utf-8")))
    assert count(GOLDEN) >= count(TTL)
```

**Green pointers**:
- Protégé 5.6.4 설치 → `trading.ttl` 열기 → File → Save as → **Turtle** 선택 (RDF/XML 금지) → `tests/fixtures/ontology/trading_after_protege.ttl` 로 커밋
- 실패 시 `protege-setup.md` 에 워크어라운드 기재

**`protege-setup.md` 필수 포함** (NB6 fix):
- **Protégé 버전 고정**: 5.6.4+
- 설치: `brew install --cask protege` (macOS) / 공식 zip (Win/Linux)
- **저장 설정 고정**: Preferences → Renderer → "Render by label OFF" (실험 필요 시), Save as dialog → Format: **Turtle**, UTF-8, LF line endings
- 편집 규칙: `@prefix` 순서 유지, 모든 Class 에 `rdfs:comment` 유지
- 저장 후: `python scripts/check_invariants.py --strict` 통과 확인

**Verify**:
```bash
python -m pytest tests/test_ontology_roundtrip.py -v   # xfail 까지만 확인
```

**Risks**: blank node label 차이 → `isomorphic()` canonical 비교로 흡수. 골든 픽스처 수동 생성 개발자 변수 → `protege-setup.md` 에 버전 + 설정 pin.

---

### Phase 2 — GraphDB compose + bootstrap (M)

**Goal**: `docker compose up` → healthcheck 통과 → `graphdb_bootstrap.py` 가 `qta` repo 생성 + T-Box 로드.

**Create**:
- `infra/.ai.md`
- `infra/graphdb/.ai.md`
- `infra/graphdb/docker-compose.yml`
- `infra/graphdb/repo-config.ttl` — GraphDB repo config (RDF). 핵심: default graph 에 T-Box+A-Box 공존하도록 설정 (A-Box 를 default graph 에 push 하므로).
- `scripts/graphdb_bootstrap.py`
- `scripts/graphdb_client.py` — 공용 HTTP 클라이언트 (NB1 fix: underscore 제거, `_graphdb_client.py` → `graphdb_client.py`)
- `tests/test_graphdb_bootstrap.py`

**Modify**: `.gitignore` — `infra/graphdb/data/`, `infra/graphdb/logs/`

**`docker-compose.yml`**:
```yaml
services:
  graphdb:
    image: ontotext/graphdb:10.6.4
    container_name: qta-graphdb
    ports: ["${GDB_PORT:-7200}:7200"]
    environment:
      GDB_JAVA_OPTS: "${GDB_JAVA_OPTS:--Xmx2g -Xms512m}"
    volumes:
      - ./data:/opt/graphdb/home/data
      - ./logs:/opt/graphdb/home/logs
    healthcheck:
      test: ["CMD", "curl", "-sf", "http://localhost:7200/rest/repositories"]
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 30s
    restart: unless-stopped
```

**`scripts/graphdb_client.py`** (공용 HTTP helper):
- `wait_for_ready(endpoint, timeout)` — GET `/rest/repositories`, retry 3x with 2s backoff
- `repo_exists(endpoint, repo) -> bool` — GET `/rest/repositories/{repo}` 200/404 분기
- `create_repo(endpoint, repo, config_path)` — POST `/rest/repositories` multipart w/ `repo-config.ttl`
- `upload_ttl(endpoint, repo, ttl_bytes, context=None)` — POST `/repositories/{repo}/statements`
- `sparql_update(endpoint, repo, update_query)` — POST `/repositories/{repo}/statements` `Content-Type: application/sparql-update`
- 공통: `requests` 라이브러리, 5s timeout, 에러 시 raise

**`scripts/graphdb_bootstrap.py`**:
- args: `--endpoint` (default `http://localhost:7200`), `--repo` (default `qta`), `--tbox` (default `docs/ontology/trading.ttl`), `--timeout` (default 90s)
- 순서: `wait_for_ready` → `repo_exists` 확인 → 없으면 `create_repo` → `upload_ttl` (T-Box) → size 출력
- exit 0/1

**TDD Red**:
```python
# tests/test_graphdb_bootstrap.py
import responses, pytest
from pathlib import Path
from scripts.graphdb_bootstrap import bootstrap
from scripts.graphdb_client import wait_for_ready

@responses.activate
def test_wait_for_ready_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda _: None)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", status=503)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", status=503)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", json=[], status=200)
    wait_for_ready("http://gdb:7200", timeout=10)

@responses.activate
def test_bootstrap_creates_repo_and_loads_tbox(tmp_path):
    tbox = tmp_path / "trading.ttl"
    tbox.write_text('@prefix qta: <http://qta/#> . qta:Strategy a <http://www.w3.org/2000/01/rdf-schema#Class> .')
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
    responses.add(responses.GET, "http://gdb:7200/rest/repositories", json=[{"id":"qta"}], status=200)
    responses.add(responses.GET, "http://gdb:7200/rest/repositories/qta", status=200)
    responses.add(responses.POST, "http://gdb:7200/repositories/qta/statements", status=204)
    responses.add(responses.GET, "http://gdb:7200/repositories/qta/size", body="8", status=200)
    bootstrap(endpoint="http://gdb:7200", repo="qta", tbox=tbox)
    assert not any(c.request.method == "POST" and c.request.url.endswith("/rest/repositories") for c in responses.calls)
```

**Verify**:
```bash
docker compose -f infra/graphdb/docker-compose.yml up -d
python scripts/graphdb_bootstrap.py
curl -sf http://localhost:7200/repositories/qta/size  # ≥8
python -m pytest tests/test_graphdb_bootstrap.py -v
```

**Risks**: 포트 7200 충돌 → `GDB_PORT` env override 문서화. 라이선스 EULA prompt → 헤드리스에서 자동 수락 (Ontotext 10.x 확인). `-Xmx2g` 저사양 랩탑에서 OOM → `.env` 로 `GDB_JAVA_OPTS=-Xmx1g` override 가능.

---

### Phase 3 — `ontology_sync.py --push-graphdb` (S)

**Goal**: 기존 `scripts/ontology_sync.py` (argparse lines 212-217) 에 `--push-graphdb` 추가. A-Box 를 default graph 에 atomic replace (B1 fix option a).

**Modify**:
- `scripts/ontology_sync.py` — `--push-graphdb`, `--endpoint`, `--repo` 옵션 추가. 기존 `--check`/`--write` 와 조합 불가 시 fail-fast.

**Create**: `tests/test_ontology_sync_push.py`

**Behavior spec**:
- `--push-graphdb` 는 `docs/ontology/instances.ttl` (OUT_TTL) 을 읽어 **default graph** 에 `CLEAR DEFAULT; INSERT DATA { ... }` 로 원자 교체 (ADR follow-up #1 바로 적용 — 2-step DELETE+POST 대신 단일 SPARQL Update)
- T-Box 는 건드리지 않음 (T-Box 는 bootstrap 시에만 `trading.ttl` 에서 로드; A-Box 만 반복 재업로드)
- **A-Box 만 clear 하려면** default graph 내 T-Box 와 A-Box 구분이 필요 — 해결: T-Box 도 default graph, A-Box 도 default graph 지만 bootstrap 후 `CLEAR DEFAULT` 는 T-Box 까지 날림. **최종 방침**: `CLEAR DEFAULT` 후 T-Box 재업로드(`trading.ttl`) + A-Box 업로드(`instances.ttl`) 를 단일 SPARQL Update 로 묶음 (`CLEAR DEFAULT; INSERT DATA { <tbox> ... <abox> ... }`). 구현 편의상 `scripts/graphdb_client.py` 의 `sync_default_graph(endpoint, repo, tbox_path, abox_path)` 로 캡슐화.
- endpoint 우선순위: `--endpoint` arg > `QTA_SPARQL_ENDPOINT` env > default `http://localhost:7200`

**TDD Red**:
```python
# tests/test_ontology_sync_push.py
import responses, pytest
from pathlib import Path
from scripts.ontology_sync import push_to_graphdb

@responses.activate
def test_push_issues_clear_and_insert(tmp_path):
    tbox = tmp_path / "trading.ttl"; tbox.write_text('@prefix qta: <http://qta/#> . qta:Strategy a <http://www.w3.org/2000/01/rdf-schema#Class> .')
    abox = tmp_path / "instances.ttl"; abox.write_text('@prefix inst: <http://qta/inst#> . inst:s1 a <http://qta/#Strategy> .')
    responses.add(responses.POST, "http://gdb:7200/repositories/qta/statements", status=204)
    push_to_graphdb(endpoint="http://gdb:7200", repo="qta", tbox=tbox, abox=abox)
    assert len(responses.calls) == 1
    body = responses.calls[0].request.body.decode() if isinstance(responses.calls[0].request.body, bytes) else responses.calls[0].request.body
    assert "CLEAR DEFAULT" in body.upper()
    assert "INSERT DATA" in body.upper()

def test_push_fails_if_abox_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        push_to_graphdb(endpoint="http://gdb:7200", repo="qta", tbox=tmp_path/"trading.ttl", abox=tmp_path/"nope.ttl")
```

**Green pointers**:
- `parser.add_argument("--push-graphdb", action="store_true")` at line 217
- `parser.add_argument("--endpoint", default=os.environ.get("QTA_SPARQL_ENDPOINT", "http://localhost:7200"))`
- `if args.push_graphdb: push_to_graphdb(args.endpoint, "qta", TRADING_TTL, OUT_TTL)`
- `push_to_graphdb()` 는 `graphdb_client.sparql_update()` 호출

**Verify**:
```bash
python scripts/ontology_sync.py --write
python scripts/ontology_sync.py --push-graphdb --endpoint http://localhost:7200
curl -sf -G http://localhost:7200/repositories/qta \
  --data-urlencode 'query=ASK { ?s a <http://qta/#Strategy> }' \
  -H "Accept: application/sparql-results+json"  # {"boolean": true}
python -m pytest tests/test_ontology_sync_push.py -v
```

**Risks**: `CLEAR DEFAULT; INSERT DATA` 중 네트워크 장애 → 단일 POST 이므로 원자적 (SPARQL Update 트랜잭션). instances.ttl 이 큰 경우 (현재 ~1KB; 100KB 이하 예상) POST body 크기 문제 없음. **T-Box 갱신 플로우**: `trading.ttl` 이 바뀌면 `graphdb_bootstrap.py` 재실행 (또는 `--push-graphdb` 가 T-Box 도 포함하므로 그대로 동기화됨) — runbook 에 명시.

---

### Phase 4 — MCP SPARQL endpoint fallback (S)

**Goal**: `services/obsidian_mcp/tools.py:355-388 sparql()` 가 env/ctx 로 GraphDB 엔드포인트 사용. 3계층 방어 (B2 fix).

**Modify**:
- `services/obsidian_mcp/tools.py` — `sparql()` 에 엔드포인트 분기
- `services/obsidian_mcp/` 내 VaultContext 관련 파일 (impl 시 확인; config 로딩 위치) — env `QTA_SPARQL_ENDPOINT` 문서화
- `docs/onboarding/mcp-setup.md` — `sparql_endpoint` 설정 예시 섹션 추가

**Create**: `tests/test_obsidian_mcp_sparql.py`

**3계층 방어 (B2 fix)**:
1. **엔드포인트 분리**: MCP 는 `/repositories/{repo}` (read) 만 호출. `/statements` (write) 절대 호출 금지. 상수로 `_QUERY_PATH = "/repositories"`, `_WRITE_PATH = "/repositories/{}/statements"` 분리, `sparql()` 은 `_QUERY_PATH` 만 참조.
2. **`Graph.query()` 고정**: local 폴백 경로는 `g.query()` 만 호출 (이미 `tools.py:376` 에서 그러함). 주석으로 명시: `# never use Graph.update() here — LLM safety boundary`.
3. **Regex 가드 (defense-in-depth)**: `re.match(r'^\s*(?:#[^\n]*\n|PREFIX[^\n]*\n|BASE[^\n]*\n)*\s*(SELECT|ASK|DESCRIBE|CONSTRUCT)\b', query, re.IGNORECASE | re.MULTILINE)` — 주석/`PREFIX`/`BASE` 전치 허용, `;` multi-statement 는 rdflib `Graph.query()` 가 거부하므로 추가 처리 불필요.

**Precedence (NB7 fix)**:
1. `ctx.sparql_endpoint` (programmatic) → remote
2. `os.environ["QTA_SPARQL_ENDPOINT"]` → remote
3. local rdflib

**TDD Red**:
```python
# tests/test_obsidian_mcp_sparql.py
import responses, pytest
from services.obsidian_mcp.tools import sparql
# _build_ctx 는 기존 test_obsidian_mcp.py 의 fixture 패턴 재사용

SELECT_Q = "SELECT ?s WHERE { ?s a <http://qta/#Strategy> } LIMIT 5"

def test_sparql_local_when_nothing_configured(monkeypatch, tmp_vault_ctx):
    monkeypatch.delenv("QTA_SPARQL_ENDPOINT", raising=False)
    result = sparql(tmp_vault_ctx, SELECT_Q)
    assert result["source"] == "local-rdflib"

@responses.activate
def test_sparql_remote_via_env(monkeypatch, tmp_vault_ctx):
    monkeypatch.setenv("QTA_SPARQL_ENDPOINT", "http://gdb:7200/repositories/qta")
    responses.add(responses.POST, "http://gdb:7200/repositories/qta",
        json={"head":{"vars":["s"]},"results":{"bindings":[{"s":{"type":"uri","value":"http://x"}}]}},
        status=200, content_type="application/sparql-results+json")
    result = sparql(tmp_vault_ctx, SELECT_Q)
    assert result["source"] == "remote-http"
    assert len(result["bindings"]) == 1

@responses.activate
def test_sparql_ctx_overrides_env(monkeypatch, tmp_vault_ctx_with_endpoint):
    # tmp_vault_ctx_with_endpoint.sparql_endpoint = "http://ctx:7200/repositories/qta"
    monkeypatch.setenv("QTA_SPARQL_ENDPOINT", "http://env:7200/repositories/qta")
    responses.add(responses.POST, "http://ctx:7200/repositories/qta",
        json={"head":{"vars":[]},"results":{"bindings":[]}}, status=200)
    sparql(tmp_vault_ctx_with_endpoint, SELECT_Q)
    assert "ctx:7200" in responses.calls[0].request.url  # ctx wins

def test_sparql_blocks_update(tmp_vault_ctx):
    with pytest.raises(ValueError, match="only SELECT|ASK|DESCRIBE|CONSTRUCT"):
        sparql(tmp_vault_ctx, "INSERT DATA { <a> <b> <c> }")

def test_sparql_remote_uses_query_endpoint_not_statements(monkeypatch, tmp_vault_ctx):
    # 상수 _WRITE_PATH 가 sparql() 내 어디서도 참조되지 않는지 static check
    import services.obsidian_mcp.tools as mod
    import ast, inspect
    src = inspect.getsource(mod.sparql)
    assert "/statements" not in src
```

**Green pointers**:
- `_resolve_endpoint(ctx) -> Optional[str]`: ctx 먼저, env 두 번째
- 원격: `requests.post(endpoint, data={"query": q}, headers={"Accept": "application/sparql-results+json"}, timeout=5.0)`
- 응답 JSON → 기존 return shape + `source` 필드 추가
- 실패 (connection/timeout/5xx) → `RuntimeError("remote SPARQL failed: {}".format(e))` raise (silent fallback 금지 — Principle 2 exception: 명시적 원격 요청 시에만)

**Verify**:
```bash
pytest tests/test_obsidian_mcp_sparql.py -v
pytest tests/test_obsidian_mcp.py -v  # 기존 테스트 무영향 확인
QTA_SPARQL_ENDPOINT=http://localhost:7200/repositories/qta python -c "
from services.obsidian_mcp.tools import sparql
# 통합 smoke
"
```

**Risks**: 기존 `sparql()` 호출부의 return shape 의존성 → impl 첫 단계로 `grep -rn '\.sparql(\|tools\.sparql' services/ tests/` 감사. 원격 HTTP 실패 시 silent fallback 요청이 와도 **거부** — 명시적 구성이므로 raise 가 정답.

---

### Phase 5 — Runbook + CI smoke (M)

**Goal**: 사람 운영자가 스택 올릴 수 있고, Protégé 편집자가 TTL 손상 회피법 알 수 있고, CI 가 전체 체인 검증.

**Create**:
- `docs/runbooks/graphdb-ops.md` — frontmatter `type: runbook`, `id: graphdb-ops`
- `.github/workflows/graphdb-smoke.yml`

**Modify**:
- `docs/runbooks/.ai.md` (NB5 fix: 이미 존재 — 수정) — graphdb-ops.md 추가 언급
- `docs/onboarding/mcp-setup.md` — remote SPARQL 섹션 추가
- (No change to `.github/workflows/ontology-check.yml` — 별도 workflow 로 격리해 ontology-check 경량 유지)

**`graphdb-ops.md` 필수 섹션**:
- 증상→원인→조치 테이블 (포트 충돌, JVM OOM, EULA, 쿼리 500, Free 용량 경고)
- 백업 `/opt/graphdb/bin/backup` 절차
- **Fuseki 수동 전환 가이드** (runbook 문서만 — CI 에서 안 돌림): docker 이미지, endpoint 형식, `graphdb_client.py` 재사용 여부
- `SPARQL_ENDPOINT` 환경변수와 `ctx.sparql_endpoint` 우선순위 명시

**`.github/workflows/graphdb-smoke.yml`**:
```yaml
name: graphdb-smoke
on:
  pull_request:
    paths:
      - 'docs/ontology/**'
      - 'scripts/ontology_sync.py'
      - 'scripts/graphdb_bootstrap.py'
      - 'scripts/graphdb_client.py'
      - 'services/obsidian_mcp/**'
      - 'infra/graphdb/**'
      - 'tests/test_graphdb_bootstrap.py'
      - 'tests/test_ontology_sync_push.py'
      - 'tests/test_obsidian_mcp_sparql.py'
      - '.github/workflows/graphdb-smoke.yml'
  workflow_dispatch: {}
jobs:
  smoke:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    services:
      graphdb:
        image: ontotext/graphdb:10.6.4
        ports: ["7200:7200"]
        options: >-
          --health-cmd "curl -sf http://localhost:7200/rest/repositories"
          --health-interval 5s --health-timeout 3s --health-retries 20 --health-start-period 30s
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install rdflib pyyaml jsonschema python-frontmatter pydantic pyshacl requests responses pytest
      - name: Bootstrap + sync
        run: |
          python scripts/graphdb_bootstrap.py --endpoint http://localhost:7200 --repo qta --timeout 90
          python scripts/ontology_sync.py --write
          python scripts/ontology_sync.py --push-graphdb --endpoint http://localhost:7200
      - name: SPARQL smoke (AC4)
        run: |
          RESP=$(curl -sf -G http://localhost:7200/repositories/qta \
              --data-urlencode "query=$(cat docs/ontology/queries/live_strategies.rq)" \
              -H "Accept: application/sparql-results+json")
          echo "$RESP"
          COUNT=$(echo "$RESP" | python -c "import json,sys; print(len(json.load(sys.stdin)['results']['bindings']))")
          test "$COUNT" -ge 1
      - name: Latency budget (p95 < 2000ms over 5 samples)  # B3 fix: 500 → 2000ms
        run: |
          : > times.txt
          for i in 1 2 3 4 5; do
            /usr/bin/time -f '%e' curl -sf -G http://localhost:7200/repositories/qta \
              --data-urlencode "query=SELECT ?s WHERE {?s ?p ?o} LIMIT 100" \
              -H "Accept: application/sparql-results+json" -o /dev/null 2>> times.txt
          done
          python -c "
          ts = sorted(float(x) for x in open('times.txt').read().split())
          p95 = ts[-1]  # 5-sample p95 = max
          print(f'p95={p95}s')
          import sys; sys.exit(0 if p95 < 2.0 else 1)"
      - name: Unit tests (mocked)
        env: { QTA_SPARQL_ENDPOINT: "" }
        run: pytest tests/test_graphdb_bootstrap.py tests/test_ontology_sync_push.py tests/test_obsidian_mcp_sparql.py tests/test_ontology_roundtrip.py -v
```

**Verify**: PR 올리면 workflow 동작 확인. 로컬 `act -j smoke` 로 드라이런 가능 (runbook 에 문서).

**Risks**: GraphDB cold-start 이미지 풀 시간 → 10분 timeout 내 완결. p95 < 2000ms 가 플레이키하면 runbook 에서 continue-on-error 분리 또는 latency-only job 으로 분리 검토.

---

### Cross-cutting Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| GraphDB 기동 실패/포트 충돌 | M | M | healthcheck + 30s start_period + 20 retries; `GDB_PORT` env override |
| `QTA_SPARQL_ENDPOINT` 미설정 시 사용자 혼동 | L | M | `result["source"]` 필드 + 로그로 분기 투명화 |
| Protégé 재저장 시 prefix/comment 손실 | M | H (불변식 차단) | Phase 1 round-trip 테스트 + `protege-setup.md` 에 버전·설정 pin |
| GraphDB Free 용량 한계 | L (~100 triples 에선 무관) | M | runbook 에 Fuseki 수동 전환 가이드 |
| TTL drift (file vs GraphDB) | M | H | File=SOT 원칙; `ontology_sync.py` 는 push 전용 (never pull); 재동기화는 `--push-graphdb` 재실행 (runbook 명시) |
| MCP 에서 LLM 쿼리 injection | M | M | 3계층: 엔드포인트 분리 + `Graph.query()` 고정 + regex |
| 1.2GB 이미지 CI cold-start | L (≤10min 내 여유) | L | 측정 후 `actions/cache` follow-up |
| 새 디렉토리 `.ai.md` 누락 | M | L (repo rule 위반) | PR 체크리스트; follow-up 로 `check_invariants.py` 확장 |

---

### Test Inventory

| 파일 | 역할 | 프레임워크/Mock |
|---|---|---|
| `tests/test_ontology_roundtrip.py` | Phase 1 — TTL 동형성 + prefix/comment 보존, `pytest.xfail` 가드 | pytest + rdflib `isomorphic()` |
| `tests/test_graphdb_bootstrap.py` | Phase 2 — wait-for-ready, repo 생성, idempotency | pytest + **`responses`** |
| `tests/test_ontology_sync_push.py` | Phase 3 — `CLEAR DEFAULT; INSERT DATA` 단일 POST, file 누락 시 예외 | pytest + **`responses`** |
| `tests/test_obsidian_mcp_sparql.py` | Phase 4 — ctx>env>local 우선순위, SELECT/ASK 가드, write 차단, 엔드포인트 분리 | pytest + **`responses`** + `monkeypatch` |
| `tests/fixtures/ontology/trading_after_protege.ttl` | Phase 1 골든 (수동 생성) | 커밋 블롭 |

**Mock 라이브러리**: **`responses`** (httpx 아님). Rationale: 기존 codebase 는 `requests` 패턴; `httpx` 도입은 별 이슈로 분리.

---

### 전역 Verification

**로컬 end-to-end (pre-PR 체크리스트)**:
```bash
# 1. compose up + healthy
docker compose -f infra/graphdb/docker-compose.yml up -d
until curl -sf http://localhost:7200/rest/repositories > /dev/null; do sleep 2; done

# 2. bootstrap (T-Box 로드)
python scripts/graphdb_bootstrap.py

# 3. A-Box 동기화
python scripts/ontology_sync.py --write
python scripts/ontology_sync.py --push-graphdb

# 4. AC4 smoke
curl -sf -G http://localhost:7200/repositories/qta \
  --data-urlencode "query=$(cat docs/ontology/queries/live_strategies.rq)" \
  -H "Accept: application/sparql-results+json" | jq '.results.bindings | length'
# expect: integer ≥ 1

# 5. AC5 — MCP remote 경로
QTA_SPARQL_ENDPOINT=http://localhost:7200/repositories/qta python -c "
from services.obsidian_mcp.tools import sparql
# ctx 주입 패턴은 tests 참조
"

# 6. AC6 — Protégé round-trip (사람이 Protégé 에서 저장 후)
python -m pytest tests/test_ontology_roundtrip.py -v

# 7. 불변식 + 전체 테스트
python scripts/check_invariants.py --strict
python -m pytest tests/ -v
```

---

### File Manifest

**Create (13)**:
1. `infra/.ai.md`
2. `infra/graphdb/.ai.md`
3. `infra/graphdb/docker-compose.yml`
4. `infra/graphdb/repo-config.ttl`
5. `scripts/graphdb_bootstrap.py`
6. `scripts/graphdb_client.py` (NB1: underscore 없음)
7. `tests/test_graphdb_bootstrap.py`
8. `tests/test_ontology_sync_push.py`
9. `tests/test_obsidian_mcp_sparql.py`
10. `tests/test_ontology_roundtrip.py`
11. `tests/fixtures/ontology/trading_after_protege.ttl`
12. `docs/runbooks/graphdb-ops.md`
13. `docs/onboarding/protege-setup.md`
14. `.github/workflows/graphdb-smoke.yml`

**Modify (5)**:
- `scripts/ontology_sync.py` — `--push-graphdb`, `--endpoint` 추가
- `services/obsidian_mcp/tools.py` — `sparql()` 3계층 방어 + env/ctx 분기
- `docs/onboarding/mcp-setup.md` — remote SPARQL 섹션
- `docs/runbooks/.ai.md` (NB5: 이미 존재) — graphdb-ops.md 추가 언급
- `.gitignore` — `infra/graphdb/data/`, `infra/graphdb/logs/`

**Excluded (NB2 fix)**: `infra/graphdb/docker-compose.fuseki.yml` — untested 라서 생성 금지. Fuseki 는 `graphdb-ops.md` 텍스트로만 문서화.

---

### 실행 순서 권장

1. **Phase 2 + 3 + 4 병렬 가능** (HTTP mock 이라 서로 독립)
2. Phase 1 은 xfail 로 뚫어놓고 사람이 Protégé 세팅 완료 시 픽스처 커밋
3. Phase 5 는 Phase 2-4 완료 후 CI 통합

총 예상: 2-3 dev-days + Protégé 픽스처 1 사람-시간.

---

### Changelog (consensus 반영)

- **B1** (blocking): A-Box 를 **default graph** 에 배치 (option a). 기존 `.rq` 파일 3개 무수정, `tools.py` rdflib 로더 무수정.
- **B2** (blocking): MCP SPARQL 3계층 방어 (엔드포인트 분리 + `Graph.query()` 고정 + regex).
- **B3** (blocking): CI p95 budget 500ms → **2000ms** (GHA 변동성 흡수, 10x 회귀 시그널 유지).
- **NB1**: `_graphdb_client.py` → `graphdb_client.py` (underscore 제거).
- **NB2**: `docker-compose.fuseki.yml` 폐기 — runbook 에 수동 전환 문서만.
- **NB3**: `pytest.xfail` 플래그로 Phase 1 과 Phase 2-5 의존성 분리.
- **NB5**: `docs/runbooks/.ai.md` manifest — modify (이미 존재 확인).
- **NB6**: `protege-setup.md` 에 Protégé 버전·저장 설정 pin.
- **NB7**: MCP endpoint precedence — `ctx.sparql_endpoint` > env > local.
- **ADR follow-up #1 앞당김**: `CLEAR DEFAULT; INSERT DATA` 단일 SPARQL Update 로 Phase 3 에서 즉시 구현 (2-step DELETE+POST 폐기).
