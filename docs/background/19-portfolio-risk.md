---
type: research
id: 19-portfolio-risk
name: "포트폴리오 레벨 리스크 — 공분산 추정·상관 집중·VaR/CVaR·팩터 노출"
sources:
  - https://www.econ.uzh.ch/static/wp/econwp122.pdf
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1681697
  - https://www.math.hkust.edu.hk/~maykwok/courses/Probability_Theory/Optimization%20of%20conditional%20value-at-risk.pdf
  - https://link.springer.com/article/10.1007/s10436-007-0079-x
  - https://en.wikipedia.org/wiki/Fama%E2%80%93French_three-factor_model
---

# 포트폴리오 레벨 리스크 — 공분산 추정·상관 집중·VaR/CVaR·팩터 노출

> 개별 전략의 MDD 캡 ([[max-drawdown-5pct]]) 만으로는 여러 전략·종목을 동시에 운용할 때 리스크가 폭주한다. 본 노트는 포트폴리오 레벨에서 필요한 네 가지 리스크 도구를 정리한다: (1) 안정적 공분산 추정, (2) 상관 집중도 제한, (3) 포트폴리오 VaR/CVaR, (4) 팩터 노출 관리. [[risk-rule-dsl]] v2 에서 "변동성 기반 동적 한도" 로 확장될 때의 이론 근거다.

---

## 1. 왜 포트폴리오 레벨인가

개별 전략 단위 리스크(일간 손실 한도, MDD 캡)는 필요조건이지만 충분조건은 아니다. 세 가지 실패 모드가 남는다.

1. **Latent correlation** — 서로 다른 전략(예: 모멘텀 + 롱숏)이 동일한 숨은 팩터(예: 저유동성 베타)에 같이 노출돼 위기 시 동조 하락
2. **Concentration creep** — 리밸런스 과정에서 특정 섹터·팩터 비중이 누적
3. **Tail co-movement** — 평상시 상관 0.2 였던 자산 쌍이 위기 시 0.9 로 치솟음 (Longin & Solnik 2001)

[[risk-rule-dsl]] 의 `per_portfolio.max_gross_exposure_krw`, `sector_limits` 는 1·2 에 부분 대응하지만 3 에는 무력하다. 본 노트의 도구가 그 공백을 채운다.

---

## 2. 공분산 행렬 추정 — 샘플 공분산의 병폐

포트폴리오 최적화 ([[14-quantum-poc-design]] 의 QAOA PoC 포함) 는 공분산 $\Sigma$ 를 입력으로 받는다. 자산 수 $N$ 이 관측 기간 $T$ 에 비해 크면 샘플 공분산 $\hat{\Sigma}_{\text{sample}}$ 은 **심하게 과적합** 된다.

### 2.1 샘플 공분산의 문제

- $N > T$ 일 때 $\hat{\Sigma}_{\text{sample}}$ 는 **비역행렬(singular)** → Markowitz 평균-분산 최적화가 불가능
- $N \approx T$ 여도 조건수가 폭발적으로 커져 역행렬이 불안정
- 실제 조언: 일간 데이터 20년 (약 5,000거래일) 이면 $N \leq 100$ 수준까지 안전, KOSPI 전종목 2,500개에는 턱없이 부족

### 2.2 Ledoit-Wolf 선형 축소 (Linear Shrinkage)

Ledoit & Wolf (2003, 2004) 는 샘플 공분산과 구조화된 타겟(보통 대각행렬 또는 상수 상관 모델) 의 볼록 조합을 최적 계수로 결합.

$$
\hat{\Sigma}_{LW} = (1 - \alpha) \hat{\Sigma}_{\text{sample}} + \alpha \, F
$$

- $F$ = 타겟 행렬 (예: 대각항만 = 평균 분산, 나머지 = 0)
- $\alpha \in [0, 1]$ = analytic 공식으로 최적값 계산 (데이터 기반, 하이퍼파라미터 아님)

효과:
- 조건수 감소 → 역행렬 안정
- 샘플링 에러로 인한 "가짜 상관" 제거
- Markowitz 최적화에서 **out-of-sample Sharpe 가 샘플 공분산 대비 15~30% 개선** 으로 보고됨 (Ledoit-Wolf 2003 p.5)

`sklearn.covariance.LedoitWolf` 구현 검증됨. [[14-quantum-poc-design]] 의 Σ 추정 경로에서 이미 사용 중.

### 2.3 대안

| 방법 | 요약 | 사용처 |
|------|------|--------|
| **Oracle Approximating Shrinkage (OAS)** | Gaussian 가정 하에 LW 보다 타이트한 축소 계수 | 짧은 윈도우 (2년 이하) |
| **Constant Correlation Model** | 모든 쌍의 상관을 평균 상관으로 대체 | 섹터 내 동질성 높을 때 |
| **Factor-based Σ** | $\Sigma = B \Sigma_f B^\top + D$ (팩터 모형) | 대형 universe (N > 500) |
| **DCC-GARCH** | 시변 상관 | 이벤트 트레이딩·레짐 전환 |

KOSPI 중대형주 universe (N ≈ 200) + 일간 5년 데이터 기준, **LW 가 default** 로 적합하다.

---

## 3. 상관 기반 집중도 제한

[[risk-rule-dsl]] 의 `sector_limits` 는 명목 섹터 라벨에 의존한다. 하지만 실제 상관은 라벨을 무시한다 — 예: 2020년 3월 코로나 쇼크 때 거의 모든 섹터가 동시에 폭락.

### 3.1 Effective Number of Bets (ENB)

Meucci (2009) 가 제안한 PCA 기반 다변화 지표:

$$
\text{ENB} = \exp\left( - \sum_k p_k \log p_k \right), \quad p_k = \frac{\lambda_k w_k^2}{\sum_j \lambda_j w_j^2}
$$

- $\lambda_k$ = 공분산 행렬의 k번째 고유값
- $w_k$ = 포트폴리오의 주성분 k 에 대한 노출

ENB=1 → 사실상 단일 팩터 베팅 (완전 집중). ENB=N → 완전 분산. 포트폴리오 리밸런스 시 ENB ≥ 0.3·N 을 하한으로 강제하면 상관 집중을 구조적으로 막을 수 있다.

### 3.2 Cluster-based concentration cap

계층적 클러스터링 (HRP 의 첫 단계, [[20-position-sizing]] 참조) 으로 종목을 상관 기반 클러스터로 묶은 후 클러스터당 최대 비중을 제한. 섹터 라벨이 틀려도 실제 상관이 높으면 같은 클러스터로 묶이므로 강건하다.

---

## 4. 포트폴리오 VaR / CVaR

VaR 과 CVaR 은 실현 손실 분포의 꼬리를 직접 제약한다. [[12-validation-protocol]] §4 의 롤백 트리거 "일간 손실 > VaR(99%) × 1.5" 가 바로 이 도구를 전제로 한다.

### 4.1 정의

- **VaR(α)** = 손실 분포의 α-분위수 (예: VaR(99%) = 99%확률로 이보다 덜 잃음)
- **CVaR(α)** = VaR(α) 를 초과하는 손실의 평균 (Expected Shortfall, ES 와 동일)

Artzner et al. (1999) 의 일관성(coherent) 공리에서 VaR 은 **sub-additivity 를 만족하지 않아** 비일관적 — 포트폴리오 분산 시 VaR 이 오히려 증가하는 경우 발생. CVaR 은 일관적.

**결론: 본 프로젝트는 CVaR(97.5%) 을 기본 리스크 측정치로 사용** (Basel III FRTB 와 동일 기준).

### 4.2 추정 방법 비교

| 방법 | 가정 | 장점 | 한계 |
|------|------|------|------|
| **Parametric** | 수익률 ~ 정규 | 빠름, 해석 용이 | 꼬리 과소 추정 |
| **Historical** | 과거 분포 = 미래 분포 | 가정 최소 | 샘플 작으면 노이즈 |
| **Monte Carlo** | 지정 분포 + Σ | 유연 (옵션 포함 가능) | 계산 비용, 분포 선택 편향 |
| **EVT (POT)** | 극단값은 Pareto | 꼬리 특화 | 복잡, tuning 필요 |

Phase 1 규칙기반 전략 ([[12-validation-protocol]] §5) 은 **Historical (N=1,000일) + 극단구간은 EVT 보조** 조합을 권장.

### 4.3 CVaR 최적화

Rockafellar & Uryasev (2000) 는 CVaR 최소화 문제를 선형계획으로 변환하는 공식을 제시:

$$
\min_w \text{CVaR}_\alpha(w) = \min_{w, \zeta} \zeta + \frac{1}{(1-\alpha)T} \sum_{t=1}^T [L_t(w) - \zeta]_+
$$

평균-CVaR 최적화는 평균-분산 최적화의 현대적 대안이며, 특히 **비정규 수익률·옵션 포함 포트폴리오** 에서 우월하다. cvxpy·PyPortfolioOpt 에 구현 제공.

---

## 5. 팩터 노출 관리

개별 종목의 베타 뿐 아니라 **스타일 팩터** (가치·모멘텀·저변동성·규모·퀄리티) 노출을 측정·제한하면 체계적 리스크를 명시적으로 통제할 수 있다.

### 5.1 Fama-French 3/5 팩터 모형

$$
r_i - r_f = \alpha_i + \beta_i^{\text{MKT}} \text{MKT} + \beta_i^{\text{SMB}} \text{SMB} + \beta_i^{\text{HML}} \text{HML} + \varepsilon_i
$$

- MKT: 시장 초과수익
- SMB: 소형주 − 대형주 (규모)
- HML: 고BM − 저BM (가치)
- (5-factor: + RMW 수익성, CMA 투자성)

한국 팩터 데이터: Kenneth French Library 는 글로벌만, **KAP (한국재무학회) 웹사이트 또는 금융공학연구소 (FnGuide) 데이터 사용** 필요.

### 5.2 팩터 노출 제한 규칙 (제안)

[[risk-rule-dsl]] v2 확장 후보:

```yaml
factor_limits:
  HML: [-0.3, 0.3]     # 가치 팩터 베타 범위
  SMB: [-0.2, 0.4]     # 소형주 바이어스는 제한적 허용
  MOM: [-0.5, 0.5]     # 모멘텀 팩터 (Carhart 4-factor)
  beta_market: [0.7, 1.3]
```

회귀는 90거래일 롤링으로 일 1회 재계산, 위반 시 `reduce` 액션.

---

## 6. 본 프로젝트 적용 로드맵

1. **v1 (현재)**: [[risk-rule-dsl]] 의 `sector_limits` + drawdown + 개별 종목 비중만. 포트폴리오 리스크는 사람이 모니터링.
2. **v2 (이 sprint 이후)**:
   - LW 공분산 추정 → ENB 지표를 [[observability]] 대시보드에 노출
   - 일간 historical CVaR(97.5%) 계산 → [[12-validation-protocol]] §4 롤백 트리거 연동
3. **v3**:
   - 팩터 노출 회귀 + `factor_limits` YAML 확장
   - CVaR 최적화 기반 동적 리밸런스 (월 1회)

## 7. 체크리스트 (운영)

- [ ] 공분산 추정: LW shrinkage 고정 (샘플 공분산 금지)
- [ ] 최소 universe 다변화: ENB ≥ 0.3 × N 유지
- [ ] 리스크 측정치: CVaR(97.5%) 을 primary, VaR 는 참고용
- [ ] 팩터 노출: 3-factor 최소, 월 1회 회귀 재추정
- [ ] 위반 시 [[risk-rule-dsl]] `reduce` 액션 → [[kill-switch-runbook]] 연계

---

## 참고 노트

- [[12-validation-protocol]] — 백테스트에서 VaR/CVaR 를 산출·롤백 트리거로 사용
- [[14-quantum-poc-design]] — QAOA 포트폴리오 최적화에서 LW 공분산을 입력으로 사용
- [[20-position-sizing]] — 개별 포지션 크기 결정 (본 노트의 "Σ" 를 소비자)
- [[risk-rule-dsl]] — 본 노트의 지표들을 YAML DSL 로 제약화
- [[max-drawdown-5pct]] — 개별 전략 레벨 리스크 룰 (본 노트의 보완 관계)
- [[observability]] — 포트폴리오 VaR/CVaR·ENB 대시보드 대상
- [[kill-switch-runbook]] — 포트폴리오 리스크 위반 시 실행 절차

---

## 출처

- Ledoit, O. & Wolf, M. (2003). *Improved estimation of the covariance matrix of stock returns with an application to portfolio selection*. Journal of Empirical Finance, 10(5), 603–621. <https://www.econ.uzh.ch/static/wp/econwp122.pdf>
- Ledoit, O. & Wolf, M. (2004). *Honey, I shrunk the sample covariance matrix*. Journal of Portfolio Management, 30(4), 110–119.
- Artzner, P., Delbaen, F., Eber, J.-M., & Heath, D. (1999). *Coherent measures of risk*. Mathematical Finance, 9(3), 203–228.
- Rockafellar, R. T. & Uryasev, S. (2000). *Optimization of Conditional Value-at-Risk*. Journal of Risk, 2, 21–42. <https://www.math.hkust.edu.hk/~maykwok/courses/Probability_Theory/Optimization%20of%20conditional%20value-at-risk.pdf>
- Meucci, A. (2009). *Managing Diversification*. Risk, 22(5), 74–79. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1358533>
- Longin, F. & Solnik, B. (2001). *Extreme correlation of international equity markets*. Journal of Finance, 56(2), 649–676.
- Fama, E. F. & French, K. R. (1993). *Common risk factors in the returns on stocks and bonds*. Journal of Financial Economics, 33(1), 3–56. 요약: <https://en.wikipedia.org/wiki/Fama%E2%80%93French_three-factor_model>
- Basel Committee on Banking Supervision (2019). *Minimum capital requirements for market risk* (FRTB). <https://www.bis.org/bcbs/publ/d457.htm>
- `sklearn.covariance.LedoitWolf` — <https://scikit-learn.org/stable/modules/generated/sklearn.covariance.LedoitWolf.html>
- PyPortfolioOpt CVaR optimization — <https://pyportfolioopt.readthedocs.io/en/latest/EfficientFrontier.html#efficient-cvar>
