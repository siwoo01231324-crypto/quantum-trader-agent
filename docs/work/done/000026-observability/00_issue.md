# [feat] 관측성 스택 (Prometheus/Grafana/Loki/알림)

## 사용자 관점 목표
시스템/전략/체결 상태를 실시간 관측하고 이상 시 즉시 알림을 받는다.

## 배경
관측성 없는 자동매매는 블라인드 드라이빙이다. Phase 1 파이프라인과 동시에 깔아야 한다.

## 완료 기준
- [ ] Prometheus 메트릭 네이밍 규약 + 최소 10종 메트릭 정의 (orders_total, fills_total, pnl_current, latency_ms 등)
- [ ] Grafana 대시보드 JSON 3종 (시스템/전략/체결)
- [ ] Loki 로그 라벨 규약 (trace_id, strategy, broker, severity)
- [ ] Telegram/Slack 알림 룰 (Critical = 즉시, Warning = 배치)
- [ ] docker-compose로 스택 재현 가능
- [ ] `docs/specs/observability.md`

## 구현 플랜
1. 메트릭·로그·트레이스 3축 명확히 분리
2. OpenTelemetry 사용 여부 결정

## 개발 체크리스트
- [ ] 테스트 코드 포함 (메트릭 방출 검증)
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음


## 작업 내역

