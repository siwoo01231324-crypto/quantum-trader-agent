# 01_plan — Obsidian 볼트 MCP 서버 노출 (#51)

> ⚠️ `/plan 51` 로 상세 구현 계획 보강 필요.

## 완료 기준 (AC)
- [ ] `mcp-server-obsidian` 커맨드로 서버 기동 가능
- [ ] Claude Code 에서 `@docs` 도구로 노트 조회·검색·SPARQL 질의 성공
- [ ] 쓰기 도구는 기본 dry-run, `--write` 플래그 필요
- [ ] `tests/test_obsidian_mcp.py` 최소 5개 케이스 (read/list/search/write/sparql) 통과
- [ ] `docs/onboarding/mcp-setup.md` + `services/obsidian_mcp/.ai.md` 완비
- [ ] CI 스모크 테스트 통과

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] services/obsidian_mcp/.ai.md 작성
- [ ] 불변식 위반 없음

## Phase
1. MCP SDK 서버 골격 (services/obsidian_mcp/server.py)
2. 읽기 도구 4종 (read_note/list_notes/search/graph_neighbors)
3. SPARQL 도구 (rdflib + ontology_sync 재사용)
4. 쓰기 도구 (write_note/append_section) + 안전장치
5. Claude Code 등록 + 통합 테스트 + 온보딩

## 관련 노트 (구현 대상)

- [[mcp-setup]]
