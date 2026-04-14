# 전략 × 리스크 규칙 매트릭스

각 전략이 적용한 `risk_rules` 리스트를 한눈에 확인한다. 공란이면 리스크 규칙 누락 가능성.

```dataview
TABLE risk_rules AS "Applied Risk Rules", status
FROM "specs/strategies"
SORT file.name ASC
```
