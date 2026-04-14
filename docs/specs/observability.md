# 관측성 스택 스펙 (Prometheus / Grafana / Loki / 알림)

## 1. 목적
시스템·전략·체결 상태를 실시간 관측하고, 임계 조건 충족 시 즉시 알림을 발송한다.
3축(메트릭/로그/트레이스)을 명확히 분리한다.

## 2. 메트릭 네이밍 규약
- `<domain>_<noun>_<unit>` 형식. 단위는 `_seconds`, `_bytes`, `_total` (counter), `_ratio`.
- 라벨은 저카디널리티만 (`strategy`, `broker`, `symbol`은 화이트리스트 종목만).
- 파생 지표는 Recording Rule로 사전 집계.

### 2.1 최소 10종 메트릭
| # | 이름 | 타입 | 라벨 | 설명 |
|---|------|------|------|------|
| 1 | `qta_orders_total` | counter | strategy, broker, side, status | 주문 발주 건수 |
| 2 | `qta_fills_total` | counter | strategy, broker, side | 체결 건수 |
| 3 | `qta_fill_qty_total` | counter | strategy, broker, side | 체결 수량 누계 |
| 4 | `qta_pnl_current` | gauge | strategy | 현재 미실현+실현 PnL (KRW) |
| 5 | `qta_position_qty` | gauge | strategy, symbol | 포지션 수량 |
| 6 | `qta_order_latency_seconds` | histogram | broker, algo | 발주→ACK 지연 |
| 7 | `qta_market_data_lag_seconds` | gauge | source | 시세 수신 지연 |
| 8 | `qta_kill_switch_state` | gauge | reason | 1=triggered |
| 9 | `qta_strategy_signal_total` | counter | strategy, signal | 알파 시그널 발생 |
| 10 | `qta_risk_breach_total` | counter | rule, severity | 리스크 룰 위반 |

## 3. Grafana 대시보드 (3종)
각각 최소 4패널 이상.

### 3.1 system.json
- 프로세스 가용성 (up), CPU/메모리, 시세 lag, 발주 latency p50/p95/p99.

### 3.2 strategy.json
- 전략별 PnL 추이, 포지션 수, 신호 발생률, 슬리피지 분포.

### 3.3 execution.json
- 발주/체결 비율, broker별 실패율, 체결가-기준가 차이, 단일가 대기 큐 깊이.

## 4. Loki 라벨 규약
- 필수: `app`, `severity` (debug/info/warn/error/critical), `trace_id`.
- 도메인: `strategy`, `broker`, `symbol`, `algo`.
- 자세한 규약은 `loki/labels.md` 참조.

## 5. 알림 룰
- Critical: PagerDuty/Telegram 즉시 (`severity=critical` OR `qta_kill_switch_state==1`).
- Warning: 5분 배치, Slack 채널.
- 룰 정의는 Prometheus Alertmanager (`alertmanager/rules.yml`).

## 6. 트레이싱 결정
- v1: OpenTelemetry SDK 도입은 보류. `trace_id`를 로그/메트릭 익셈플러로만 전파.
- v2: OTLP exporter → Tempo 도입 검토.

## 7. 도커 스택
`docker-compose.yml`로 prometheus + grafana + loki + promtail 재현.
포트: prom=9090, grafana=3000, loki=3100.

## 8. 출처
- Prometheus naming: https://prometheus.io/docs/practices/naming/
- Grafana dashboard JSON: https://grafana.com/docs/grafana/latest/dashboards/build-dashboards/view-dashboard-json-model/
- Loki labels best practices: https://grafana.com/docs/loki/latest/get-started/labels/
- Alertmanager: https://prometheus.io/docs/alerting/latest/alertmanager/
- prometheus_client (Python): https://github.com/prometheus/client_python
