---
type: work-done
id: 000054-shacl-validation-00-issue
name: "[feat] SHACL 제약 기반 고급 검증 (CI fail 모드)"
status: done
---

# feat: SHACL 제약 기반 고급 검증 (CI fail 모드)

## 사용자 관점 목표
프론트매터 "필드 존재 여부" 수준을 넘어 **도메인 의미 규칙**을 SHACL 로 강제한다. 예: "라이브 전략은 반드시 최소 1개 riskRule 을 가져야 한다", "CriticalRule 위반 Incident 는 반드시 PostMortem 연결" 같은 제약을 CI 에서 자동 검증.

## 배경
- #47 의 `check_invariants.py` 는 필드 존재·링크 무결성 수준
- 도메인 규칙 위반(예: 리스크 규칙 없는 라이브 전략) 은 현재 사람 눈으로만 잡힘
- SHACL (Shapes Constraint Language) 은 RDF 위에서 제약 정의하는 W3C 표준 → 이미 있는 `instances.ttl` 과 자연스럽게 결합

## 범위

### 포함
- `docs/ontology/shapes.ttl` — SHACL 제약 정의
- 필수 제약 최소 10종:
  1. 라이브 전략은 `risk_rules` 최소 1건
  2. 라이브 전략은 `sharpe_bt` 필수
  3. CriticalRule 위반 Incident 는 PostMortem 연결 필수 (48h 유예)
  4. Backtest 의 `period` 시작일 < 종료일
  5. Signal 의 `lookback` > 0
  6. RiskRule `threshold` 는 타입별 범위 (drawdown 0~1, position-limit 0~1)
  7. Strategy `timeframe` 은 허용 enum 내
  8. Instrument `venue` 는 허용 enum 내
  9. Incident `severity` P0 는 affected_strategies 최소 1건
  10. PostMortem `status: final` 은 `action_items` 최소 1건
- `scripts/shacl_validate.py` — pyshacl 기반 검증 러너
- `scripts/check_invariants.py` 에 SHACL 단계 통합
- CI 워크플로우에 SHACL 스텝 추가 (fail 모드)
- 위반 시 친절한 한글 에러 메시지 (shape message)
- 온보딩: `docs/onboarding/shacl-rules.md` — 규칙 추가법·리뷰 절차

### 제외
- OWL 기반 추론 (별도 이슈 고려)
- 실시간 검증 (배치·CI 만)

## 완료 기준
- [x] `docs/ontology/shapes.ttl` 에 10종 이상 SHACL 제약 정의
- [x] `pyshacl` 로 `instances.ttl` + `shapes.ttl` 검증 스크립트 동작
- [x] CI 가 SHACL 위반 시 실패 (fail 모드)
- [x] 위반 메시지가 사람이 읽기 쉬운 한국어 설명 포함
- [x] 규칙별 단위 테스트: 위반 픽스처·준수 픽스처 각각 존재 (`tests/test_shacl.py`)
- [x] `docs/onboarding/shacl-rules.md` 가이드 작성

## 구현 플랜
1. **Phase 1** — pyshacl 셋업 + 최소 3개 제약으로 파이프라인 검증
2. **Phase 2** — 10종 제약 작성 + 테스트 픽스처
3. **Phase 3** — check_invariants.py 통합
4. **Phase 4** — CI fail 모드 + 위반 샘플 메시지 톤 정돈
5. **Phase 5** — 온보딩 문서

## 개발 체크리스트
- [x] 테스트 코드 포함
- [x] docs/ontology/.ai.md 갱신
- [x] 불변식 위반 없음

## 선행 조건
- #47 머지 완료
- 이슈 B (전체 마이그레이션) 권장 — 미마이그레이션 노트가 많으면 false positive 폭증



## 작업 내역

### 2026-04-14

**현황**: 0/6 완료 — 구현 대기 (01_plan.md 상세 플랜 작성 완료)
**완료된 항목**:
- (없음)
**미완료 항목**:
- docs/ontology/shapes.ttl 에 10종 이상 SHACL 제약 정의
- pyshacl 로 instances.ttl + shapes.ttl 검증 스크립트 동작
- CI 가 SHACL 위반 시 실패 (fail 모드)
- 위반 메시지가 사람이 읽기 쉬운 한국어 설명 포함
- 규칙별 단위 테스트: 위반/준수 픽스처 (tests/test_shacl.py)
- docs/onboarding/shacl-rules.md 가이드 작성
**변경 파일**: 2개 (01_plan.md 보강, 00_issue.md 작업 내역 추가)

### 2026-04-15

**현황**: 6/6 완료 — 구현 완료 (Phase 1~5 전체 완료)
**Phase 결과**:
- Phase 1: `docs/ontology/shapes.ttl` 초기 3 shape + `scripts/shacl_validate.py` smoke 성공
- Phase 2: `trading.ttl` 에 PostMortem 클래스 + 신규 property 7종 추가, `ontology_sync.py` 신규 핸들러 통합
- Phase 3: shapes.ttl 10 shape 확정 + `tests/fixtures/shacl/` 20 픽스처 + `tests/test_shacl.py` 22 케이스 전부 통과
- Phase 4: `check_invariants.py` 에 `check_shacl()` 통합, `.github/workflows/ontology-check.yml` 에 pyshacl 설치 + verbose 스텝 추가
- Phase 5: `docs/onboarding/shacl-rules.md` + `docs/ontology/.ai.md` · `scripts/.ai.md` · `tests/.ai.md` 갱신
**검증**:
- `pytest tests/test_shacl.py tests/test_ontology_sync.py` → 24 passed
- `python scripts/check_invariants.py --strict` → exit 0 (60 노트)
- `python scripts/ontology_sync.py --check` → up-to-date
**변경 파일**: 11개 (트리당 shape/script/tests/docs 전반)
**다음 작업**: `/finish-issue` 로 커밋·PR

## 관련 노트 (구현 대상)

- [[shacl-rules]]
