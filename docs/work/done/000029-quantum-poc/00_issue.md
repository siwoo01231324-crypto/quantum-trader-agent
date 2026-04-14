---
type: work-done
id: 000029-quantum-poc-00-issue
name: "[research] 양자 PoC 설계 (Phase 4 옵션, QAOA 포트폴리오 최적화)"
status: done
---

# [research] 양자 PoC 설계 (Phase 4 옵션, QAOA 포트폴리오 최적화)

## 목적
Phase 4(선택)로 예정된 양자 포트폴리오 최적화 PoC의 실험 설계를 선제적으로 확정한다.

## 배경
양자 컴포넌트는 현재 메인 경로에서 제외됐으나(#5 결정), PoC는 별도 트랙으로 유지할 가치. 설계를 먼저 해두면 기회 비용 최소화.

## 완료 기준
- [ ] Qiskit Finance QAOA 50종목 포트폴리오 최적화 벤치마크 설계 (목적함수·제약·큐비트 수)
- [ ] 고전 baseline (Markowitz / CVXPY) 정의
- [ ] 성공 지표·기각 기준 1문장씩 명시 (NISQ 한계 포함)
- [ ] 예상 비용(IBM Quantum 크레딧·시간) 추정
- [ ] `docs/background/14-quantum-poc-design.md`

## 구현 플랜
1. IBM Quantum·Qiskit 최신 튜토리얼 참조
2. 하드웨어 선택 (heron/eagle/시뮬레이터)

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화


## 작업 내역

