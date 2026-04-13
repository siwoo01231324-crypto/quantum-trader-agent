# Loki 로그 라벨 규약

## 원칙
- 라벨은 저카디널리티 (수십~수백). 고카디널리티 값(주문ID, trace_id)은 라벨이 아닌 본문에 둔다.
- 모든 로그 라인은 JSON 1줄 (`promtail` json 파서 사용).

## 필수 라벨
| 라벨 | 예시 | 비고 |
|------|------|------|
| `app` | `qta-engine`, `qta-broker`, `qta-strategy` | 서비스명 |
| `severity` | `debug` `info` `warn` `error` `critical` | RFC5424 부분집합 |
| `env` | `dev` `staging` `prod` | 환경 |

## 권장 라벨
| 라벨 | 카디널리티 | 비고 |
|------|------------|------|
| `strategy` | 수십 | 활성 전략 ID |
| `broker` | <10 | kis, ebest, mock |
| `algo` | <10 | market/limit/twap/vwap |

## 본문에만 (라벨 X)
- `trace_id` (UUID), `order_id`, `symbol` (전 종목), `user_id`, `request_id`.
- 검색은 LogQL line filter로 (`|= "trace_id=abc"`).

## 예시 로그 라인
```json
{"ts":"2026-04-13T10:00:00Z","app":"qta-engine","severity":"info","strategy":"momo-v1","trace_id":"7b1f...","msg":"order submitted","symbol":"005930","qty":100}
```

## 출처
- Loki labels: https://grafana.com/docs/loki/latest/get-started/labels/
- Loki best practices: https://grafana.com/docs/loki/latest/get-started/labels/bp-labels/
- promtail json pipeline: https://grafana.com/docs/loki/latest/send-data/promtail/stages/json/
