---
id: graphdb-ops
type: runbook
name: "GraphDB Free — 로컬/CI 운영 런북"
summary: GraphDB Free 로컬/CI 운영 가이드
severity: P3
---

# GraphDB Free — 로컬/CI 운영 런북

> GraphDB 는 ontology query/edit 도구로만 사용한다. 주문 실행·리스크 결정에 관여하지 않음 (CLAUDE.md 불변식 #6).
> 파일(`docs/ontology/trading.ttl`, `instances.ttl`)이 Source of Truth; GraphDB 는 derived index.

---

## 1. 개요

GraphDB Free 10.6.4 는 SPARQL 엔드포인트와 Workbench UI 를 제공해서, 로컬 개발과 CI 에서:
- T-Box (온톨로지, `trading.ttl`): 클래스·속성·제약
- A-Box (인스턴스, `instances.ttl`): 구체적 전략·신호

를 쿼리·편집할 수 있게 한다.

**Key Principles**:
1. 파일(`docs/ontology/`) 이 소스; GraphDB 는 캐시
2. Protégé 로 `trading.ttl` 편집 후 저장 → 파일에서 자동으로 prefix/comment 보존 확인
3. `scripts/ontology_sync.py --push-graphdb` 로 A-Box 만 GraphDB 에 일괄 업로드 (T-Box 는 bootstrap 시점에만)

---

## 2. 로컬 기동

### 2-1. 전제조건
- Docker Desktop 또는 `docker` + `docker compose` 설치
- 메인 또는 worktree 루트: `D:/project/quantum-trader-agent`

### 2-2. 기동 스크립트

```bash
cd D:/project/quantum-trader-agent  # 메인 또는 worktree

# 1. Docker Compose 로 GraphDB 컨테이너 기동
docker compose -f infra/graphdb/docker-compose.yml up -d

# 2. Healthcheck 대기 (최대 90초)
until curl -sf http://localhost:7200/rest/repositories > /dev/null; do
  sleep 2
done
echo "GraphDB ready"

# 3. Repository 생성 + T-Box 로드
python scripts/graphdb_bootstrap.py

# 4. A-Box (instances.ttl) 로드
python scripts/ontology_sync.py --write
python scripts/ontology_sync.py --push-graphdb

# 5. Workbench 접속 확인
# 브라우저에서 http://localhost:7200 열기
```

### 2-3. 종료

```bash
docker compose -f infra/graphdb/docker-compose.yml down
```

포트 `7200` 을 계속 점유하려면 `--volumes` 플래그 생략 (데이터 유지).
전체 삭제: `docker compose ... down --volumes`

---

## 3. 증상 → 원인 → 조치

| 증상 | 확인 명령 | 조치 |
|---|---|---|
| `docker compose up` 60초 내 healthcheck 실패 | `docker logs qta-graphdb` 또는 `docker ps` 에서 상태 확인 | JVM heap 초과 → `.env` 또는 `docker-compose.yml` 의 `GDB_JAVA_OPTS=-Xmx1g` 로 축소; 또는 시스템 메모리 부족 |
| `curl http://localhost:7200` 연결 거부 | `lsof -i :7200` (macOS) / `netstat -ano \| findstr 7200` (Windows) | 포트 7200 이미 점유 → `GDB_PORT=7201` 환경변수로 override 후 재기동 |
| Bootstrap `403 Forbidden` (EULA) | Workbench UI 방문 → `Accept EULA` 대화상자 | 일회성 EULA 동의; `infra/graphdb/data/` 삭제 후 재기동하면 다시 나타남 |
| SPARQL 쿼리 500 에러 | `curl http://localhost:7200/repositories/qta/size` → 0 응답 | T-Box 미로드 → `python scripts/graphdb_bootstrap.py` 재실행 |
| Workbench "Free 2-core 쿼리 제한" 경고 | Workbench → System 탭 → Cluster 정보 | vault 규모 (≈100 triples) 에서는 무관; 초과 시 **§ 5 Fuseki 수동 전환** 참조 |
| `ontology_sync.py --push-graphdb` 404 | `curl http://localhost:7200/rest/repositories/qta` → 404 | repo 없음 → `python scripts/graphdb_bootstrap.py` 먼저 실행 |
| MCP tool 에서 `QTA_SPARQL_ENDPOINT` 사용 시 원격 실패 | `echo $QTA_SPARQL_ENDPOINT` / `curl -I http://localhost:7200` | GraphDB 미기동 또는 endpoint URL 오타 → env 를 unset 하면 로컬 rdflib 로 자동 폴백 |

---

## 4. 백업 / 복원

### 4-1. 백업

```bash
# GraphDB 컨테이너 내에서 실행 (RDF N-Quads 형식)
docker exec qta-graphdb /opt/graphdb/bin/backup \
  -r qta \
  -f /data/backup.nq.gz

# 호스트로 복사
docker cp qta-graphdb:/data/backup.nq.gz ./backup-$(date +%Y%m%d-%H%M%S).nq.gz
```

### 4-2. 복원

```bash
# 1. GraphDB 중지
docker compose -f infra/graphdb/docker-compose.yml down

# 2. 기존 데이터 제거
rm -rf infra/graphdb/data/*

# 3. 재시작 + bootstrap
docker compose -f infra/graphdb/docker-compose.yml up -d
python scripts/graphdb_bootstrap.py

# 4. Workbench 에서 Import
#    Workbench → Import → [N-Quads 선택] → 백업 파일 선택 → Import
#    또는 curl:
curl -X POST \
  -H "Content-Type: application/n-quads" \
  --data-binary @backup-20260416.nq.gz \
  http://localhost:7200/repositories/qta/statements
```

---

## 5. Fuseki 수동 전환 가이드 (CI 미포함)

GraphDB Free 의 2-core 제약이 실제 문제가 될 경우 (현재 vault 규모에선 무관), 수동으로 Apache Jena Fuseki 로 전환할 수 있다.
**주의**: 이 경로는 CI 에서 검증되지 않으므로, 개발/테스트 환경에서만 사용.

### 5-1. Fuseki 기동

```bash
docker run --rm -d --name qta-fuseki \
  -p 3030:3030 \
  -v $(pwd)/infra/fuseki-data:/fuseki \
  stain/jena-fuseki

# 대시보드: http://localhost:3030
# 기본 인증: admin / admin (또는 FUSEKI_PASSWORD env 변수 설정)
```

### 5-2. 데이터셋 생성

Fuseki UI (http://localhost:3030) 에서:
1. "Manage datasets"
2. "Create New Dataset" → 이름: `qta`, Type: **Persistent (TDB2)**

또는 curl:

```bash
curl -u admin:admin -X POST 'http://localhost:3030/$/datasets' \
  -d 'dbName=qta' -d 'dbType=tdb2'
```

### 5-3. T-Box 로드

```bash
curl -u admin:admin -X POST \
  -H 'Content-Type: text/turtle' \
  --data-binary @docs/ontology/trading.ttl \
  http://localhost:3030/qta/data
```

### 5-4. A-Box 로드

```bash
curl -u admin:admin -X POST \
  -H 'Content-Type: text/turtle' \
  --data-binary @docs/ontology/instances.ttl \
  http://localhost:3030/qta/data
```

### 5-5. MCP 엔드포인트 변경

```bash
export QTA_SPARQL_ENDPOINT=http://localhost:3030/qta/query
```

또는 `docs/.obsidian/mcp-config.json`:

```json
{
  "sparql_endpoint": "http://localhost:3030/qta/query"
}
```

### 5-6. graphdb_client.py 호환성 주의

`scripts/graphdb_client.py` 의 다음 함수들은 GraphDB REST API 기반이므로 **Fuseki 엔드포인트에 직접 사용 불가**:
- `upload_ttl()` — GraphDB `/statements` 엔드포인트
- `sparql_update()` — GraphDB `/statements` 엔드포인트

Fuseki 에 SPARQL Update 를 실행하려면 `/update` 엔드포인트를 사용하거나, curl 로 직접:

```bash
curl -u admin:admin -X POST \
  -H 'Content-Type: application/sparql-update' \
  --data 'CLEAR DEFAULT; INSERT DATA { ... }' \
  http://localhost:3030/qta/update
```

---

## 6. 환경변수 우선순위

MCP 의 `sparql()` 도구가 SPARQL 엔드포인트를 결정하는 순서:

1. **`ctx.sparql_endpoint`** (mcp-config.json 의 `sparql_endpoint` 키) — 프로그램적 최우선
2. **`QTA_SPARQL_ENDPOINT`** 환경변수
3. **로컬 rdflib** — 둘 다 없거나 원격 실패 시 폴백 (silent)

예시:

```bash
# 원격 사용
export QTA_SPARQL_ENDPOINT=http://localhost:7200/repositories/qta
python -c "from services.obsidian_mcp.tools import sparql; ..."

# 로컬 사용 (env 를 비워야 함)
unset QTA_SPARQL_ENDPOINT
python -c "from services.obsidian_mcp.tools import sparql; ..."
```

---

## 7. TTL drift 방지

GraphDB 는 derived cache 이므로, 파일과의 불일치를 방지하려면:

1. **편집 흐름**:
   - Protégé 에서 `docs/ontology/trading.ttl` 편집
   - 저장 시 `format: Turtle, charset: UTF-8, line ending: LF` 고정
   - 파일 저장 후 `git diff` 로 의도한 변경 확인

2. **동기화 흐름**:
   ```bash
   # (Protégé 편집 후 저장)
   python scripts/ontology_sync.py --write          # TTL integrity check
   python scripts/ontology_sync.py --push-graphdb   # GraphDB 와 동기화
   ```

3. **재동기화가 필요할 때**:
   ```bash
   # GraphDB 의 default graph 를 파일에서 전체 재로드
   python scripts/ontology_sync.py --push-graphdb --endpoint http://localhost:7200
   ```
   (`--push-graphdb` 는 `CLEAR DEFAULT; INSERT DATA {...}` 로 원자적 교체)

4. **GraphDB 에서 pull 하지 않음**:
   - `ontology_sync.py` 는 **push-only** 방식
   - GraphDB 에서 직접 쿼리로 수정한 것은 파일에 반영되지 않음
   - 모든 변경은 파일(`trading.ttl`, `instances.ttl`) 을 통해서만

---

## 8. 참조

- `docs/onboarding/protege-setup.md` — Protégé 5.6.4 설치·저장 설정 가이드
- `docs/onboarding/mcp-setup.md` — MCP 서버 설정 및 원격 SPARQL 엔드포인트 구성
- `infra/graphdb/docker-compose.yml` — 로컬 GraphDB 컨테이너 구성
- `scripts/graphdb_bootstrap.py` — GraphDB repo 생성 + T-Box 로드
- `scripts/ontology_sync.py` — TTL 파일 동기화 및 GraphDB push
- `scripts/graphdb_client.py` — GraphDB REST API 래퍼
- `.github/workflows/graphdb-smoke.yml` — CI 스모크 테스트 workflow
- `docs/work/active/000055-graphdb-integration/01_plan.md` — 구현 계획 및 설계 문서
