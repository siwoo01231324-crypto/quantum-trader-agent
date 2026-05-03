# feat: OrderRouter 비용 기반 동적 라우팅 (특허 #84-2 차용)

Issue: #112

## 배경

#80 의 특허 차용 4건 중 2번. `docs/background/34-patents-execution-algos.md` §3 의 💎 제안 — CME Group US11164248B2 (활성) 의 (b) 구성요소 차용 — "best execution platform" 선택 개념.

현재 `src/brokers/router.py::OrderRouter` 는 단일 active broker 만 사용. 다중 브로커 등록 시 실시간 레이턴시·수수료·슬리피지 추정치 기반 라우팅 점수로 자동 선택.

## 완료 기준

- [x] `ExecutionCostEstimator` 구현 + 단위 테스트
- [x] `OrderRouter` 통합
- [x] `AsyncOrderRouter` 통합
- [x] `tests/brokers/test_order_router_cost.py` — mock 브로커 2개로 슬리피지 차이 시나리오 → 라우팅 선택 검증
- [x] `src/brokers/.ai.md` 갱신
