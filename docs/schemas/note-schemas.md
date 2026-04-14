# 노트 타입별 프론트매터 스키마

> 이 문서는 Obsidian 볼트 내 노트의 **공식 프론트매터 규약**이다. CI(`scripts/check_invariants.py`) 가 이 스키마를 검증한다.

## 공통 규칙

- 모든 노트는 YAML 프론트매터로 시작한다.
- `type` 은 필수. 아래 타입 중 하나.
- `id` 는 파일명(확장자 제외)과 반드시 일치해야 한다.
- 다른 노트 참조는 본문에서 `[[id]]` 위키링크 형식 사용.
- 날짜는 ISO 8601 (`YYYY-MM-DD` 또는 `YYYY-MM-DDTHH:MM:SS+09:00`).
- 리스트 필드에는 참조 대상의 `id` 만 적는다 (경로 아님).

## 타입 목록

| type | 경로 | 설명 |
|------|------|------|
| `strategy` | `docs/specs/strategies/` | 거래 전략 명세 |
| `signal` | `docs/specs/signals/` | 진입·청산 신호 정의 |
| `risk-rule` | `docs/specs/risk-rules/` | 리스크 규칙 |
| `instrument` | `docs/specs/instruments/` | 거래 종목 메타 |
| `backtest` | `docs/work/done/backtests/` | 백테스트 결과 |
| `incident` | `docs/work/incidents/` | 장애·사건 기록 |
| `postmortem` | `docs/work/incidents/` | 장애 회고 |
| `spec-architecture` | `docs/specs/*.md` (플랫) | 아키텍처·기술 명세 |
| `runbook` | `docs/runbooks/` | 운영 런북 |
| `research` | `docs/background/` | 조사·리서치 노트 |
| `onboarding` | `docs/onboarding/` | 온보딩 가이드 |
| `whitepaper` | `docs/whitepaper/` | 백서 |
| `work-done` | `docs/work/done/*/` · `docs/work/active/*/` | 이슈 작업 내역 (보조 타입) |

---

## 1. Strategy

```yaml
---
type: strategy
id: momo-btc-v2                 # 필수, 파일명과 일치
name: BTC Momentum v2           # 필수, 사람이 읽는 이름
status: draft                   # 필수: draft|backtest|paper|live|retired
instruments: [BTCUSDT]          # 필수, instrument id 리스트
timeframe: 15m                  # 필수: 1m|5m|15m|1h|4h|1d
uses_signals: [rsi-divergence]  # 선택, signal id 리스트
risk_rules: [max-drawdown-5pct] # 선택, risk-rule id 리스트
owner: siwoo                    # 필수
created: 2026-04-14             # 필수
sharpe_bt: 1.8                  # 선택, 백테스트 Sharpe
sharpe_live: null               # 선택, 라이브 Sharpe (없으면 null)
tags: [momentum, crypto]        # 선택
---
```

### 필수 필드
`type`, `id`, `name`, `status`, `instruments`, `timeframe`, `owner`, `created`

### 상태 전이
`draft` → `backtest` → `paper` → `live` → `retired`

---

## 2. Signal

```yaml
---
type: signal
id: rsi-divergence              # 필수
name: RSI Divergence            # 필수
inputs: [close, volume]         # 필수, 계산에 필요한 입력
lookback: 14                    # 필수 (bar 수)
source_model: ml-rsi-clf-v1     # 선택, ML 모델 id
tags: [technical]               # 선택
---
```

### 필수 필드
`type`, `id`, `name`, `inputs`, `lookback`

---

## 3. RiskRule

```yaml
---
type: risk-rule
id: max-drawdown-5pct           # 필수
name: Max Drawdown 5%           # 필수
severity: critical              # 필수: critical|warn
scope: portfolio                # 필수: portfolio|strategy|instrument
threshold: 0.05                 # 필수, 위반 기준값
action: halt                    # 필수: halt|reduce|alert
description: |                  # 선택, 긴 설명
  포트폴리오 MDD 5% 초과 시 즉시 halt
---
```

### 필수 필드
`type`, `id`, `name`, `severity`, `scope`, `threshold`, `action`

---

## 4. Instrument

```yaml
---
type: instrument
id: BTCUSDT                     # 필수
name: BTC Perpetual             # 필수
asset_class: crypto-spot        # 필수: crypto-spot|crypto-perp|krx-stock|kr-fx|us-stock
venue: binance                  # 필수: binance|upbit|krx|ibkr 등
tick_size: 0.01                 # 필수
lot_size: 0.00001               # 선택
min_notional: 10                # 선택
---
```

### 필수 필드
`type`, `id`, `name`, `asset_class`, `venue`, `tick_size`

---

## 5. Backtest

```yaml
---
type: backtest
id: bt-2026-04-10-momo-btc-v2   # 필수, 형식: bt-{YYYY-MM-DD}-{strategy-id}
strategy: momo-btc-v2           # 필수, strategy id
period: [2024-01-01, 2026-04-01]# 필수, [start, end]
metrics:                        # 필수
  sharpe: 1.82
  mdd: -0.048
  trades: 412
  win_rate: 0.54
  cagr: 0.35
artifacts:                      # 선택, 리포트 경로
  - reports/bt-2026-04-10.html
---
```

### 필수 필드
`type`, `id`, `strategy`, `period`, `metrics`

`metrics` 최소 필드: `sharpe`, `mdd`, `trades`

---

## 6. Incident

```yaml
---
type: incident
id: inc-2026-04-12-slippage     # 필수, 형식: inc-{YYYY-MM-DD}-{slug}
occurred: 2026-04-12T14:30:00+09:00  # 필수
severity: P2                    # 필수: P0|P1|P2|P3
affected_strategies: [momo-btc-v2]   # 필수, strategy id 리스트
violated_rules: [max-drawdown-5pct]  # 선택
root_cause: 유동성 급감 구간 시장가 진입  # 필수, 1-2줄
postmortem: pm-2026-04-12       # 선택, postmortem id
---
```

### 필수 필드
`type`, `id`, `occurred`, `severity`, `affected_strategies`, `root_cause`

---

## 7. PostMortem

```yaml
---
type: postmortem
id: pm-2026-04-12               # 필수, 형식: pm-{YYYY-MM-DD}
incident: inc-2026-04-12-slippage    # 필수, incident id
authors: [siwoo]                # 필수
status: draft                   # 필수: draft|final
action_items: [ai-2026-04-13-slippage-guard]  # 선택
---
```

### 필수 필드
`type`, `id`, `incident`, `authors`, `status`

---

## 검증 규칙 (CI)

1. `type` 값이 위 7개 중 하나여야 함
2. 필수 필드 누락 시 실패
3. `id` 가 파일명과 불일치하면 실패
4. 참조 필드(`uses_signals`, `risk_rules`, `strategy`, `incident` 등)가 존재하지 않는 id 를 가리키면 실패
5. 본문의 `[[id]]` 위키링크도 동일하게 검증
6. v1 단계에서는 모든 검증이 **warn**, v2 에서 **fail** 로 전환

---

## 예시 노트
최소 샘플은 Phase 2 마이그레이션에서 `docs/specs/strategies/`, `docs/specs/signals/`, `docs/specs/risk-rules/` 하위에 3~5건 추가된다.

---

## 8. SpecArchitecture (아키텍처 명세)

`docs/specs/` 바로 아래(서브폴더 없이) 놓이는 아키텍처·기술 설계 문서. 위 4개 엔티티 타입(strategy/signal/risk-rule/instrument)과 구분된다.

```yaml
---
type: spec-architecture
id: data-lake-schema             # 필수, 파일명과 일치
name: Data Lake Schema           # 필수
owner: siwoo                     # 필수
status: draft                    # 필수: draft|accepted|superseded
tags: [storage, parquet]         # 선택
---
```

### 필수 필드
`type`, `id`, `name`, `owner`, `status`

---

## 9. Runbook (운영 런북)

```yaml
---
type: runbook
id: kill-switch-runbook          # 필수
name: Kill Switch & DR Runbook   # 필수
severity: P1                     # 필수: P0|P1|P2|P3
related_rules: [max-drawdown-5pct]  # 선택, risk-rule id 리스트
---
```

### 필수 필드
`type`, `id`, `name`, `severity`

---

## 10. Research (조사·리서치)

`docs/background/` 하위의 팩트 기반 조사 노트. 출처 목록이 필수.

```yaml
---
type: research
id: 07-market-microstructure-basics  # 필수
name: KRX Market Microstructure Basics  # 필수
sources:                         # 필수, 출처 URL 또는 문서 경로 리스트
  - https://example.com/paper
---
```

### 필수 필드
`type`, `id`, `name`, `sources`

---

## 11. Onboarding (온보딩 가이드)

```yaml
---
type: onboarding
id: getting-started              # 필수
name: Getting Started            # 필수
---
```

### 필수 필드
`type`, `id`, `name`

---

## 12. Whitepaper (백서)

```yaml
---
type: whitepaper
id: qta-whitepaper-v01           # 필수
name: QTA Whitepaper             # 필수
version: "0.1"                   # 필수
---
```

### 필수 필드
`type`, `id`, `name`, `version`

---

## 13. WorkDone (이슈 작업 내역, 보조 타입)

`docs/work/done/<issue>/` 와 `docs/work/active/<issue>/` 하위 작업 노트용. 엔티티가 아니라 기록 노트이므로 최소 필드만 요구.

```yaml
---
type: work-done
id: 00_issue                     # 필수 (파일명과 일치)
name: "#9 시스템 구성요소 개괄"    # 필수
status: done                     # 필수: active|done
---
```
