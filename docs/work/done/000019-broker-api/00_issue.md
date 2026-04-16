---
type: work-done
id: 000019-broker-api-00-issue
name: "[research] 브로커 Open API 비교·선정 (KIS / 키움 / LS)"
status: done
---

# [research] 브로커 Open API 비교·선정 (KIS / 키움 / LS)

## 목적
KIS / 키움 / LS증권의 Open API를 실증적으로 비교하여 본 프로젝트의 1차 브로커 채널과 fallback 2차 채널을 확정한다.

## 배경
Phase 1에서 선정한 "저빈도 규칙기반 퀀트 자동매매" 정체성상 한국 개인 접근 가능한 브로커 API 중 가장 안정적인 채널 확정이 모든 실행 레이어의 전제다.

## 완료 기준
- [ ] KIS / 키움 / LS증권 Open API 비교표 (인증·주문·체결·시세·레이트리밋·수수료·모의계좌·SDK 상태·활성 커뮤니티)
- [ ] 1차 브로커 1개 선정 + 근거 1문단 (레퍼런스 URL 포함)
- [ ] 2차 후보(Fallback) 1개 지정 + 전환 트리거 조건 정의
- [ ] `docs/background/10-broker-api-comparison.md` 작성 (출처 URL 명시)

## 구현 플랜
1. 각 브로커 공식 문서·GitHub 샘플 수집
2. 실제 모의계좌 가입·토큰 발급 난이도 기록
3. 선정 기준(안정성·샘플·제약) 가중치 적용

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화


## 작업 내역

## 관련 노트 (구현 대상)

- [[10-broker-api-comparison]]
