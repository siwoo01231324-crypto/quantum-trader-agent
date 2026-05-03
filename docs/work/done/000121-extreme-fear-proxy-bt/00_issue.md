---
type: work-done
id: 000121-extreme-fear-proxy-bt
name: "#121 extreme_fear_threshold 가격 기반 프록시 백테스트 검증"
title: "#121 extreme_fear_threshold 가격 기반 프록시 백테스트 검증"
status: in_progress
issue: 121
created: 2026-04-27
owner: siwoo
---

## 이슈 요약

`extreme_fear_block` 기능은 `fear_greed_proxy = current_price / rolling_max(window=252)`가
`extreme_fear_threshold` (기본 0.2) 미만일 때 매수 주문을 차단한다.

이 임계값의 적정성을 실제 Crypto Fear & Greed Index와 비교·검증하고, 민감도 분석으로 권고 임계값을 도출한다.

## AC

- [ ] 과거 3년 BTC + KRX 데이터에 가격 프록시 vs 실제 공포·탐욕 지수 상관계수 측정
- [ ] 0.2 임계값 정확도 (precision/recall) — 실제 공포 구간 포착률
- [ ] 보정 필요 시 임계값·계산식 갱신 제안
- [ ] 결과 노트 docs/work/done/research/extreme_fear_validation.md

## 구현체 위치

- `src/portfolio/orchestrator.py:48` — `compute_fear_greed_proxy(price_history, window=252)`
- `src/risk/dsl.py:86-87` — `extreme_fear_block`, `extreme_fear_threshold=0.2`
- `src/risk/dsl.py:255-264` — 평가 로직

## 분석 방법

1. Binance Vision에서 BTC/USDT 일봉 3년치 다운로드 (2023-01-01 ~ 2026-04-27)
2. Alternative.me Fear & Greed Index API로 같은 기간 실제 지수 수집
3. 가격 프록시 계산 (window=252)
4. 상관계수 측정 + 임계값별 precision/recall 분석
5. 권고 임계값 도출
