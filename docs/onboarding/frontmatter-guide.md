---
type: onboarding
id: frontmatter-guide
name: "프론트매터 작성 가이드"
---

# 프론트매터 작성 가이드

`docs/schemas/note-schemas.md` 의 공식 스펙을 실전 관점에서 풀어 쓴 가이드. 새 노트를 만들 때 이 문서를 체크리스트로 사용한다.

## 공통 규칙

- 모든 노트는 `---` 로 감싼 YAML 프론트매터로 시작한다.
- `type` 과 `id` 는 **필수**.
- `id` 는 파일명(확장자 제외)과 반드시 일치.
- 다른 노트 참조는 본문에서 `[[id]]` 위키링크 사용, 프론트매터 리스트에는 `id` 문자열만.
- 날짜는 ISO 8601. `2026-04-14` 또는 `2026-04-14T14:30:00+09:00`.

## 7개 타입 요약

| type | 경로 | 핵심 필수 필드 |
|------|------|---------------|
| `strategy` | `docs/specs/strategies/` | `name, status, instruments, timeframe, owner, created` |
| `signal` | `docs/specs/signals/` | `name, inputs, lookback` |
| `risk-rule` | `docs/specs/risk-rules/` | `name, severity, scope, threshold, action` |
| `instrument` | `docs/specs/instruments/` | `name, asset_class, venue, tick_size` |
| `backtest` | `docs/work/done/backtests/` | `strategy, period, metrics` |
| `incident` | `docs/work/incidents/` | `occurred, severity, affected_strategies, root_cause` |
| `postmortem` | `docs/work/incidents/` | `incident, authors, status` |

## 실전 팁

### 1. ID 네이밍
- 짧은 slug + 버전: `momo-btc-v2`, `mean-reversion-krx-v1`
- 날짜 기반 엔트리: `bt-2026-04-10-momo-btc-v2`, `inc-2026-04-12-slippage`, `pm-2026-04-12`
- 소문자·하이픈만. 공백·대문자 금지 (위키링크 깨짐)

### 2. 리스트 필드 주의
- YAML 에서 `instruments: [BTCUSDT]` 또는 멀티라인:
  ```yaml
  instruments:
    - BTCUSDT
    - ETHUSDT
  ```
- 한 원소도 반드시 리스트 (`instruments: BTCUSDT` 금지)

### 3. null 표현
- 값 없을 때는 `null` 명시 (예: `sharpe_live: null`) — 파서가 필드 존재 여부로 구분

### 4. 참조 무결성
- `uses_signals`, `risk_rules`, `instruments`, `strategy`, `incident` 등 참조 필드는 실제 노트가 존재해야 한다.
- `scripts/check_invariants.py` 가 v2 부터 fail 로 전환 (v1 은 warn).

### 5. 본문 위키링크
- 본문에서 다른 노트를 참조할 땐 `[[rsi-divergence]]` 형태
- 표시 텍스트 변경: `[[rsi-divergence|RSI 다이버전스]]`

### 6. 상태 전이 (strategy)
`draft → backtest → paper → live → retired`
- `live` 로 올리기 전에 Backtest 노트 1건 이상 필수 (ontology 쿼리 `strategy_without_tests.rq` 로 감지)

## 최소 샘플

### strategy
```yaml
---
type: strategy
id: my-strategy-v1
name: My Strategy v1
status: draft
instruments: [BTCUSDT]
timeframe: 1h
owner: siwoo
created: 2026-04-14
---
```

### signal
```yaml
---
type: signal
id: my-signal
name: My Signal
inputs: [close]
lookback: 20
---
```

### risk-rule
```yaml
---
type: risk-rule
id: stop-loss-2pct
name: Stop Loss 2%
severity: warn
scope: strategy
threshold: 0.02
action: reduce
---
```

## 참조
- 공식 스펙: `docs/schemas/note-schemas.md`
- 검증 스크립트: `scripts/check_invariants.py`
- 온톨로지 매핑: `docs/ontology/trading.ttl`
