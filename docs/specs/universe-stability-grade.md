---
type: spec-architecture
id: universe-stability-grade
name: Universe Stability Grade (A~F)
owner: siwoo
status: draft
---

# Universe Stability Grade (A~F)

알트코인 유니버스 필터링을 위한 안정성 등급 산출 스펙.

관련 알파 팩터 목록: [[13-feature-alpha-catalog]]

## 개요

`StabilityGrade.grade(mcap_usd, vol_30d_usd, dev_activity)` 는 세 가지 입력 변수를 
로그 정규화 후 가중 합산해 A~F 등급을 반환하는 **순수 함수**다.
외부 I/O 없음. CoinGecko 어댑터는 별도 이슈(follow-up chore).

## 입력 변수

| 변수 | 타입 | 설명 |
|------|------|------|
| `mcap_usd` | float | 현재 시가총액 (USD) |
| `vol_30d_usd` | float | 30일 거래대금 (USD) |
| `dev_activity` | Optional[int] | 30일 커밋/PR 수 (None 허용) |

## 가중치

| 시나리오 | mcap | volume | dev |
|---------|------|--------|-----|
| dev_activity 제공 | 0.4 | 0.4 | 0.2 |
| dev_activity=None | 0.5 | 0.5 | — |

## 등급 기준 (composite score [0, 1])

| 등급 | 최소 score |
|------|-----------|
| A | 0.85 |
| B | 0.70 |
| C | 0.50 |
| D | 0.30 |
| E | 0.15 |
| F | 0.15 미만 |

## 정규화

각 변수는 log10 스케일로 정규화 (6-decade 윈도우):

```
score = (log10(value / max_anchor) + 6) / 6
```

앵커값: mcap_max=2e12, vol_max=1e11, dev_max=1000

## 특허 회피

업리치 특허 청구항 (d) 의 A~F 등급 개념을 학술 참고로 차용.
입력 변수(시가총액·거래량·개발활동), 정규화 방식(log10), 등급 경계값 모두 재정의.

## 구현 위치

- `src/universe/stability_grade.py` — `StabilityGrade`, `grade_symbol`
- `src/universe/__init__.py` — 공개 API

## 완료 조건

- `pytest tests/test_stability_grade.py -q` 전체 pass
- `scripts/check_invariants.py --strict` 통과
- DSL 배선은 별도 follow-up 이슈
