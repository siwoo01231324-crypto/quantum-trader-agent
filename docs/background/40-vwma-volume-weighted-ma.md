---
type: research
id: 40-vwma-volume-weighted-ma
name: "VWMA — 거래량 가중 이동평균: 이론과 실증"
sources:
  - "출처: https://youtu.be/j_0FRRgYYN8 (이랑이 인터뷰, 새로운 부자TV, 2026-01)"
  - "López de Prado, M. (2018). Advances in Financial Machine Learning. Wiley. Ch.2 (Information-driven bars)"
  - "Mandelbrot, B. (1963). The Variation of Certain Speculative Prices. Journal of Business, 36(4), 394-419."
  - "Wikipedia — Volume-weighted average price. https://en.wikipedia.org/wiki/Volume-weighted_average_price"
  - "Berkowitz, S.A., Logue, D.E., Noser, E.A. (1988). The Total Cost of Transactions on the NYSE. Journal of Finance, 43(1), 97-112."
---

# VWMA — 거래량 가중 이동평균 (Volume-Weighted Moving Average)

> 본 노트는 이슈 #99 의 사전 리서치 — 영상 인터뷰 ([[iranyi-vwma-2026-04-27]]) 의 "제1비법" 인 VWMA100 의 이론적 근거와 실증 한계를 정리한다.

## 1. 정의

**VWMA** 는 단순이동평균(SMA) 의 가격 가중치를 **거래량(volume)** 으로 대체한 변형이다.

$$
\text{VWMA}_t(w) = \frac{\sum_{i=t-w+1}^{t} P_i \cdot V_i}{\sum_{i=t-w+1}^{t} V_i}
$$

- $P_i$: $i$ 시점 가격 (보통 종가)
- $V_i$: $i$ 시점 체결량
- $w$: 윈도우 길이 (lookback)

핵심 차이: SMA 는 모든 bar 가 동일 가중치이지만, VWMA 는 **거래량이 큰 bar 에 더 많은 가중치** 를 부여한다. 따라서 거래량이 거의 없는 noise bar 의 영향이 자동으로 줄어들고, 거래량이 폭발한 정보-bearing bar 가 평균선을 더 빠르게 움직인다.

VWAP (Volume-Weighted Average Price, [Berkowitz et al. 1988](https://www.jstor.org/stable/2328325)) 와 형태는 같으나, VWAP 은 **장중 누적** (intraday cumulative) 인 반면 VWMA 는 **rolling window**.

## 2. 정보 이론적 근거

### 2.1 López de Prado AFML — Information-driven bars

López de Prado (2018) AFML Ch.2 는 시간 기반 bar (1m, 1h) 가 **정보 균등성** 을 보장하지 못한다고 주장한다. 같은 1m 안에 1억원 체결되든 1만원 체결되든 같은 bar 로 취급되므로, "정보가 거의 없는 시간대" 와 "정보가 풍부한 시간대" 가 노이즈/시그널 비율을 왜곡한다.

대안으로 다음을 제시:
- **Tick bars**: 일정 체결 횟수마다 bar 생성
- **Volume bars**: 일정 체결량마다 bar 생성
- **Dollar bars**: 일정 체결 대금마다 bar 생성

VWMA 는 시간 bar 를 그대로 두되, **bar 별 가중치** 를 거래량으로 부여하여 같은 효과를 부분적으로 달성한다. 따라서 VWMA 는 information-driven bars 의 "lite" 형태로 볼 수 있다.

### 2.2 Mandelbrot 의 변동성 — 거래량과 정보

Mandelbrot (1963) 은 "거래량은 정보 흐름의 proxy" 라고 주장. 가격 변동의 분포가 정규분포가 아닌 fat-tail 인 이유는 정보 도착이 Poisson process 가 아니라 burst 형태이기 때문. 거래량이 폭발하는 시점이 정보 도착 시점과 강한 상관관계를 가진다 (Mixture of Distributions Hypothesis, MDH).

따라서 VWMA 의 거래량 가중은 **정보 흐름 가중** 의 implicit 적용이다.

## 3. 영상 인터뷰 (이랑이) 매핑

영상 ([[iranyi-vwma-2026-04-27]]) 에서 화자 이랑이는 VWMA 를 "제1비법" 으로 제시:

> "거래량 가중 이동 평균이라는 거를 쓰거든. 이거는 제가 수익을 좀 많이 내고 난 다음에 더 수익을 늘릴 수 없을까 하다가 찾아낸 거거든요. 근데 이것저것 다 넣어 봤는데 결국 백선이 가장 신뢰도가 높아서"

**라인업 비교** (영상 라인 241-242):
- VWMA 멀티플 럼 75 / 50 / 100 / 200 모두 테스트
- 75/50: "그냥 일반 이평선이랑 거의 비슷하게 움직여요" (정보 추가 없음)
- 200: "이격이 벌어진다고잖아 시도가 좀 떨어지는 느낌" (반응 지연)
- **100: "가장 실내(신뢰)도 높아"** → 채택

**효과 주장** (영상 라인 251-253):
> "이거 쓰고 나서 제가 월 3억씩 벌다가 저거 딱 처음 적용하고 그 달 13억"

**용도** (영상):
1. **진입 시그널**: 역배열 (close < VWMA100) 구간에서 close 가 VWMA100 을 상향 돌파 시 매수
2. **익절 트리거**: 상향 후 VWMA100 에서 저항받을 때 익절
3. **손절 라인**: VWMA100 하향 재돌파 시 손절 ("마지막 탈출 기회")
4. **자석 이론** (별도 노트 [[41-multi-tf-fractal-trading]] 참조): 이격이 클수록 평균회귀 압력

## 4. 수학적 성질

### 4.1 SMA 와의 관계

거래량이 모든 bar 에 동일하게 분포하면 ($V_i = V$ for all $i$):

$$
\text{VWMA}_t(w) = \frac{V \cdot \sum_{i=t-w+1}^{t} P_i}{V \cdot w} = \frac{1}{w} \sum_{i=t-w+1}^{t} P_i = \text{SMA}_t(w)
$$

→ **VWMA 는 SMA 의 일반화** (volume 이 상수일 때 동일).

### 4.2 인과성 (Causality)

VWMA 는 $t$ 시점에 $[t-w+1, t]$ 의 데이터만 사용 → **lookahead 없음** (단, $t$ 시점의 bar 가 미완료 상태에서 사용 시 부분 정보 누출 위험. 백테스트에서는 close 사용 시 bar 종료 후만 사용해야 함, 신호 발생은 다음 bar 에서).

### 4.3 윈도우 선택의 trade-off

| 윈도우 | 장점 | 단점 |
|--------|------|------|
| 짧음 (예: 20-50) | 빠른 반응 | 노이즈 많음, false signal |
| 중간 (예: 100) | 균형 | (영상 채택) |
| 긺 (예: 200-400) | 안정 | 지연, 이격 누적 |

영상 화자의 100 채택은 post-hoc selection (사후적 선택) 이며 backtest 검증 없는 단일 사례.

## 5. 한계 및 비판

### 5.1 표본 편향 (Single-case Survivor Bias)

영상의 "월 3억 → 13억" 은:
- **표본 N=1** (개인 1인의 1개월)
- 성공자 인터뷰 — 동일 기법 사용 후 실패한 N 명은 미관측
- 통제 집단(control group) 부재
- 유의수준 (p-value) 없음

생존자 편향 (Mlodinow 2008, Mauboussin 2012) 의 전형: "성공자가 무엇을 했는가" 는 인과 추론 자료가 아니다.

### 5.2 Post-hoc 윈도우 선택 (Overfitting Risk)

화자가 75/50/100/200 중 100 을 채택한 것은 backtest 결과 보고 사후적 선택. 이는 **data snooping** 의 전형 (Lo & MacKinlay 1990). 본 이슈 (#99) 는 [[12-validation-protocol]] 의 PurgedKFold + DSR 보정으로 이를 검증한다.

### 5.3 Narrative Fallacy

영상은 "거래량 가중 = 정보 가중 = 더 의미 있는 기준선" 이라는 plausible 한 narrative 를 제공하지만, 이는 **사후 합리화** 가능성 (Taleb 2007 *The Black Swan* Ch.6 "The Narrative Fallacy"). 검증해야 할 것은 narrative 가 아니라 OOS Sharpe.

### 5.4 거래량 데이터 품질

암호화폐 거래소의 거래량은 wash trading 으로 부풀려진 경우 빈번함 (Cong et al. 2023, *Crypto Wash Trading*, Management Science). VWMA 는 reported volume 을 신뢰하므로 wash-trade-heavy 자산에서는 의미 왜곡.

## 6. 본 프로젝트 활용 (Variant A)

이슈 #99 의 [[01_plan]] Stage 4 에서 VWMA100 cross 를 baseline (Variant A) 으로 사용:

- `src/features/vwma.py::vwma(close, volume, window=100)` — 결정론적 계산
- `src/features/vwma.py::vwma_cross(close, volume, window=100)` — "golden" / "dead" 시그널, `shift(1)` 로 인과성
- Variant H 의 full stack 에 vwma_cross 가 baseline 으로 포함

검증: PurgedKFold (n_splits=5, embargo_frac=0.01) + DSR ≥ 0.95 + PBO ≤ 0.2 게이트 ([[12-validation-protocol]] §3.7).

## 관련 노트
- [[iranyi-vwma-2026-04-27]] — 영상 원문 전사 + 8 기법 매핑
- [[12-validation-protocol]] — DSR/PBO 검증 SOP
- [[35-meta-labeling-lopez-de-prado]] — AFML 라벨링 이론 (cv 인프라와 연결)
- [[13-feature-alpha-catalog]] — 피처 카탈로그 (mom/value/quality factor 와의 차원 비교)
- [[41-multi-tf-fractal-trading]] — VWMA 의 멀티프레임 적용 (자매 노트)
- [[42-cross-sectional-momentum-crypto]] — 크립토 RS 필터 (Variant E)
- [[43-orderbook-flow-features]] — 호가창 마이크로구조 feature (Variant G/H)

## 출처

1. **이랑이 인터뷰** (2026-01, 새로운 부자TV) — https://youtu.be/j_0FRRgYYN8
   - 본 노트의 모든 영상 인용 (라인 241-253 등) 은 자동자막 전사 ([[iranyi-vwma-2026-04-27]]) 에서 발췌
2. **López de Prado, M.** (2018). *Advances in Financial Machine Learning*. Wiley. ISBN: 978-1-119-48208-6.
   - Ch.2 §2.3 "Information-driven bars" (pp.25-36) — VWMA 의 정보 이론적 정당화
3. **Mandelbrot, B.** (1963). *The Variation of Certain Speculative Prices*. Journal of Business, 36(4), 394-419. https://doi.org/10.1086/294632
   - 거래량과 정보 흐름의 관계, fat-tail 가격 분포의 기원
4. **Berkowitz, S.A., Logue, D.E., Noser, E.A.** (1988). *The Total Cost of Transactions on the NYSE*. Journal of Finance, 43(1), 97-112. https://doi.org/10.1111/j.1540-6261.1988.tb02591.x
   - VWAP 의 시초 — 기관 트레이더의 체결 품질 기준
5. **Wikipedia — Volume-weighted average price.** https://en.wikipedia.org/wiki/Volume-weighted_average_price
6. **Lo, A.W. & MacKinlay, A.C.** (1990). *Data-Snooping Biases in Tests of Financial Asset Pricing Models*. Review of Financial Studies, 3(3), 431-467.
   - Post-hoc parameter selection 의 통계적 위험
7. **Cong, L.W., Li, X., Tang, K., Yang, Y.** (2023). *Crypto Wash Trading*. Management Science, 69(11), 6427-6454.
   - 암호화폐 거래량 데이터 품질 한계
