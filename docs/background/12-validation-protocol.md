---
type: research
id: 12-validation-protocol
name: "백테스트 검증 프로토콜 (walk-forward · purged K-fold · data snooping)"
sources: []
---

# 백테스트 검증 프로토콜 (walk-forward · purged K-fold · data snooping)

> 목적: 저빈도 규칙기반 퀀트 전략의 데이터 스누핑·오버피팅을 구조적으로 차단할 수 있는 검증 방법론을 프로젝트 SOP로 고정한다. 주 레퍼런스: López de Prado, *Advances in Financial Machine Learning* (2018) + Bailey·López de Prado, *Deflated Sharpe Ratio* (2014) + *Probability of Backtest Overfitting* (2014).

## 1. 핵심 검증 기법

### 1.1 Walk-forward validation (WFV)

시계열을 시간순으로 잘라 (train → test) 윈도우를 오른쪽으로 밀어가며 반복 평가.

- **Anchored WFV**: train 시작점을 고정하고 윈도우를 확장 (expanding). 데이터 희소한 장기 전략에 적합.
- **Rolling WFV**: train 길이를 고정하고 윈도우를 이동 (sliding). 체제 변화(regime shift)에 민감.

수식 (rolling, 총 T기간, train L, test M, step S):

```
Fold k: train = [tk, tk+L), test = [tk+L, tk+L+M)
tk = k · S,  k = 0, 1, ..., ⌊(T - L - M)/S⌋
```

평가지표는 각 fold의 OOS 지표(Sharpe, MDD 등)를 집계 (중앙값·IQR 권장).

### 1.2 Purged K-fold CV

Fold 경계에서 라벨이 train/test로 겹치는 구간을 제거(purge)하고, test 직후 일정 기간을 embargo로 추가 제거해 미래 유출을 차단.

- purge: train 관측치 중 라벨 구간이 test와 겹치면 제외
- embargo(h): test 종료 후 h 구간의 train 관측치 제외 (보통 전체 길이의 1% 내외)

### 1.3 Combinatorial Purged CV (CPCV)

N개 그룹 중 k개를 test로 뽑는 모든 조합에 대해 purge+embargo 수행. C(N,k) 경로를 생성해 backtest path 분포를 얻고 PBO/DSR 계산에 활용.

### 1.4 Deflated Sharpe Ratio (DSR)

다중 실험·비정규성 편향을 보정한 Sharpe. PSR(Probabilistic SR)에 시행 횟수 N을 반영:

```
SR0 = sqrt(Var(SR_estimates)) · [(1 - γ) · Z⁻¹(1 - 1/N) + γ · Z⁻¹(1 - 1/(N·e))]
DSR = Prob( SR_observed > SR0 | 비정규성 보정 )
```

의사결정 기준: **DSR ≥ 0.95** 통과 시에만 전략 채택.

### 1.5 Probability of Backtest Overfitting (PBO)

CSCV(Combinatorially Symmetric CV)로 OOS rank < median이 되는 비율. **PBO ≤ 0.5**를 최소 기준, 실전 배치는 **PBO ≤ 0.2** 권장.

## 2. 데이터 스누핑·편향 방지 체크리스트

| # | 항목 | 설명 |
|---|------|------|
| 1 | Survivorship bias | 상장폐지·리네임 종목을 포함한 point-in-time universe 사용 |
| 2 | Look-ahead bias | 특정 시점 t의 피처는 t 이전 데이터만 사용 (분할·배당 보정 시각 정확) |
| 3 | Data snooping | 시행 횟수 N 기록, DSR로 deflate |
| 4 | Label leakage | triple-barrier 라벨·t1 초과 윈도우 train/test에서 purge |
| 5 | Point-in-time fundamentals | 공시일·수정일 타임스탬프 기준, rolling restatement 반영 |
| 6 | Corporate actions | 액면분할·유상증자 조정 계수 backward adjust |
| 7 | 거래비용·슬리피지 | fee·tax(#28)·호가 스프레드·시장충격 포함 |
| 8 | Market regime split | 강세·약세·횡보 구간 별 성과 리포트 |
| 9 | Overnight gap | 시가 진입 전략은 전일 종가 데이터만 사용 |
| 10 | Winner bias | 파라미터 그리드 로그 저장, 하이퍼 탐색은 CPCV 외부 loop에서만 |
| 11 | Time zone | 한국(KST) ↔ 해외(UTC) 믹싱 시 명시 |
| 12 | Liquidity filter | 최소 거래대금·호가 스프레드·ADV 5% 미만 체결 가정 |
| 13 | Random seed | 난수 사용 시 seed 기록, 동일 시드로 재현 가능 |

## 3. 본 프로젝트 SOP (표준 절차)

Phase 1/2 규칙기반 전략에 적용되는 **1페이지 SOP**:

1. **데이터 계약**: data-lake(#20)에서 point-in-time parquet 로드. 모든 피처는 as-of 타임스탬프 컬럼 필수.
2. **Universe 선택**: 해당 시점 상장·거래정지 해제·최소 ADV 조건 통과 종목만. 종목 변경 이력 log 필수.
3. **스플릿**:
   - 총 기간 = Train(70%) + Validation(15%) + Test(15%, 봉인).
   - Train/Validation은 Rolling Walk-forward 12개월 train / 3개월 test / 1개월 step.
   - 파라미터 탐색은 Train 내 CPCV(N=8, k=2, embargo=1%)로만.
4. **평가 지표**: Sharpe, Sortino, MDD, Calmar, turnover, tail ratio, 월간 hit rate. 비용 포함 순수익 기준.
5. **통계 검증**:
   - DSR 계산, 시행 횟수 N 기록.
   - PBO 계산(CSCV N=16).
   - Test(봉인)는 모든 수정 완료 후 **단 1회** 실행.
6. **보고**: `reports/backtest/{strategy}/{run_id}/` 에 config, metrics, equity curve, trade log, DSR/PBO 저장.
7. **승인 기준**: DSR ≥ 0.95 AND PBO ≤ 0.2 AND 월간 hit rate ≥ 50% (5년 이상 구간).
8. **라이브 전환**: 최소 2개월 paper-trading → 실자금 소액 pilot(portfolio의 5%) → 단계적 스케일업.

## 4. 검증 실패 시 롤백 기준

배치된 전략의 다음 중 하나 발생 시 즉시 롤백(포지션 청산·비활성화):

| 트리거 | 임계값 | 근거 |
|--------|---|------|
| Rolling 3-month Sharpe < 0 | 2개월 연속 | 모멘텀 붕괴 |
| MDD > backtest MDD × 1.5 | 한 번이라도 | 분포 shift |
| 일간 손실 > VaR(99%) × 1.5 | 3거래일 중 2회 | 리스크 모델 붕괴 |
| 월간 turnover > 예상 ×2 | 한 달 | 비용 구조 파괴 |
| hit rate < 40% | 60거래일 rolling | 엣지 소실 |
| 실거래 Sharpe vs 백테스트 Sharpe 괴리 | |difference| > 1.5 | 6개월 | 체제 변화 |
| 데이터 파이프라인 SLO 위반 (#26) | 연속 3회 | 입력 신뢰도 붕괴 |

롤백 의사결정은 kill-switch(#27)와 연계. 롤백 이후 재배치는 전체 검증 사이클 재실행 후에만 허용.

## 5. Phase 1 규칙기반 적용 예

대상: KOSPI 중대형주 모멘텀 (6개월 momentum, 월간 리밸런스)

- Train: 2010-01 ~ 2018-12 (Anchored WFV 24m train / 3m test / 1m step)
- Validation: 2019-01 ~ 2021-12 (CPCV N=8, k=2, embargo=5일)
- Test(봉인): 2022-01 ~ 2024-12 — 단 1회
- 파라미터: lookback ∈ {3, 6, 9, 12}, top-k ∈ {10, 20, 30} — 총 12 조합 → N=12로 DSR 계산
- 승인 기준: DSR ≥ 0.95, PBO ≤ 0.2, turnover-adjusted Sharpe ≥ 0.8

## 출처

- López de Prado, M. *Advances in Financial Machine Learning*, Wiley, 2018. 핵심 요약: [Reasonable Deviations](https://reasonabledeviations.com/notes/adv_fin_ml/)
- Bailey, D.H. & López de Prado, M. [The Deflated Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting, and Non-Normality (2014)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551)
- Bailey, Borwein, López de Prado, Zhu. [The Probability of Backtest Overfitting (2014)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2326253)
- [Purged cross-validation — Wikipedia](https://en.wikipedia.org/wiki/Purged_cross-validation)
- [Deflated Sharpe Ratio — Wikipedia](https://en.wikipedia.org/wiki/Deflated_Sharpe_ratio)
- [Combinatorial Purged Cross-Validation (QuantInsti)](https://blog.quantinsti.com/cross-validation-embargo-purging-combinatorial/)
- [Combinatorial Purged CV Explained (Quantoisseur)](https://quantoisseur.com/2019/11/05/combinatorial-purged-cross-validation-explained/)
- [skfolio CombinatorialPurgedCV API](https://skfolio.org/generated/skfolio.model_selection.CombinatorialPurgedCV.html)
- [Interpretable Hypothesis-Driven Trading: Walk-Forward Validation (arXiv, 2025)](https://arxiv.org/html/2512.12924v1)
- [Backtest overfitting in the ML era (ScienceDirect, 2024)](https://www.sciencedirect.com/science/article/abs/pii/S0950705124011110)
