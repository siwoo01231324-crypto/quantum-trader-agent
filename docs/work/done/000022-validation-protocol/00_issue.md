# [research] 백테스트 검증 프로토콜 (walk-forward·purged K-fold)

## 목적
데이터 스누핑·오버피팅을 방지하는 백테스트 검증 방법론을 고정한다.

## 배경
LFT 퀀트의 치명적 실패 모드는 "과거에선 좋았는데 운영에선 깨진다". walk-forward·purged K-fold 등 통계 가드레일이 필수.

## 완료 기준
- [ ] walk-forward / purged K-fold / anchored vs rolling 설명·수식
- [ ] 데이터 스누핑 / survivorship bias / look-ahead bias 방지 체크리스트
- [ ] 본 프로젝트 적용 SOP(표준 절차) 1쪽
- [ ] 검증 실패 시 롤백 기준 정의
- [ ] `docs/background/12-validation-protocol.md`

## 구현 플랜
1. 주요 레퍼런스: López de Prado "Advances in Financial ML"
2. Phase 1 규칙기반 전략에 적용 예시 포함

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화


## 작업 내역

