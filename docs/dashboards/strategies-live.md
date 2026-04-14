# 라이브 전략 현황

현재 `status: live` 로 설정된 전략들의 백테스트 Sharpe 및 타임프레임을 표시한다.

```dataview
TABLE sharpe_bt AS "Sharpe (BT)", status, timeframe
FROM "specs/strategies"
WHERE status = "live"
SORT sharpe_bt DESC
```
