---
type: work-done
id: 000024-risk-dsl-00-issue
name: "[feat] 리스크 룰 DSL 설계 (YAML 기반 한도 정책)"
status: done
---

# [feat] 리스크 룰 DSL 설계 (YAML 기반 한도 정책)

## 사용자 관점 목표
매매 한도·손실 한도·포지션 한도를 YAML로 선언해 전략 코드와 분리된 리스크 룰을 운영한다.

## 배경
리스크 로직이 전략 코드에 묶이면 긴급 한도 조정이 불가하다. 선언적 DSL로 분리 필수.

## 완료 기준
- [ ] YAML 스키마 정의 (per-trade / per-day / per-portfolio / sector-level / drawdown)
- [ ] 파서 + 검증기 구현 (`src/risk/dsl.py` 스텁)
- [ ] 예시 정책 3종 (보수 / 중립 / 공격) YAML 파일 첨부
- [ ] 단위 테스트 (invalid YAML 거부, breach 감지)
- [ ] `docs/specs/risk-rule-dsl.md`

## 구현 플랜
1. JSON Schema로 먼저 스키마 정의
2. pydantic 기반 파싱·검증
3. 런타임 평가 함수 인터페이스 설계

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음


## 작업 내역

