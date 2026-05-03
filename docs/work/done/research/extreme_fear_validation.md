---
type: research
id: extreme_fear_validation
name: "extreme_fear_threshold 가격 기반 프록시 검증"
title: "extreme_fear_threshold 가격 기반 프록시 검증"
status: done
issue: 121
created: 2026-04-27
owner: siwoo
tags: [risk, fear-greed, backtest, btc]
sources:
  - https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/
  - https://api.alternative.me/fng/
---

## 요약

`compute_fear_greed_proxy(price_history, window=252)` = `current_price / rolling_max(252)` 와
Alternative.me Crypto Fear & Greed Index(FGI)를 2023-01-01~2026-03-31 BTC 일봉 1,185일로 비교 검증.

**핵심 결론: 기본값 `extreme_fear_threshold=0.2`는 분석 기간 내 단 한 번도 발동하지 않는다.
임계값을 `0.75`로 상향 조정해야 공포 구간을 실질적으로 포착할 수 있다.**

## 데이터 출처

- BTC 일봉: Binance Vision (BTCUSDT-1d, 2023-01 ~ 2026-03), 총 1,186일
- Fear & Greed Index: Alternative.me `/fng/` API, 동일 기간 1,185일 공통

## 1. 가격 프록시 분포

| 통계 | 값 |
|------|----|
| 최솟값 | 0.505 (2026-02-05) |
| 5th pct | 0.643 |
| 10th pct | 0.734 |
| 25th pct | 0.837 |
| 중앙값 | 0.913 |
| 평균 | 0.887 |
| 최댓값 | 1.000 |

**결론:** 프록시가 0.30 미만인 날은 0일. BTC는 분석 기간 내 252일 rolling high 대비 50% 이상 하락한 적이 없다.
따라서 `threshold=0.2` (기존 기본값)는 사실상 비활성 상태이다.

## 2. 상관 분석 (n=1,185일)

| 지표 | 값 |
|------|----|
| Pearson 상관계수 | **0.817** |
| Spearman 순위 상관 | **0.755** |

가격 프록시와 실제 FGI 사이에 강한 양의 상관이 존재한다.
단, 선형 관계이므로 절대값 보정(임계값 재설정)이 필수이다.

## 3. 임계값별 Precision / Recall (공포 기준: FGI ≤ 25)

| 임계값 | Precision | Recall | F1 | 차단비율% | TP | FP | FN | TN |
|--------|-----------|--------|----|-----------|----|----|----|----|
| 0.55 | 1.000 | 0.186 | 0.314 | 2.3% | 27 | 0 | 118 | 1040 |
| 0.60 | 0.964 | 0.366 | 0.530 | 4.6% | 53 | 2 | 92 | 1038 |
| 0.65 | 0.967 | 0.400 | 0.566 | 5.1% | 58 | 2 | 87 | 1038 |
| 0.70 | 0.959 | 0.483 | 0.642 | 6.2% | 70 | 3 | 75 | 1037 |
| **0.75** | **0.815** | **0.759** | **0.786** | **11.4%** | **110** | **25** | **35** | **1015** |
| 0.80 | 0.606 | 0.848 | 0.707 | 17.1% | 123 | 80 | 22 | 960 |

- 실제 공포일 145일 중 임계값 0.75에서 110일 포착 (Recall 75.9%)
- 오탐(FP) 25일: 전체의 2.1%에 불과

## 4. 백테스트 민감도 (1-bar daily, 2023-2026)

| 임계값 | Sharpe | MDD% | 총수익률% | 차단일수 | 차단비율% |
|--------|--------|------|-----------|----------|-----------|
| 0.55 | 0.955 | -60.2% | +198% | 26 | 2.2% |
| 0.60 | 1.221 | -42.1% | +335% | 54 | 4.6% |
| 0.65 | **1.282** | -36.8% | **+374%** | 59 | 5.0% |
| 0.70 | 1.236 | -41.5% | +339% | 72 | 6.1% |
| **0.75** | **1.239** | **-32.5%** | +327% | 134 | 11.3% |
| 0.80 | 1.180 | -36.7% | +275% | 202 | 17.0% |
| B&H (기준) | 1.150 | -49.5% | +311% | — | — |

## 5. 권고 임계값: 0.75

### 근거

1. **F1 최고**: 0.75에서 F1=0.786으로 전체 최고
2. **MDD 개선 최대**: -32.5% vs B&H -49.5% → 낙폭 35% 감소
3. **Sharpe B&H 상회**: 1.239 vs 1.150
4. **오탐 제한**: FP 25일(2.1%) — 정상 구간에서 불필요한 차단이 적음
5. **Recall 균형**: 75.9% — 실제 공포일의 3/4 포착, 과도한 차단 없음

0.65는 Sharpe가 소폭 높지만 Recall 40%로 공포일 60%를 놓친다.
0.80은 Recall은 높지만 FP 80일(6.7%)로 오탐이 과다하다.

### 주의사항

- 분석 기간(2023-2026)은 BTC 강세 사이클 위주 — 장기 하락장(2018, 2022년)에서는 프록시 분포가 달라질 수 있다
- window=252 (일봉 기준 ≈1년)는 현행 유지. 단기 window는 과민, 장기 window는 둔감
- Alternative.me FGI는 소셜·온체인 데이터를 포함하므로 "실제 공포"의 완벽한 기준은 아님

## 6. 권고 변경 사항

`src/risk/dsl.py` `PerPortfolioRisk`:

```python
# 변경 전
extreme_fear_threshold: Optional[float] = Field(default=0.2, ge=0.0, le=1.0)

# 변경 후
extreme_fear_threshold: Optional[float] = Field(default=0.75, ge=0.0, le=1.0)
```

`policies/conservative.yaml`, `policies/neutral.yaml`, `policies/aggressive.yaml` 에서
`extreme_fear_threshold` 필드를 명시적으로 설정할 것을 권고.

## 출처

- BTC 가격 데이터: https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1d/
- Fear & Greed Index: https://api.alternative.me/fng/
- 구현체: `src/portfolio/orchestrator.py:48` `compute_fear_greed_proxy`
- DSL 정의: `src/risk/dsl.py:86-87`
- 분석 스크립트: `scripts/fear_proxy_analysis.py`
- 관련 이슈: [[000121-extreme-fear-proxy-bt]]
