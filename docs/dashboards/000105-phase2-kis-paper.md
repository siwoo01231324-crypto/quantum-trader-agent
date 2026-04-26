# Phase 2 KIS 모의계좌 — Grafana 대시보드 스펙

> Issue #105 · Stage 5.4 · 작성: 2026-04-26
> Grafana 패널 6종 정의. 각 패널의 PromQL 쿼리, 임계값, alert 조건, 운영자 1차 대응 가이드 포함.
> 메트릭 등록: `src/observability/metrics.py` (Stage 1.1, KRW 메트릭 9종)

---

## 패널 1 — KRW PnL / Equity 실시간

**목적**: 전략별 KRW 기준 실현+미실현 PnL 및 총자산을 실시간 추적.

| 항목 | 값 |
|------|-----|
| 패널 타입 | Time series (multi-line) |
| 단위 | KRW (₩) |

**PromQL**

```promql
# 전략별 KRW PnL
sum by (strategy) (qta_paper_pnl_krw)

# 총 KRW 자산
sum by (strategy) (qta_paper_equity_krw)
```

**임계값**: 낙폭 -5% (인포) / -10% (경고) — `qta_paper_drawdown_ratio` 패널과 연동

**운영자 가이드**: PnL 급락 시 `qta_paper_kis_tracking_error` (패널 3)와 함께 확인. tracking error > 0.5% 연속이면 R3 트리거 검토.

---

## 패널 2 — KIS API 5xx 오류율 (R1 임계값 시각화)

**목적**: KIS REST API 5xx 오류율 모니터링. 15분 윈도우 10% 초과 시 R1 트리거 → daemon halt + PaperBroker 폴백.

| 항목 | 값 |
|------|-----|
| 패널 타입 | Stat + Time series |
| 단위 | % |
| Alert 임계값 | 10% (15분 윈도우) |

**PromQL**

```promql
# KIS 5xx 오류율 (15분 윈도우)
sum(rate(qta_broker_request_latency_seconds_count{broker="kis", status_class="5xx"}[15m]))
  /
sum(rate(qta_broker_request_latency_seconds_count{broker="kis"}[15m]))
* 100
```

**Alert 조건**: `value > 10` for 15 minutes → severity=critical

**운영자 가이드**:
1. KIS 모의투자 API 상태 페이지 확인 (https://apiportal.koreainvestment.com/)
2. `qta_broker_keepalive_failure_total{broker="kis"}` 확인 — 토큰 만료 여부
3. R1 trip 시 자동 PaperBroker 폴백 확인 (`mode_switched` WAL event)
4. 오류 해소 후 수동으로 daemon 재시작

---

## 패널 3 — Tracking Error Daily (R3 임계값)

**목적**: KIS 체결가 vs MockMatchingEngine 시뮬 체결가 괴리율 모니터링 (AC4). 5분 연속 0.5% 초과 시 R3 트리거.

| 항목 | 값 |
|------|-----|
| 패널 타입 | Gauge + Time series |
| 단위 | % (ratio × 100) |
| Alert 임계값 | 0.5% (5분 연속) |

**PromQL**

```promql
# 일일 tracking error (mean |kis_fill - sim_fill| / sim_fill)
qta_paper_kis_tracking_error * 100
```

**Alert 조건**: `value > 0.5` for 5 minutes → severity=warning; escalate to critical if > 1.0%

**운영자 가이드**:
1. `tracking_sample` WAL 이벤트에서 구체적 주문 확인
2. KIS 체결 지연 vs 시장가 변동 구분 (slippage 모델 미활성 — #109 참조)
3. R3 trip 시 자동 PaperBroker 폴백 → `mode_switched` WAL 확인
4. 결측 fill (`qta_kis_fill_missing_total`) 급증 시 R2 트리거도 점검

---

## 패널 4 — WS 재연결 횟수

**목적**: KIS WebSocket 단절/재연결 빈도 추적 (AC6). 비정상적 급증은 네트워크 불안정 또는 KIS WS 서버 이슈 신호.

| 항목 | 값 |
|------|-----|
| 패널 타입 | Time series + Stat (1h 누계) |
| 단위 | 횟수/시간 |
| 참고 임계값 | 5회/시간 (경고) |

**PromQL**

```promql
# KIS WS 재연결 횟수 (1시간 증분)
increase(qta_broker_ws_reconnect_total{broker="kis"}[1h])
```

**운영자 가이드**:
1. backoff 정책 확인: base 1s, max 10s, jitter ±0.2 (async_ws.py:36-38)
2. 단절 이유(`reason` 라벨) 별 분류 — 정상 keepalive vs 비정상 disconnect 구분
3. 지속 재연결 시 R4 트리거(`qta_broker_keepalive_failure_total` 연속 3회) 연계 확인

---

## 패널 5 — Rate-Limit Hit 횟수

**목적**: AsyncOrderRouter token-bucket 의 KIS 2 RPS 제한 위반 감지. 빈번한 hit 는 전략 발주 빈도 과다 신호.

| 항목 | 값 |
|------|-----|
| 패널 타입 | Time series |
| 단위 | 횟수/분 |
| 참고 임계값 | 3회/분 (조정 검토) |

**PromQL**

```promql
# KIS rate-limit hit 빈도 (5분 이동평균)
rate(qta_broker_rate_limit_hit_total{broker="kis"}[5m]) * 60
```

**운영자 가이드**:
1. 발주 전략 파라미터 점검 — 신호 빈도 축소 고려
2. `paper=true` 환경에서 2 RPS 제한; `paper=false` 는 20 RPS (`.env.example` 참조)
3. REJECTED ack 비율(`qta_orders_placed_total{status="REJECTED"}`) 과 함께 확인

---

## 패널 6 — KIS 토큰 TTL 카운트다운

**목적**: KIS access token 잔여 유효시간 모니터링. 5분 이하 시 선갱신 트리거 여부 확인.

| 항목 | 값 |
|------|-----|
| 패널 타입 | Gauge (countdown) |
| 단위 | 초 (s) |
| Alert 임계값 | 300초 이하 (선갱신 미작동 시 경고) |

**PromQL**

```promql
# KIS access token 잔여 TTL (초)
qta_kis_token_ttl_seconds
```

**Alert 조건**: `value < 60` → severity=critical (토큰 만료 임박, 선갱신 미작동)

**운영자 가이드**:
1. `_should_renew` 5분 전 로직 정상 동작 확인 (auth.py)
2. cross-process lock (`.omc/state/kis_token_paper.lock`) 확인 — 잠금 해제 대기 중인지 점검
3. 토큰 갱신 실패 연속 3회 시 R4 트리거 → daemon halt

---

## 운영 참조

| 롤백 트리거 | 메트릭 | 자동 액션 |
|-------------|--------|-----------|
| R1: KIS 5xx > 10% (15분) | 패널 2 | PaperBroker 폴백 자동 |
| R2: 체결 누락 ≥ 1건 (1시간) | `qta_kis_fill_missing_total` | daemon halt only |
| R3: tracking error > 0.5% (5분) | 패널 3 | PaperBroker 폴백 자동 |
| R4: 토큰 갱신 실패 연속 3회 | 패널 6 | daemon halt only |
| R5: 모의 잔고 불일치 > 1% | `live_report.py` 일일 diff | daemon halt only |

**관련 문서**:
- 구현 계획: `docs/work/active/000105-phase2-paper-live/01_plan.md` §7
- 메트릭 카탈로그: `src/observability/.ai.md`
- 롤백 프로토콜: `docs/background/29-paper-to-live-protocol.md` §3.4
