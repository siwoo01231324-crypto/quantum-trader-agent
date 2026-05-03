---
id: 000110-partial-fill
type: work-active
title: "feat: Partial fill 지원 (MockMatchingEngine partial_fill_enabled=True)"
issue: 110
---

## 배경

MockMatchingEngine은 `partial_fill_enabled=False` 기본 — 즉시 100% 체결.
Phase 2+ 사실적 시뮬을 위해 ADV 기반 확률적 부분 체결 활성화.

## AC

- [ ] `partial_fill_enabled=True` 모드 구현 + 단위 테스트
- [ ] PaperBroker의 부분 체결 처리 (cancel 정책 결정)
- [ ] 결정적 시드 재현성 검증
- [ ] 통합 테스트: 큰 주문 → 부분 체결 시퀀스 → 누적 fill_qty == order.qty 검증
- [ ] `src/execution/.ai.md` 갱신

## 설계 결정

- ADV 기반 확률적 분할: `order.qty / adv` 비율로 fill 수 결정
- `seed` 파라미터로 결정적 재현성 보장
- 각 fill마다 trade_id 분리 (idempotency 보장)
- PaperBroker cancel 정책: partial fill 시 잔여 즉시 취소 (Phase 1 단순화)
- 기본값 False 유지 → #80 회귀 없음
