---
type: work-done
id: 000114-tca-impl-shortfall-00-issue
name: "feat: Implementation Shortfall 사전 추정 + TCA 메트릭 (특허 #84-4 차용)"
status: active
---

# #114 Implementation Shortfall 사전 추정 + TCA 메트릭 (특허 #84-4 차용)

## 범위

- `src/brokers/is_estimator.py` — `pre_flight_is_estimate()` + `realized_is()` + `MarketSnapshot`
- `src/observability/metrics.py` — IS 메트릭 3종 추가
- `src/brokers/router.py` — `place_order()` 에서 pre-flight 호출 + 메트릭 emit
- `src/backtest/metrics.py` — `avg_is_bps` 컬럼 추가
- `tests/test_is_estimator.py` — TDD 수치 검증
- `src/brokers/.ai.md` — 갱신

## 구현 계획

### IS 공식 (Perold 1988 단순 파라메트릭)
```
IS_est_bps = (spread_bps / 2) + market_impact_coeff * sqrt(qty / adv) * 10000
realized_IS_bps = (fill_price - arrival_price) / arrival_price * 10000  (BUY)
                = (arrival_price - fill_price) / arrival_price * 10000  (SELL)
```

### 특허 회피
- BlackRock US12067619B1 (c)(d)에서 측정·로깅만 차용
- (b) "실행 스타일 확률 (Auto/RFQ/Voice)" 미채택
- IS 수식: BlackRock 방식과 다른 단순 파라메트릭

## AC 체크리스트
- [ ] pre_flight_is_estimate 함수 구현
- [ ] BrokerFill 후처리에서 실현 IS 계산 + 메트릭 송출
- [ ] qta_is_estimate_bps, qta_is_realized_bps, qta_is_prediction_error_bps 메트릭
- [ ] tests/test_is_estimator.py
- [ ] avg_is_bps 백테스트 리포트 컬럼
- [ ] src/brokers/.ai.md 갱신
