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

포트폴리오 레벨 MDD 5% 하드 리밋. [[momo-btc-v2]] 등 모든 라이브 전략에 적용된다. 위반 시 `docs/runbooks/` 의 Kill Switch 절차에 따라 처리.
