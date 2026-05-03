---
type: research
id: 41-multi-tf-fractal-trading
name: "Multi-Timeframe Fractal Trading — 멀티프레임 자기유사성과 자석 이론"
sources:
  - "출처: https://youtu.be/j_0FRRgYYN8 (이랑이 인터뷰, 새로운 부자TV, 2026-01)"
  - "Mandelbrot, B. & Hudson, R.L. (2004). The (Mis)Behavior of Markets: A Fractal View of Financial Turbulence. Basic Books."
  - "Peters, E.E. (1994). Fractal Market Analysis: Applying Chaos Theory to Investment and Economics. Wiley."
  - "Calvet, L.E. & Fisher, A.J. (2008). Multifractal Volatility: Theory, Forecasting, and Pricing. Academic Press."
  - "Hurst, H.E. (1951). Long-Term Storage Capacity of Reservoirs. Transactions of the American Society of Civil Engineers, 116, 770-799."
  - "Mantegna, R.N. & Stanley, H.E. (2000). An Introduction to Econophysics: Correlations and Complexity in Finance. Cambridge University Press."
---

# Multi-Timeframe Fractal Trading — 멀티프레임 자기유사성과 자석 이론

> 본 노트는 이슈 #99 영상 인터뷰 ([[iranyi-vwma-2026-04-27]]) 의 "프랙탈 멀티프레임 일치" 와 "이평선 자석 이론" 을 정리한다. 영상의 직관적 주장 (1시간봉 파동이 일봉에서 똑같이 반복) 의 학술 근거 (Mandelbrot fractal markets, Hurst exponent) 와 자동화 가능 feature 를 연결한다.

## 1. 자기유사성 (Self-Similarity) 정의

확률 과정 $X_t$ 가 **자기유사 (self-similar)** 라는 것은 임의의 스케일 $a > 0$ 에 대해:

$$
X_{at} \stackrel{d}{=} a^H \cdot X_t
$$

가 성립함을 의미한다. 여기서 $H$ 는 **Hurst exponent** (Hurst 1951), $\stackrel{d}{=}$ 는 분포 동등성.

- $H = 0.5$: 무기억 (Brownian motion) — 모든 스케일에서 평균 회귀 속도 동일
- $H > 0.5$: 양의 자기상관 (persistence, trending)
- $H < 0.5$: 음의 자기상관 (anti-persistence, mean-reverting)

## 2. Mandelbrot Fractal Market Hypothesis (FMH)

Mandelbrot & Hudson (2004) 와 Peters (1994) 는 EMH (Efficient Market Hypothesis) 의 정규성 가정을 부정하고 **Fractal Market Hypothesis** 를 제안:

1. **이질적 투자자 시간대** (Heterogeneous time horizons): day-trader, swing, long-term — 각각 다른 timeframe 으로 거래
2. **유동성 안정성**: 다양한 시간대 투자자가 동시에 존재할 때 시장은 유동적이고 안정적. 모든 투자자가 같은 시간대로 수렴하면 (e.g., 패닉) 유동성 붕괴
3. **자기유사 가격 패턴**: fractal 구조로 인해 1m, 5m, 15m, 1h, 1d 차트 모양이 통계적으로 유사 (단, 동일하지는 않음)

영상의 화자 표현 (라인 84-91):
> "한시간 봉에서 움직임이 있잖아요. 근데 이게 다른 시간 봉에서 똑같이 간다는 거죠. 그러니까 이런 파동이 있다고 하면은 이게 일봉에서 똑같이 그렇게 갈 수 있다라는. 그니까 같은 그림이 다른 시간대에서 반복된다는 느낌. 약간 멀티버스 같은 느낌"

이는 **scale-invariance** (스케일 불변성) 의 직관적 표현.

## 3. Multifractal Volatility Models

Calvet & Fisher (2008) 의 **MSM (Markov-Switching Multifractal)** 모델은 변동성이 다중 시간 스케일의 충격 (multiplicative cascade) 으로 설명됨을 보임. 이는 단일 GARCH 보다 fat-tail 과 long-memory 를 더 잘 설명.

핵심 함의:
- 시장 변동성은 단일 스케일이 아닌 **여러 frequency band 의 superposition**
- 각 band 는 독립적으로 진화하지만 결합 효과로 fat-tail 생성
- → 멀티프레임 동시 관찰이 통계적으로 정당화됨

본 프로젝트는 MSM 자체를 구현하지 않으나, 멀티프레임 alignment feature (Variant C) 의 이론적 근거로 활용.

## 4. 영상의 자동화 가능 주장

### 4.1 매수 = 작은 프레임, 익절·추세 = 큰 프레임

영상 라인 392-415:
> "매도를 할 때는 조금 더 시간 프레임이 늘리는구나... 점점 보수적으로 갈수록 시간 프레임이 늘어나는 거예요. 실제 매수 매도는 최대한 작은 프레임에서 사고 큰 추세를 볼 때는 시간 [긴 프레임]"

**자동화 feature**:
- 진입: 5m 또는 15m VWMA cross
- 추세 확인: 1h 또는 4h VWMA 정배열 boolean
- Variant C: `multi_tf_alignment(close_1m, volume_1m, higher_tf="1h", vwma_window=100)` — 상위 TF VWMA100 정배열 시 True

### 4.2 이평선 자석 이론 (Mean Reversion to MA)

영상 라인 461-470:
> "캔들은 언젠가 결국에 이평선에 닿는다고 생각하거든. 그러니까 이평선이 강력한 자석이 돼서 캔들이 멀어지면 멀어질수록 강하게 당기는 거죠. 캔들이 이평선과 너무 이격되어 벌어져 있으면 과매수라는 뜻이잖아요. 위로 벌어져 있으면 또 아래로 벌어져 있으면 과매도. 결국 언젠가는 당겨져서 그 평균 회귀를 하는 거죠"

**학술 연결**:
- **Bollinger (1980s)**: Bollinger Bands — MA ± k×σ 의 통계적 평균회귀
- **Cont (2001)** — *Empirical properties of asset returns: stylized facts and statistical issues*: 단기 (수분~수시간) 평균회귀 + 장기 trending 의 stylized fact
- **Lo & MacKinlay (1988)** — *Stock Market Prices Do Not Follow Random Walks*: variance ratio 검정으로 단기 음의 자기상관 입증

**자동화 feature**:
- z-score: $z_t = (P_t - \text{MA}_t) / \sigma_t$, $|z_t| > 2$ 시 mean-reversion 카운터트레이드 후보
- 본 이슈에선 [[12-validation-protocol]] 와 결합한 별도 mean-reversion variant 를 후속 이슈로 분리 (현재는 VWMA cross trending 위주)

### 4.3 영상의 실증 사례 (자석 + VWMA 결합)

영상 라인 511-523:
> "예전에 한번 그 예측을 한게 세이라는 코인이 있거든요... 끌어당기 보칙 위해서 일봉상 200선 닫는다 닫고 [200]선이나 거래량 가중 100평 쯤에 부딪쳐서 저항을 다시 맞을거다. 그래서 그거 대충 계산해 보면 한 30% 정도 나온다 막 했거든요. 근데 실제로 35% 정도 반등을 했어요"

이는 **자석 이론 + 멀티프레임 + VWMA** 를 결합한 1 사례. N=1 sample 로 통계적 유의성 없음.

### 4.4 이평선 경로 예측 (Forward MA Projection)

영상 라인 282-308:
> "차트는 실제로 여기까지만 보여 주잖아요. 그럼 저는 이제 이 순간에 이 연장선을 그리는 거죠. 50선 연장선, 그리고 이 거량 가중 이평도 여기서부터 이제 하락이 시작돼서 쭉 이어져 내려오고 있잖아요. 그럼 여기도 연장선이 되어서 쭉 우하향 하겠구나... 계속 이평선의 흐름을 제가 직접 예측해서 그려보는 거예요 미리"

**자동화 가능 features**:
- `ema_slope_k`: $k$ bar 동안의 EMA 기울기 (linear regression)
- `ema_curvature`: 2차 미분 (slope of slope)
- `ema_proj_n`: $t+N$ 시점 EMA 선형 외삽 추정
- `eta_to_cross`: 캔들-EMA 교차 ETA (현재 추세 유지 가정)

본 프로젝트는 `src/features/ma_projection.py` 에서 구현 (Variant B 의 baseline).

## 5. 한계 및 비판

### 5.1 "파동" 의 정량화 어려움

영상 화자가 사용하는 "파동" 은 시각적·직관적 개념. Elliott Wave (Frost & Prechter 1978) 류의 형식화는 존재하지만 OOS 검증 결과 일관된 alpha 입증 실패 (Kaufman 2013 *Trading Systems and Methods*).

본 프로젝트는 "파동" 자체가 아니라 **상위 TF 정배열 boolean** 만 자동화. 더 복잡한 wave-counting 은 범위 밖.

### 5.2 Fractal Markets 의 검증 가능성

FMH (Peters 1994) 자체는 입증 가능한 가설보다는 **메타이론** (alternative framework). Hurst exponent 측정은 가능하나 사용 가능한 alpha 로 변환은 별도 문제. 단순 $H \neq 0.5$ 만으로 trading edge 확보 불가.

### 5.3 시간 프레임 임의 선택

영상 화자의 1m / 15m / 1h / 4h / 1d 선택은 관습적. 진정한 fractal 이라면 임의의 비율 (예: 7m) 도 동등해야 하나 실제로는 거래 시작/종료 같은 외생 cycles 가 특정 시간대를 특별하게 만듦 (Cont 2001 stylized fact #6 "U-shaped intraday volatility").

### 5.4 Look-ahead 위험 (멀티프레임 resample)

상위 TF 신호를 하위 TF 에 매핑할 때 `resample().last()` 또는 `resample(label='right')` 를 잘못 쓰면 미래 정보 누출. 본 프로젝트는 `multi_tf_alignment` 에서 `label='right', closed='right'` 강제 + `src/signals/lookahead_guard.py::assert_no_lookahead` 단위 테스트.

## 6. 본 프로젝트 활용 (Variant C)

- **`src/features/multi_tf.py::multi_tf_alignment(close_1m, volume_1m, higher_tf="1h", vwma_window=100)`** — 상위 TF VWMA100 정배열 boolean
- 의존: `vwma()` from [[40-vwma-volume-weighted-ma]]
- Variant C = A (vwma_cross) + multi_tf_alignment AND-gate
- 검증: lookahead 없음 단위 테스트 + 1m → 1h 매핑에서 60번째 1m bar 가 첫 1h bar 에 포함되지 않는지 검증

## 관련 노트
- [[iranyi-vwma-2026-04-27]] — 영상 원문 전사
- [[40-vwma-volume-weighted-ma]] — 자매 노트, VWMA 본체
- [[12-validation-protocol]] — multi-TF 시 lookahead 가드 SOP
- [[30-market-regime-detection]] — Hurst / regime 관련
- [[13-feature-alpha-catalog]] — 멀티프레임 momentum 차원 비교

## 출처

1. **이랑이 인터뷰** (2026-01, 새로운 부자TV) — https://youtu.be/j_0FRRgYYN8
2. **Mandelbrot, B. & Hudson, R.L.** (2004). *The (Mis)Behavior of Markets: A Fractal View of Financial Turbulence*. Basic Books. ISBN: 0-465-04355-0.
3. **Peters, E.E.** (1994). *Fractal Market Analysis: Applying Chaos Theory to Investment and Economics*. Wiley. ISBN: 978-0-471-58524-4.
4. **Calvet, L.E. & Fisher, A.J.** (2008). *Multifractal Volatility: Theory, Forecasting, and Pricing*. Academic Press. ISBN: 978-0-12-150013-9.
   - Markov-Switching Multifractal (MSM) 모델
5. **Hurst, H.E.** (1951). *Long-Term Storage Capacity of Reservoirs*. Transactions of the American Society of Civil Engineers, 116, 770-799.
   - Hurst exponent 의 시초 (Nile 강 저수지 연구)
6. **Mantegna, R.N. & Stanley, H.E.** (2000). *An Introduction to Econophysics: Correlations and Complexity in Finance*. Cambridge University Press. ISBN: 978-0-521-62008-7.
   - 금융 시계열의 fractal·power-law 성질 정리
7. **Cont, R.** (2001). *Empirical properties of asset returns: stylized facts and statistical issues*. Quantitative Finance, 1(2), 223-236. https://doi.org/10.1080/713665670
   - 단기 mean-reversion + 장기 trending stylized fact
8. **Lo, A.W. & MacKinlay, A.C.** (1988). *Stock Market Prices Do Not Follow Random Walks: Evidence from a Simple Specification Test*. Review of Financial Studies, 1(1), 41-66.
9. **Frost, A.J. & Prechter, R.R.** (1978). *Elliott Wave Principle*. New Classics Library. — Wave counting 의 한계 비교용
10. **Kaufman, P.J.** (2013). *Trading Systems and Methods* (5th ed.). Wiley. — wave-counting 의 OOS 실패 증례
