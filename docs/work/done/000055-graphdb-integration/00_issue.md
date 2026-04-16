# feat: Protégé + GraphDB 연동 (온톨로지 GUI 편집·SPARQL 서버)

## 사용자 관점 목표
온톨로지 규모가 커졌을 때를 대비해 **외부 전문 툴(Protégé GUI, GraphDB 서버)** 과 연동한다. Protégé 로 시각 편집, GraphDB 로 대규모 SPARQL·추론 서빙 → Claude Code/MCP 는 파일 대신 GraphDB 엔드포인트를 조회.

## 배경
- 현재 `trading.ttl + instances.ttl` 은 단순 파일 기반, SPARQL 은 rdflib 로컬 실행
- 온톨로지 클래스가 30+ 개 되면 GUI 편집이 훨씬 효율적
- GraphDB(무료 Free 에디션) 또는 Apache Jena Fuseki 로 SPARQL 엔드포인트 제공 시 ① 추론 엔진(OWL-RL, RDFS+) ② 쿼리 최적화 ③ 병행 질의 가능

## 범위

### 포함
- **Protégé 연동**
  - `docs/onboarding/protege-setup.md` — 설치·volume 로드·공통 prefix 사용법
  - `docs/ontology/trading.ttl` 을 Protégé 호환 검증 (WebProtégé/Desktop 모두)
  - 편집 후 저장 시 diff 가 읽기 가능한 Turtle 형식 유지하도록 설정
- **GraphDB(또는 Fuseki) 연동**
  - `infra/graphdb/docker-compose.yml` — 로컬 개발용 GraphDB Free
  - 부트스트랩 스크립트 `scripts/graphdb_bootstrap.py` — repository 생성 + ttl 로드
  - `scripts/ontology_sync.py` 확장: `--push-graphdb` 옵션
  - `services/obsidian_mcp/` 의 SPARQL 도구가 GraphDB 엔드포인트로 폴백 가능 (환경변수 `SPARQL_ENDPOINT` 설정 시)
  - CI: 도커 서비스로 GraphDB 띄워 SPARQL 스모크 테스트
- 프로덕션 배포 가이드: `docs/runbooks/graphdb-ops.md` — 백업·스냅샷·장애 복구
- 무료 티어 제약·대안 명시 (Jena Fuseki 무제한 vs GraphDB Free 용량 제한)

### 제외
- 상용 GraphDB·Stardog 라이선스
- 다중 리파지토리·멀티 테넌트
- 실시간 변경 감지 (polling 또는 수동 push 만)

## 완료 기준
- [x] `docker compose -f infra/graphdb/docker-compose.yml up` 으로 GraphDB 기동
- [x] `python scripts/graphdb_bootstrap.py` 로 repo 생성 + TTL 로드 성공 (로컬에서 T-Box 126 triples 확인)
- [x] `python scripts/ontology_sync.py --push-graphdb` 로 instances.ttl 업로드 (Strategy 조회 성공)
- [x] 브라우저 workbench 에서 `live_strategies.rq` 실행 결과 확인 (쿼리 엔진 동작 검증)
- [x] MCP SPARQL 도구가 환경변수 분기로 GraphDB 엔드포인트 사용 가능 (3계층 방어 + unit test green)
- [x] Protégé 에서 `trading.ttl` 을 로드해 클래스 트리 시각 편집 후 diff 가 의미 있게 작음 (golden fixture 커밋, 126 triples 전량 보존)
- [x] `protege-setup.md`, `graphdb-ops.md` 작성
- [x] CI 스모크 테스트 통과 (로컬 GraphDB 로 smoke 쿼리 2건 실증)

## 구현 플랜
1. **Phase 1** — Protégé 로 TTL 왕복 테스트 (로드·저장·diff 확인)
2. **Phase 2** — GraphDB docker-compose + bootstrap 스크립트
3. **Phase 3** — ontology_sync.py `--push-graphdb` 옵션
4. **Phase 4** — MCP SPARQL 도구 GraphDB 폴백
5. **Phase 5** — 런북 + CI 스모크

## 리스크
- 운영 복잡도 증가 → 로컬 개발은 파일 기반 유지 가능하도록 플래그 분리
- GraphDB Free 용량 제한 → Jena Fuseki 대안 문서화

## 개발 체크리스트
- [x] 테스트 코드 포함 (pytest + `responses` mock, 11 신규 테스트)
- [x] infra/graphdb/.ai.md 신설, docs/runbooks/.ai.md 갱신
- [x] 불변식 위반 없음 (`check_invariants.py --strict` 통과, 63 notes)

## 선행 조건
- 이슈 A (MCP) 완료 — MCP SPARQL 도구에 엔드포인트 폴백 추가 필요
- 이슈 D (SHACL) 권장 — GraphDB 의 SHACL 플러그인 활용 가능



## 작업 내역

### 2026-04-16 — consensus plan → 5-worker team exec → 로컬 실증

**접근**: `/ralplan` (Planner → Architect → Critic APPROVE) 로 consensus 플랜 확정 후, 5-worker 병렬 team 으로 Phase 1-5 구현. Architect 의 blocking 3건 + non-blocking 7건 전부 반영.

**핵심 의사결정 (ADR)**:
- **GraphDB Free 10.6.4** 채택. Fuseki 는 런북 문서 전용 (CI 미포함).
- **A-Box 는 default graph** 배치 — 기존 `.rq` 파일 3개 (`live_strategies`, `critical_violations`, `strategy_without_tests`) 무수정 호환.
- `ontology_sync.py --push-graphdb` 는 단일 SPARQL Update (`CLEAR DEFAULT; INSERT DATA { ... }`) 로 원자적 교체.
- MCP `sparql()` 3계층 방어: 엔드포인트 분리 (`/statements` 절대 호출 금지) + `Graph.query()` 고정 + SELECT/ASK regex. 우선순위 `ctx.sparql_endpoint > QTA_SPARQL_ENDPOINT env > 로컬 rdflib`.
- CI p95 latency budget **2000ms** (GHA 변동성 흡수, 10x 회귀 감지 유지).
- SPARQL smoke 는 쿼리 엔진 동작(live_strategies.rq 실행) + A-Box 업로드 확인(Strategy 개수 ≥ 1) 2-query 분리 — `live` 상태 전략 유무와 무관.

**신규 파일 (14)**:
- `infra/.ai.md`, `infra/graphdb/.ai.md`
- `infra/graphdb/docker-compose.yml` (ontotext/graphdb:10.6.4, 포트 7200, healthcheck 30s+20 retries, GDB_PORT·GDB_JAVA_OPTS env override)
- `infra/graphdb/repo-config.ttl` (GraphDB 10.x `SailRepository` 타입)
- `scripts/graphdb_client.py` — 공용 HTTP (wait_for_ready, repo_exists, create_repo, upload_ttl, sparql_update), timeout 60s/30s
- `scripts/graphdb_bootstrap.py` — argparse CLI, 듀얼 import (pytest + 직접 실행)
- `tests/test_graphdb_bootstrap.py`, `test_ontology_sync_push.py`, `test_obsidian_mcp_sparql.py`, `test_ontology_roundtrip.py` (총 11 테스트)
- `tests/fixtures/ontology/trading_after_protege.ttl` (Protégé 5.6.9 골든 — 126 triples 보존)
- `docs/onboarding/protege-setup.md` (버전 pin 5.6.4+, Turtle 저장 절차, 골든 생성 가이드)
- `docs/runbooks/graphdb-ops.md` (증상→조치 테이블, 백업/복원, Fuseki 수동 전환)
- `.github/workflows/graphdb-smoke.yml` (docker services + 2 smoke 쿼리 + p95 latency)

**수정 파일 (5)**:
- `scripts/ontology_sync.py` — `--push-graphdb`, `--endpoint`, `--repo` 옵션 + `push_to_graphdb()` (sys.path 해킹 임포트)
- `services/obsidian_mcp/tools.py` — `sparql()` 3계층 방어 + env/ctx 분기 + `source` 필드
- `docs/onboarding/mcp-setup.md` — 원격 SPARQL 엔드포인트 섹션
- `docs/runbooks/.ai.md` — graphdb-ops.md 추가
- `.gitignore` — `infra/graphdb/data/`, `infra/graphdb/logs/`

**실증 검증**:
- `docker compose up` → healthcheck 통과
- `graphdb_bootstrap.py` → T-Box 126 triples 로드
- `ontology_sync.py --push-graphdb` → `momo-btc-v2` Strategy 조회 성공
- Workbench `http://localhost:7200/sparql` → 쿼리 실행 OK
- Protégé 5.6.9 round-trip → `xsd:date a rdfs:Datatype` 1줄 추가 (benign 정규화), 원본 126 triples 전량 보존

**Trade-off / 알려진 이슈**:
- GraphDB Free 2-core 쿼리 제한 — 100 triples 규모에서 무관. 한계 도달 시 `graphdb-ops.md` 의 Fuseki 수동 전환 가이드.
- GraphDB 10.x 이미지 1.2GB → CI cold-start ~30-60s. follow-up: `actions/cache` 검토.
- Protégé 가 unused prefix 자동 제거 + `xsd:date` datatype 선언 자동 추가 — round-trip 테스트가 "additive OK, 손실 금지" 로 유연화.

**Follow-ups (별 이슈)**:
- `check_invariants.py` 에 `.ai.md` 존재 검사 추가
- GraphDB basic-auth 지원 (prod 배포 시)
- SHACL 검증을 `pyshacl` → GraphDB 내장 플러그인 통합
