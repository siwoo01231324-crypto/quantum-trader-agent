# 최근 30일 Incident

최근 30일 이내 발생한 장애·사건 타임라인.

```dataview
TABLE occurred, severity, affected_strategies, root_cause
FROM "work/incidents"
WHERE type = "incident" AND date(occurred) >= date(today) - dur(30 days)
SORT occurred DESC
```
