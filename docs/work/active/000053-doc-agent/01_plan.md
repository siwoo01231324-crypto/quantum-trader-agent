# 01_plan — LLM 에이전트 자동 노트 생성 (#53)

> ⚠️ `/plan 53` 로 상세 구현 계획 보강 필요.

## 완료 기준 (AC)
- [ ] 백테스트 JSON 샘플 입력 → `docs/work/done/backtests/bt-*.md` 초안 생성
- [ ] 모의 인시던트 이벤트 입력 → `docs/work/incidents/inc-*.md` 초안 생성
- [ ] 초안은 `.draft.md` 확장자, CI 에서 정식 노트로 오인 안 함
- [ ] 생성된 초안이 스키마(note-schemas.md) 위반 없음
- [ ] 프롬프트·에이전트 정의 문서화 (`.ai.md` 포함)
- [ ] `tests/test_doc_agent.py` 통과

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] services/doc_agent/.ai.md 작성
- [ ] 불변식 위반 없음

## Phase
1. 백테스트 초안 생성 (가장 구조화)
2. 인시던트 초안
3. 포스트모템 초안 (백링크 자동 수집)
4. Claude Code 서브에이전트 통합 (.claude/agents/doc-writer.md)
5. 감사 로그 + CI 가드 (.draft.md 허용 규칙)

## 선행
- #51 (MCP 서버) — 완료 후 쓰기 도구 활용 예정. 병렬 개발 중엔 MCP 없이 파일 직접 쓰기로 골격 완성

## 관련 노트 (구현 대상)

- [[mcp-setup]]
- [[frontmatter-guide]]
