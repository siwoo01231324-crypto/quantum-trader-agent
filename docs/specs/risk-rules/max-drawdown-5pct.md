---
type: risk-rule
id: max-drawdown-5pct
name: Max Drawdown 5%
severity: critical
scope: portfolio
threshold: 0.05
action: halt
description: |
  포트폴리오 실시간 MDD 가 5% 를 초과하면 즉시 모든 라이브 전략을 halt 한다.
---

# Max Drawdown 5%

포트폴리오 레벨 MDD 5% 하드 리밋. [[momo-btc-v2]] 등 모든 라이브 전략에 적용된다. 위반 시 [[kill-switch-runbook]] 의 Kill Switch 절차에 따라 처리.

## 관련 노트

- [[risk-rule-dsl]] — 본 룰을 YAML DSL 로 표현하는 방법
- [[kill-switch-dr]] — 자동 트리거 §4 "일일 DrawDown" 연계
- [[kill-switch-runbook]] — 위반 시 실행 절차
- [[19-portfolio-risk]] — 단일 룰을 보완하는 포트폴리오 레벨 리스크 (공분산·CVaR)
- [[12-validation-protocol]] — 백테스트 롤백 트리거 "MDD × 1.5" 와 연동
