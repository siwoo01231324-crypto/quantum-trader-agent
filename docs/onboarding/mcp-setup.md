---
type: onboarding
id: mcp-setup
name: Obsidian 볼트 MCP 서버 — 셋업 가이드
---

# Obsidian 볼트 MCP 서버 — 셋업 가이드

`services/obsidian_mcp/` 가 제공하는 stdio MCP 서버를 Claude Code / 외부 LLM 에
연결해서, 볼트 (`docs/`) 를 "도구(MCP tool)" 로 읽고 쓰는 방법이다.

## 1. 의존성 설치

```bash
pip install mcp rdflib python-frontmatter PyYAML pytest
```

- `mcp` — Python MCP SDK. stdio 프로토콜 구현체.
- `rdflib` — `sparql` tool 용 (ontology 파싱 + SPARQL)
- `python-frontmatter`, `PyYAML` — 프론트매터 파서

> SDK 가 없어도 `tools.py` 의 함수는 단독 동작한다 (`pytest` 는 SDK 없이 통과).

## 2. 로컬 실행

```bash
# 기본 (쓰기는 dry-run)
python -m services.obsidian_mcp.server

# 실쓰기 허용
python -m services.obsidian_mcp.server --write
# 또는 환경변수
OBSIDIAN_MCP_ALLOW_WRITE=1 python -m services.obsidian_mcp.server

# SDK 없이 도구만 점검
python -m services.obsidian_mcp.server --selftest
```

옵션:

| 옵션 | 설명 |
|------|------|
| `--config PATH` | 설정 파일 경로 (기본 `docs/.obsidian/mcp-config.json`) |
| `--vault-root PATH` | 볼트 루트 오버라이드 |
| `--write` | 실쓰기 활성화 (기본 dry-run) |
| `--selftest` | MCP SDK 없이 도구 디스패처 점검 |

## 3. Claude Code 등록

`.claude/mcp.json` (프로젝트 루트 또는 `~/.claude/`) 에 추가:

```json
{
  "mcpServers": {
    "obsidian-vault": {
      "command": "python",
      "args": ["-m", "services.obsidian_mcp.server"],
      "cwd": "${workspaceFolder}",
      "env": {
        "PYTHONUNBUFFERED": "1"
      }
    },
    "obsidian-vault-write": {
      "command": "python",
      "args": ["-m", "services.obsidian_mcp.server", "--write"],
      "cwd": "${workspaceFolder}",
      "env": {
        "PYTHONUNBUFFERED": "1",
        "OBSIDIAN_MCP_ALLOW_WRITE": "1"
      }
    }
  }
}
```

- 조회/생성 두 서버를 분리해 등록하면, 실쓰기 버전에만 권한 프롬프트를 달 수 있다.
- Windows PowerShell 환경에서는 `"command": "py"` 또는 `"command": "python.exe"` 로
  바꿔야 할 수 있다.

## 4. 노출 도구

| tool | 쓰기 | 설명 |
|------|------|------|
| `read_note(id)` | - | 프론트매터 + body |
| `list_notes(type?, tag?, path_prefix?)` | - | 요약 리스트 |
| `search(query)` | - | 풀텍스트·태그·위키링크 |
| `write_note(id, frontmatter, body, create_if_missing?)` | dry-run 기본 | 신규/갱신 |
| `append_section(id, heading, content)` | dry-run 기본 | 섹션 추가 |
| `sparql(query)` | - | `trading.ttl + instances.ttl` SPARQL |
| `graph_neighbors(id, depth=1)` | - | 백링크 + 아웃링크 |

쓰기 기본값은 **dry-run** — 결과의 `dry_run: true` 필드로 구분된다.

## 5. 설정 파일 (`docs/.obsidian/mcp-config.json`)

| key | 의미 |
|-----|------|
| `vault_root` | 볼트 루트 (디폴트 `docs`) |
| `allowed_paths` | 쓰기 허용 경로 프리픽스 화이트리스트 (`vault_root` 기준 상대경로) |
| `write_mode` | `"dry-run"` \| `"enabled"` |
| `sparql_endpoint` | 외부 SPARQL endpoint (null 이면 로컬 ttl 사용) |

화이트리스트에 없는 경로에 쓰려 하면 `ok: false` + `error: path not in allowed_paths` 로 거절.

## 6. 운영 주의사항

- 실쓰기 활성화 상태에서는 **커밋 전 반드시 `git diff` 로 변경 확인**.
- 온톨로지 일관성은 `scripts/ontology_sync.py --check` + `scripts/check_invariants.py`
  로 검증. MCP 도구가 프론트매터를 바꿨다면 `ontology_sync.py --write` 를 다시 돌려야 한다.
- CI smoke 는 `.github/workflows/mcp-smoke.yml` 이 `tests/test_obsidian_mcp.py` 를 실행.

## 7. 원격 SPARQL 엔드포인트 (GraphDB)

`sparql` 도구는 3-layer fallback 으로 실행 경로를 결정한다:

1. **ctx 우선** — `ctx.sparql_endpoint` (mcp-config.json 의 `sparql_endpoint` 키) 가 설정되면 해당 URL 사용
2. **env 차선** — `QTA_SPARQL_ENDPOINT` 환경변수가 비어 있지 않으면 해당 URL 사용
3. **로컬 fallback** — 둘 다 없으면 `vault_root/ontology/trading.ttl + instances.ttl` 를 rdflib 로 로드

### 환경변수 설정

```bash
export QTA_SPARQL_ENDPOINT=http://localhost:7200/repositories/qta
```

### 프로그램적 설정 (`docs/.obsidian/mcp-config.json`)

```json
{
  "sparql_endpoint": "http://localhost:7200/repositories/qta"
}
```

### 우선순위 요약

| 우선순위 | 설정 방법 | `result["source"]` |
|----------|-----------|---------------------|
| 1 (높음) | `ctx.sparql_endpoint` | `"remote-http"` |
| 2 | `QTA_SPARQL_ENDPOINT` env | `"remote-http"` |
| 3 (낮음) | 없음 (로컬 ttl) | `"local-rdflib"` |

### 안전 쿼리 필터

SELECT / ASK / DESCRIBE / CONSTRUCT 만 허용. INSERT / UPDATE / DELETE 등 쓰기 쿼리는 `ValueError` 로 차단된다 (CLAUDE.md 불변식 #6).

### 참조

- GraphDB 운영 절차: `docs/runbooks/graphdb-ops.md` (worker-5 작성 중)
