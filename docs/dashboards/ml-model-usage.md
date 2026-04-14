# ML 모델을 사용하는 Signal

`source_model` 이 지정된 Signal 만 추려 ML 의존성을 파악한다.

```dataview
TABLE source_model AS "ML Model", lookback, inputs
FROM "specs/signals"
WHERE source_model != null
SORT source_model ASC
```
