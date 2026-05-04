---
type: research
id: 49-hmm-regime-detection
name: "HMM Regime Detection — Hamilton 1989 기반 시장 체제 판별"
sources:
  - https://doi.org/10.2307/1912559
  - https://doi.org/10.1093/rfs/15.4.1137
  - https://doi.org/10.1111/jofi.13105
  - https://hmmlearn.readthedocs.io/
---

# HMM Regime Detection — Hamilton 1989 기반 시장 체제 판별

> 이슈 #173. PR #172의 PBO=0.714 미통과를 regime-conditional strategy switching으로 공략하기 위한 학술 배경 정리.

---

## 1. Hamilton (1989) — 2-State Markov Switching Model

### 핵심 아이디어

관측된 시계열 (수익률)이 숨은 상태(hidden state) `S_t`에 조건부로 생성된다고 가정. `S_t`는 Markov chain을 따르며, 각 상태에서 수익률의 분포 파라미터가 다르다:

```
r_t | S_t = k ~ N(μ_k, σ²_k)
P(S_t = j | S_{t-1} = i) = p_{ij}  (transition probability)
```

2-state 모델에서:
- **State 1 (expansion)**: μ > 0, σ 작음 — 안정적 성장기
- **State 2 (contraction)**: μ < 0, σ 큼 — 변동성 확대 · 하락기

### 추정 방법

- **Baum-Welch (EM)**: E-step에서 forward-backward로 상태 확률 추정, M-step에서 파라미터 갱신
- **Viterbi decoding**: 가장 가능성 높은 상태 시퀀스 복원

### 본 프로젝트 적용

- BTC@4h 수익률에 `GaussianHMM(n_components=2)` 적용
- low-vol state → S2c (Donchian trend-following)
- high-vol state → S4 (funding carry)
- 근거: 추세장에서 trend-following 우세, 변동성 확대기에 carry (funding premium) 우세

---

## 2. Ang & Bekaert (2002) — Regime-Dependent Asset Allocation

### 핵심 기여

- 국제 주식 시장에서 regime-dependent factor returns 모델링
- 2-state Markov switching으로 bull/bear regime 분리
- **핵심 발견**: regime-conditional portfolio가 unconditional 대비 유의미한 Sharpe 개선
- Transition probability의 persistence가 높을수록 (p_{kk} > 0.95) 전략 효과 커짐

### 본 프로젝트 시사점

- transition matrix의 persistence (diagonal 값) ≥ 0.90이면 전략 스위칭에 유의미
- 낮은 persistence → 잡음 분류 → 과도한 turnover → 비용으로 alpha 소멸
- Hysteresis 규칙 필요: 최소 N 바 연속 같은 state여야 실제 스위칭

---

## 3. Liu, Tsyvinski & Wu (2022) — Crypto Risk Premia

### 핵심 기여

- 암호화폐 시장의 risk premia를 3가지 factor로 분해
- Regime-dependent factor loading 확인: 불확실성(VIX analogues) 상승 시 crypto momentum factor 약화
- **Funding rate**가 crypto-specific carry factor 역할 — perpetual futures basis와 연관

### 본 프로젝트 시사점

- BTC funding carry (S4)는 crypto carry factor의 proxy
- Regime 전환 시 momentum (S2c) ↔ carry (S4) 최적 배분이 변화
- 3-state 모델 (R3)에서 crash state를 별도로 분리해 flat position 취하는 근거

---

## 4. 구현 세부사항

### 4.1 hmmlearn 라이브러리

```python
from hmmlearn.hmm import GaussianHMM
model = GaussianHMM(
    n_components=2,
    covariance_type="full",
    n_iter=100,
    random_state=42,
)
model.fit(returns.reshape(-1, 1))
states = model.predict(returns.reshape(-1, 1))
```

- `covariance_type="full"`: 각 state별 독립 공분산 (1D에서는 variance)
- `n_iter=100`: EM 최대 반복. monitor_.converged로 수렴 확인
- `random_state=42`: 재현성 (#99 불변식)

### 4.2 상태 수 (K) 선택

- K=2: vol regime (low/high) — 가장 robust, 해석 명확
- K=3: bull/bear/crash — 해석력 ↑, 과적합 위험 ↑
- K≥4: 거의 항상 과적합 (BIC 감소 정체)
- 본 프로젝트: K=2 (R2)와 K=3 (R3)만 사전등록

### 4.3 Known Limitations

1. **Gaussian 가정**: 수익률 fat tail에 부적합. Student-t HMM이 대안이나 hmmlearn 미지원
2. **Lookback 의존성**: EM 학습 윈도우 크기에 따라 상태 할당 변동
3. **Label switching**: EM 초기화에 따라 state 0/1 의미 뒤바뀔 수 있음 → variance 기준 재라벨링 필수
4. **Non-causal risk**: fit()은 전체 데이터 사용 → backtest에서 in-sample 오염 가능 → rolling fit 권장 (후속)

---

## 5. 사전등록 Variant Matrix

| ID | 정의 | 학술 근거 |
|----|------|-----------|
| R0 | S2c always | baseline (no regime) |
| R1 | S4 always | baseline 2 |
| R2 | HMM-2state vol regime | Hamilton (1989) |
| R3 | HMM-3state bull/bear/crash | Ang & Bekaert (2002) |
| R4 | Threshold switch | rule-based baseline |
| R5 | Ensemble (R2+R3+R4 vote) | model averaging |

---

## 관련 노트

- [[30-market-regime-detection]] — 본 노트의 선행 배경 (Vol Regime, Trend-Range, Rule-based)
- [[12-validation-protocol]] — PBO/CSCV 검증 인프라
- [[08-strategy-paradigms]] — 체제 변화가 규칙기반 전략의 약점

## 출처

- Hamilton, J.D. (1989). A New Approach to the Economic Analysis of Nonstationary Time Series. Econometrica 57(2), 357-384. <https://doi.org/10.2307/1912559>
- Ang, A. & Bekaert, G. (2002). International Asset Allocation with Regime Shifts. RFS 15(4), 1137-1187. <https://doi.org/10.1093/rfs/15.4.1137>
- Liu, Y., Tsyvinski, A., Wu, X. (2022). Common Risk Factors in Cryptocurrency. JF 77(2), 1133-1177. <https://doi.org/10.1111/jofi.13105>
- hmmlearn documentation. <https://hmmlearn.readthedocs.io/>
