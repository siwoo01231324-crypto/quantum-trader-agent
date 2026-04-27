---
id: 02_implementation_catalog_3y
type: work-plan
name: "#119 카탈로그 5종 3년 측정 결과"
measured_at: 2026-04-26 16:50 UTC
window: "2023-04-27 ~ 2026-04-27"
mode: DRY-RUN (synthetic data, seed=119)
---

# #119 카탈로그 5종 베이스라인 — 3년 윈도우 측정

> 자동 생성: `scripts/measure_strategy_catalog_3y.py`
> 측정 모드: DRY-RUN (synthetic data, seed=119)

## 측정 파라미터

| 항목 | 값 |
|------|-----|
| 요청 측정 기간 | 2023-04-27 ~ 2026-04-27 (3년) |
| 데이터 소스 | Binance (crypto 3종), KIS paper (KRX) |
| KRX 수집 | N/A (KIS env 미설정 - synthetic fallback; KRX 실측 데이터 가용 기간: ~2025-01 ~ 2026-04 (약 1.3년). 3년 요구 미충족 - 결과는 참고용.) |
| 공통 거래일 수 | 330 |

## 주의사항 (한계)

- **KRX 1.3년 미충족**: KOSPI200 실측 데이터는 2025-01 이후만 가용. 3년 기준 미충족.
- **사후 곱하기 근사**: 레버리지 시나리오는 `r_t^(L) = L·r_t - (L-1)·c_borrow` 사후 근사.
- **Dry-run**: DRY-RUN (synthetic data, seed=119). 실데이터 결과와 차이 있음.

## 카탈로그 5종 베이스라인

| 전략 | 연 수익률 | Sharpe | MDD | 기간(일) |
|------|----------|--------|-----|---------|
| momo_btc_v2 | 9.08% | 0.472 | -24.42% | 1095 |
| meanrev_pairs | 5.03% | 0.324 | -26.22% | 1095 |
| momo_vol_filtered | 26.42% | 1.102 | -24.25% | 1095 |
| breakout_donchian | -15.40% | -0.783 | -27.51% | 330 |
| momo_kis_v1 | 6.25% | 0.411 | -16.06% | 330 |

> momo_kis_v1 은 KRX 전략으로 별도 표기. breakout_donchian = KOSPI200 equal-weight basket.

## 실측 상관 매트릭스

| strategy | momo_btc_v2 | meanrev_pairs | momo_vol_filtered | breakout_donchian | momo_kis_v1 |
|---|---|---|---|---|---|
| momo_btc_v2 | 1.000 | -0.014 | 0.026 | 0.025 | 0.056 |
| meanrev_pairs | -0.014 | 1.000 | 0.044 | -0.004 | -0.030 |
| momo_vol_filtered | 0.026 | 0.044 | 1.000 | 0.003 | 0.025 |
| breakout_donchian | 0.025 | -0.004 | 0.003 | 1.000 | -0.023 |
| momo_kis_v1 | 0.056 | -0.030 | 0.025 | -0.023 | 1.000 |

## 포트폴리오 리스크 지표

| 지표 | 값 |
|------|-----|
| ENB | 3.3215 |
| ENB Ratio (ENB/N) | 0.6643 |
| 평균 pairwise ρ | 0.0075 |
| CVaR (97.5%) | 0.0139 |
| VaR (97.5%) | 0.0110 |
| 전략 수 | 5 |
