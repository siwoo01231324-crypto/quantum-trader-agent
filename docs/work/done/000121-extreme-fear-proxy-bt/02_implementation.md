---
type: work-done
id: 000121-extreme-fear-proxy-bt
name: "#121 구현 상세 — extreme_fear_threshold 보정"
title: "#121 구현 상세 — extreme_fear_threshold 보정"
status: done
issue: 121
created: 2026-04-27
owner: siwoo
---

## 변경 내용

### 1. `src/risk/dsl.py` — 기본값 변경

`PerPortfolioRisk.extreme_fear_threshold` 기본값을 `0.2` → `0.75`로 상향.

**근거:** 2023-2026 BTC 일봉 1,185일 분석 결과, 가격 프록시(current_price/rolling_max(252)) 최솟값이 0.505.
기존 0.2는 한 번도 발동하지 않는 데드 코드였음. 0.75에서 F1=0.786, MDD -32.5% (B&H -49.5% 대비 개선).

### 2. `scripts/fear_proxy_analysis.py` — 신규 분석 스크립트

Binance Vision + Alternative.me FGI 비교 분석 스크립트.

### 3. `docs/work/done/research/extreme_fear_validation.md` — 결과 노트

상관계수, Precision/Recall, 백테스트 민감도 전체 결과 수록.

## 권고 임계값: 0.75

| 지표 | 값 |
|------|-----|
| Pearson 상관 (프록시 vs FGI) | 0.817 |
| Spearman 상관 | 0.755 |
| F1 (threshold=0.75) | 0.786 |
| Precision | 0.815 |
| Recall | 0.759 |
| Sharpe (threshold=0.75) | 1.239 |
| MDD (threshold=0.75) | -32.5% |
| B&H Sharpe | 1.150 |
| B&H MDD | -49.5% |
