---
type: research
id: 30-market-regime-detection
name: "시장 체제 탐지 — HMM · Markov Switching · Vol Regime · Trend-Range 분류"
sources:
  - https://en.wikipedia.org/wiki/Regime_switching
  - https://www.jstor.org/stable/1912559
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2956760
  - https://arxiv.org/abs/2104.05543
  - https://hmmlearn.readthedocs.io/
  - https://www.statsmodels.org/stable/generated/statsmodels.tsa.regime_switching.markov_regression.MarkovRegression.html
---

# 시장 체제 탐지 — HMM · Markov Switching · Vol Regime · Trend-Range 분류

> [[08-strategy-paradigms]] 는 "시장 체제(regime) 변화 시 규칙이 무용화" 를 규칙기반 전략의 약점으로 지적한다. [[12-validation-protocol]] §4 롤백 트리거는 "체제 변화" 를 6개월 Sharpe 괴리 > 1.5 로 사후 감지한다. [[19-portfolio-risk]] 는 DCC-GARCH 를 한 줄 언급. 그러나 **실시간·사전 체제 탐지 방법론** 은 정리 안 됨. 본 노트는 (1) 4가지 체제 개념, (2) 탐지 알고리즘 3가지, (3) 전략 스위칭 규칙, (4) 본 프로젝트 적용 계획을 정리한다.

---

## 1. "Regime" 정의 — 4가지 축

시장 체제는 단일 스칼라가 아니다. 전략마다 관심 축이 다르다.

| 축 | 정의 | 주요 지표 | 대표 전략 반응 |
|----|------|-----------|----------------|
| **Volatility Regime** | 실현·내재 변동성 레벨 | VIX·realized vol·GARCH 추정 σ | Vol Targeting ([[20-position-sizing]]) 스케일 |
| **Trend vs Range** | 추세 강도 | ADX, Hurst exponent, variance ratio | 모멘텀 on/off (`momo-btc-v2`) |
| **Risk-On / Risk-Off** | 자산 간 상관·팩터 노출 | 섹터 분산·안전자산 프리미엄 | 포트폴리오 rebalance |
| **Bull / Bear / Sideways** | 장기 수익률 방향 | 200-day MA·drawdown | 전략 universe 축소 |

본 프로젝트 **1차 우선순위는 Volatility + Trend-Range** — 본 프로젝트의 전략들이 이 두 축에 가장 민감.

---

## 2. 탐지 알고리즘 — 3가지 계보

### 2.1 Hidden Markov Model (HMM)

**가정**: 관측된 수익률은 숨은 상태 (hidden state) `S_t ∈ {1, 2, ..., K}` 에 조건부로 생성됨. 상태는 Markov 과정 `P(S_t | S_{t-1})` 을 따름.

**가정 2**: 각 상태는 고유 분포 `(μ_k, σ_k)` 를 가짐. 상태 2개이면 보통 **저변동성 (μ>0, σ 작음) vs 고변동성 (μ<0, σ 큼)** 으로 수렴.

**학습**: Baum-Welch (EM algorithm) — `hmmlearn` 로 5~10 라인 구현
```python
from hmmlearn.hmm import GaussianHMM
model = GaussianHMM(n_components=2, covariance_type="full",
                    n_iter=100, random_state=42)
model.fit(returns.reshape(-1, 1))
states = model.predict(returns.reshape(-1, 1))   # 각 시점의 추정 상태
```

**장점**:
- 상태 수 소수 (보통 2~3) 로 해석 용이
- 사전 라벨 없이 unsupervised
- `transmat_` 에서 지속성 (`p_{k→k}`) 직접 추출 — 상태 체류 시간 추정

**단점**:
- 상태 수 K 는 사전 결정 (BIC/AIC 로 선택하지만 주관적)
- Gaussian 가정은 수익률 fat tail 에 부적합 (Student-t HMM 대안)
- 상태 **정의는 데이터가 결정** → "bull / bear" 라는 해석이 사후적

**본 프로젝트 활용**: `returns.rolling(252).apply(hmm_state)` 로 1년 rolling HMM. 상태 전환 감지 시 [[risk-rule-dsl]] 의 drawdown 한도 감소.

### 2.2 Markov Switching Regression (MSR)

Hamilton (1989) 의 고전 모델. HMM 과 유사하나 **회귀식 계수가 상태별로 다름**:

```
r_t = μ(S_t) + σ(S_t) · ε_t
μ(S_t=1) = α_1 + β_1 · X_{t-1}
μ(S_t=2) = α_2 + β_2 · X_{t-1}
```

`statsmodels.tsa.regime_switching.MarkovRegression` 으로 구현.

**장점**:
- 외생변수 (거시, 팩터) 를 회귀에 포함 가능 → 해석력 ↑
- 표준오차·p-value 등 통계적 추론 제공
- 변동성 전환 (Markov Switching GARCH) 로 확장 가능

**단점**:
- 계산 비용 HMM 대비 높음
- 비정상성 (unit root) 이면 추정 붕괴 — 수익률 (정상) 에는 OK, 가격에는 NO

### 2.3 Rule-based / Threshold 분류 (경량)

통계 모델 없이 지표 기반 분류. 해석성 극단적으로 높음.

```python
def classify_regime(df):
    realized_vol = df.returns.rolling(20).std() * (252 ** 0.5)
    adx = df.adx_14
    regime = "range"
    if adx > 25:
        regime = "trend"
    if realized_vol > realized_vol.rolling(252).quantile(0.85):
        regime = "high_vol"    # 변동성 우선
    return regime
```

**장점**: 실시간 계산, 해석 즉시, 백테스트 재현 완벽
**단점**: 임계값이 sample dependent, 다축 결합 시 복잡

**본 프로젝트 1차 도입은 Rule-based** → Phase 2 에서 HMM 도입 검증.

---

## 3. 변동성 체제 (Vol Regime) 상세

변동성 추정기 비교:

| 방법 | 수식 요약 | 반응속도 | 본 프로젝트 적합성 |
|------|----------|---------|-------------------|
| **Realized Vol** (stddev) | `log_returns.rolling(20).std() * sqrt(252)` | 중 | 기본값 |
| **EWMA σ** (RiskMetrics λ=0.94) | `σ²_t = 0.94 σ²_{t-1} + 0.06 r²_t` | 빠름 | [[20-position-sizing]] vol targeting 입력 |
| **GARCH(1,1)** | `σ²_t = ω + α r²_{t-1} + β σ²_{t-1}` | 중 | 추론 ·예측에 우수 |
| **HAR-RV** | 일·주·월 RV 회귀 | 빠름 | 일간 예측 |
| **Implied Vol (VIX·VKOSPI)** | 옵션 내재 | 선행적 | 사전신호 (다만 한국 VKOSPI 유동성 낮음) |

### 3.1 체제 임계값

VKOSPI (한국) 기준 역사적 분위수:
- `vkospi < 15` → 저변동성
- `15 ≤ vkospi ≤ 25` → 정상
- `vkospi > 25` → 고변동성
- `vkospi > 40` → 위기 (2008 리먼·2020 코로나 수준)

본 프로젝트에서 사용 권장: **EWMA σ 기반 percentile** (rolling 252일의 85/95%ile)

---

## 4. 추세 vs 횡보 (Trend-Range) 상세

### 4.1 지표

- **ADX(14)**: ≥ 25 추세장, < 20 횡보 ([[13-feature-alpha-catalog]] §1.3)
- **Variance Ratio (Lo-MacKinlay 1988)**: VR(k) = Var(r^k) / (k · Var(r)). VR > 1 추세 (positive autocorrelation), VR < 1 평균회귀
- **Hurst Exponent (H)**: H > 0.5 추세, H < 0.5 평균회귀, H ≈ 0.5 랜덤워크. DFA (Detrended Fluctuation Analysis) 로 추정

### 4.2 의사결정 매트릭스 (제안)

| Vol Regime \ Trend | Low Vol | Normal | High Vol |
|---------------------|---------|--------|----------|
| **Trend (ADX≥25)** | 모멘텀 풀 사이즈 | 모멘텀 half | 모멘텀 quarter |
| **Range** | 평균회귀 풀 | 평균회귀 half | 전략 off |

[[20-position-sizing]] 의 Half Kelly + Vol Targeting 과 직결. 체제 전환 감지 시 사이징 스케일 **점진적** (1 거래일 당 max 25%) 변경해 과도한 turnover 방지.

---

## 5. 본 프로젝트 아키텍처 제안

### 5.1 Phase 1 — Rule-based 실시간 분류 (MVP)

```python
# services/regime_classifier/ (신규 모듈)
class RegimeClassifier:
    def classify(self, snapshot: MarketSnapshot) -> Regime:
        vol = snapshot.ewma_sigma_20
        adx = snapshot.adx_14
        vol_pct = snapshot.vol_percentile_252d

        if vol_pct > 0.85:
            return Regime(vol="high", trend="unknown")
        trend = "trend" if adx >= 25 else "range"
        return Regime(vol="normal" if vol_pct < 0.65 else "elevated",
                      trend=trend)
```

출력은 [[observability]] 의 `qta_market_regime` 게이지 라벨로 송출. 전략은 이 게이지를 구독해 자체 정책 적용.

### 5.2 Phase 2 — HMM 보조 (offline nightly)

- 매일 장 마감 후 최근 1년 returns 로 `GaussianHMM(K=2)` 학습
- 상태 전환 감지 시 [[observability]] 에 `qta_regime_transition_total` counter 증가
- 7일 이동평균 상태가 바뀌면 리밸런스 예약 (다음 거래일)

### 5.3 Phase 3 — 체제별 멀티 전략 운영

- 전략 A: 저변동 + 추세 전용 (`momo-btc-v2` 변형)
- 전략 B: 고변동 + 횡보 전용 (평균회귀)
- 자본 배분 ([[20-position-sizing]]) 을 체제 확률에 비례

### 5.4 전략 스위칭 규칙 (중요)

- **Hysteresis 필수** — 체제 전환 감지 후 최소 N 거래일 (N=3 권장) 유지되어야 실제 스위칭 → 잡음 방지
- **점진적 sizing** — 체제 A → B 전환 시 포지션은 3거래일 선형 감소
- **Kill-switch 연계** — 고변동성 위기 체제 진입 시 [[risk-rule-dsl]] 의 drawdown 임계 자동 하향 (5% → 3%)
- **사람 승인 게이트** — 체제 변경 자동 스위칭은 [[29-paper-to-live-protocol]] Phase 2 까지만. 실자금 Phase 3 부터는 사람 최종 승인

---

## 6. 평가·검증

### 6.1 체제 분류 정확도는 정의 어려움

Ground truth 가 없음 → "맞다/틀리다" 평가 불가. 대신 다음 지표 사용:

- **Regime persistence**: `transmat_[k,k]` — 같은 체제에 머무는 확률. 너무 낮으면 잡음 분류
- **Out-of-sample Sharpe 개선**: 체제별 전략 스위칭 vs 단일 전략의 Sharpe 차이
- **Turnover 제약**: 스위칭 과다로 비용 초과하는지

### 6.2 [[12-validation-protocol]] 와의 연계

§3 SOP 의 "Train → Validation → Test 분할" 에 체제 정보 추가:
- Train 에서 학습한 체제 분류기 → Validation 에서 out-of-sample 평가
- CPCV fold 분할 시 **같은 체제가 train/test 에 균형 배치** 되도록 stratified 분할

---

## 7. 한국 시장 특이점

1. **VKOSPI 유동성 낮음** — 선진국 VIX 수준 대비 내재변동성의 신뢰도 낮음. Realized vol 기반 보조 필수
2. **공매도 금지·재개 이벤트** — 2020, 2021, 2024 공매도 금지 구간은 **구조적 체제 변화**. 자동 플래그 필요
3. **개인 투자자 비중 과다** — 종목별로 체제 변화가 섹터 수준보다 빠른 경우 (테마주) → 섹터 평균 지표 부족
4. **장 개시·마감 동시호가** ([[07-market-microstructure-basics]]) — 분류기 입력에서 이 구간 제외 필수
5. **규제 발표 (금투세 등)** — 외생적 체제 shock. 규칙기반에 사람 수동 플래그 허용

---

## 8. 알려진 함정

1. **Regime hindsight bias** — 사후적으로 "아 그때가 bear 였구나" 는 쉬움. 실시간 분류의 지연이 1개월+ 일 수 있음
2. **Kelly 와의 상호작용** — Kelly 가 μ 와 σ 에 민감. 체제 전환 직후 σ 추정이 불안정하면 사이징 폭발 위험 → **체제 전환 직후 7일은 보수적 고정 사이징** 권장
3. **Lookback 윈도우 의존성** — 252일 rolling 은 1년 전 극단 이벤트가 percentile 에 계속 영향. 윈도우 바꾸면 결과 크게 변동
4. **과적합** — K=5 이상 HMM 은 거의 항상 과적합. BIC 로 K=2 또는 K=3 이 합리적
5. **레짐은 자산별** — 크립토 체제 vs 주식 체제는 다름. 같은 분류기로 모두 감쌀 수 없음

---

## 9. 로드맵 (본 프로젝트 타임라인)

- **Phase 1 (즉시)**: Rule-based `RegimeClassifier` 구현 + `qta_market_regime` 메트릭 송출
- **Phase 2 (4주)**: HMM offline 분류기, transition 감지 알림
- **Phase 3 (8주)**: [[20-position-sizing]] 이 regime state 를 소비해 스케일 자동 조정
- **Phase 4 (3~6개월)**: 체제별 멀티 전략 운영 + CPCV validation

---

## 10. 체크리스트

- [ ] 변동성 추정기: EWMA σ (λ=0.94) 고정
- [ ] 추세 지표: ADX(14) 기본 + Hurst 보조
- [ ] 체제 수 K ≤ 3 (BIC 로 선택)
- [ ] Hysteresis N=3 거래일 유지 조건
- [ ] 체제 전환 직후 7일 사이징 고정 (Kelly 불안정 방지)
- [ ] [[observability]] 에 `qta_market_regime` 게이지·전환 counter 추가
- [ ] 공매도 금지·규제 이벤트는 **수동 플래그** 로 체제 분류기 overrule
- [ ] 스위칭 모든 결정 로그 저장 + [[12-validation-protocol]] §4 롤백 트리거와 연계

---

## 관련 노트

- [[08-strategy-paradigms]] — 체제 변화가 규칙기반 전략의 약점
- [[12-validation-protocol]] — §4 체제 변화 롤백 트리거를 본 노트가 상세화
- [[19-portfolio-risk]] — DCC-GARCH 시변 상관이 본 노트의 vol regime 연장선
- [[20-position-sizing]] — 체제별 사이징 스케일 조정
- [[13-feature-alpha-catalog]] — ADX·EWMA σ·Hurst 지표 계산
- [[risk-rule-dsl]] — 체제별 drawdown·exposure 한도 자동 조정
- [[observability]] — 체제 메트릭 송출
- [[29-paper-to-live-protocol]] — Phase 4 M2 이상에서 체제 다양성 통과 요구
- [[07-market-microstructure-basics]] — 동시호가 구간은 분류기 입력 제외

---

## 출처

- Hamilton, J. D. (1989). *A New Approach to the Economic Analysis of Nonstationary Time Series and the Business Cycle*. Econometrica, 57(2). <https://www.jstor.org/stable/1912559>
- Ang, A. & Bekaert, G. (2002). *Regime Switches in Interest Rates*. Journal of Business & Economic Statistics.
- Lo, A. W. & MacKinlay, A. C. (1988). *Stock Market Prices Do Not Follow Random Walks*. Review of Financial Studies. (Variance Ratio 테스트)
- Ang, A. & Timmermann, A. (2012). *Regime Changes and Financial Markets*. NBER Working Paper. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2034712>
- Nystrup, P. et al. (2021). *Dynamic portfolio optimization with multivariate Hidden Markov Models*. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2956760>
- Jha, K. et al. (2021). *Deep-Learning-Based Financial Market Regime Detection Survey*. arXiv:2104.05543. <https://arxiv.org/abs/2104.05543>
- `hmmlearn` GaussianHMM — <https://hmmlearn.readthedocs.io/>
- `statsmodels` MarkovRegression — <https://www.statsmodels.org/stable/generated/statsmodels.tsa.regime_switching.markov_regression.MarkovRegression.html>
- RiskMetrics Technical Document (1996) — EWMA λ=0.94. <https://www.msci.com/documents/10199/5915b101-4206-4ba0-aee2-3449d5c7e95a>
- Wikipedia — *Markov Switching Multifractal / Regime-switching model*. <https://en.wikipedia.org/wiki/Regime_switching>
- Peng, C.-K. et al. (1994). *Mosaic organization of DNA nucleotides* — DFA (Hurst 추정) 원전
- 한국거래소 — *VKOSPI 지수 개요*. <https://www.krx.co.kr/>
