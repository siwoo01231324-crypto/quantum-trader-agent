---
type: work-done
id: 000027-kill-switch-00-issue
name: "[feat] Kill Switch & DR 런북"
status: done
---

# [feat] Kill Switch & DR 런북

## 사용자 관점 목표
치명적 상황에서 시스템을 즉시 중단하고 안전하게 복구할 수 있는 kill switch와 DR 런북을 갖춘다.

## 배경
자동매매의 최악 시나리오(무한 주문 루프·잘못된 신호 폭주)에서 중단 지연은 직접 손실로 이어진다.

## 완료 기준
- [ ] 자동 트리거 3개 이상 (일일 DD 한도 초과, API 연속 오류 N회, 의심 체결 패턴)
- [ ] 수동 kill 인터페이스 (CLI, Telegram bot, 대시보드 버튼)
- [ ] 포지션 청산 런북 (시장가 vs TWAP 청산 결정 기준)
- [ ] 복구 체크리스트 (로그 보존·재시작·검증·재개)
- [ ] Dry-run 모드 테스트 케이스
- [ ] `docs/specs/kill-switch-dr.md` + `src/ops/kill_switch.py` 스텁

## 구현 플랜
1. Kill switch는 주문 직전 게이트에 강제 경유
2. 수동·자동 경로 모두 동일 코드 경로 사용

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음


## 작업 내역

