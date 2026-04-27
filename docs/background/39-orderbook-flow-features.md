---
type: research
id: 39-orderbook-flow-features
name: "Orderbook Flow Features — OBI / OFI / Microprice / Hawkes intensity"
sources:
  - "출처: https://youtu.be/j_0FRRgYYN8 (이랑이 인터뷰, 새로운 부자TV, 2026-01)"
  - "Cont, R., Kukanov, A., Stoikov, S. (2014). The Price Impact of Order Book Events. Journal of Financial Econometrics, 12(1), 47-88."
  - "Stoikov, S. (2018). The Micro-Price: A High-Frequency Estimator of Future Prices. Quantitative Finance, 18(12), 1959-1966."
  - "Bacry, E., Mastromatteo, I., Muzy, J.-F. (2015). Hawkes Processes in Finance. Market Microstructure and Liquidity, 1(1)."
  - "Kyle, A.S. (1985). Continuous Auctions and Insider Trading. Econometrica, 53(6), 1315-1335."
  - "Cartea, Á., Jaimungal, S., Penalva, J. (2015). Algorithmic and High-Frequency Trading. Cambridge University Press."
---

# Orderbook Flow Features — OBI / OFI / Microprice / Hawkes Intensity

> 본 노트는 이슈 #99 영상 인터뷰 ([[iranyi-vwma-2026-04-27]]) 의 "호가창 힘 빠짐" 직관을 정량화하는 microstructure feature 를 정리한다. 영상 화자의 정성적 발언을 OBI (Order Book Imbalance), OFI (Order Flow Imbalance), Microprice gap, Hawkes process arrival intensity 로 자동화한다. Variant G (orderbook flow only) 와 Variant H (full stack) 의 핵심 입력.

## 1. 영상의 직관 → 정량화 격차

영상 라인 185-187:
> "이때는 그냥 호가창 보고 그냥 힘 빠진다 싶을 때 그냥 파는, 딱히 그걸 차해서 이제 찾진 않고"

영상은 "힘 빠진다" 를 정성적으로만 표현. 본 노트는 학술 microstructure 문헌으로 이를 형식화:

| 영상 직관 | 자동화 feature | 학술 근거 |
|---------|---------------|---------|
| 매수 압력 약화 | OBI 감소 | Cartea et al. (2015) Ch.3 |
| 매도 호가 두꺼워짐 | OFI 음수 누적 | Cont, Kukanov, Stoikov (2014) |
| 다음 체결 방향 (mid 위/아래) | Microprice - mid gap | Stoikov (2018) |
| 체결 빈도 변화 | Trade arrival intensity | Hawkes process (Bacry et al. 2015) |

## 2. Order Book Imbalance (OBI)

### 2.1 정의

호가창 1차 (top-of-book) 의 매수·매도 잔량 비율:

$$
\text{OBI}_t = \frac{V^{\text{bid}}_t - V^{\text{ask}}_t}{V^{\text{bid}}_t + V^{\text{ask}}_t}
$$

- $V^{\text{bid}}_t$: $t$ 시점 best bid 잔량
- $V^{\text{ask}}_t$: $t$ 시점 best ask 잔량
- 범위: $[-1, +1]$
- 양수: 매수 압력 (다음 체결이 ask 쪽 hit 가능성 ↑)
- 음수: 매도 압력

### 2.2 다단 OBI (Multi-Level)

상위 $L$ 단계 잔량의 가중 합:

$$
\text{OBI}_t^{(L)} = \frac{\sum_{l=1}^L w_l (V^{\text{bid},l}_t - V^{\text{ask},l}_t)}{\sum_{l=1}^L w_l (V^{\text{bid},l}_t + V^{\text{ask},l}_t)}
$$

가중치 $w_l$ 은 가격 거리에 반비례 (top level 이 가장 영향 큼).

### 2.3 실증 (Cartea-Jaimungal-Penalva 2015)

OBI 와 미래 단기 (수초~수분) 가격 변동의 양의 상관:
- $\text{Corr}(\text{OBI}_t, \Delta P_{t+\tau}) > 0$ for $\tau \in [1s, 60s]$
- 효과는 $\tau$ 가 길어질수록 감소 (mean-reverting)

본 프로젝트는 1m 집계 OBI (`obi_mean`) 를 Variant G/H 에 사용.

## 3. Order Flow Imbalance (OFI) — Cont, Kukanov, Stoikov (2014)

### 3.1 정의

OBI 가 *level* 의 imbalance 라면, OFI 는 *변화량* 의 누적:

각 호가창 이벤트 $i$ (시간 $\tau_i$) 에서:

$$
e_i = \begin{cases}
+\Delta V^{\text{bid}}_i & \text{if bid update (add or fill)} \\
-\Delta V^{\text{ask}}_i & \text{if ask update}
\end{cases}
$$

OFI 는 이를 누적 합:

$$
\text{OFI}_t = \sum_{i: \tau_i \leq t} e_i \cdot \text{sign}_i
$$

부호:
- bid 추가 (+) / bid 취소 (-) / bid 체결 (-)
- ask 추가 (-) / ask 취소 (+) / ask 체결 (+)

### 3.2 핵심 결과

Cont, Kukanov, Stoikov (2014) 의 핵심 발견:
- **Mid-price 변화 ≈ OFI 의 선형 함수**: $\Delta \text{Mid}_t = \beta \cdot \text{OFI}_t / (\text{depth}) + \epsilon_t$
- $R^2 \approx 0.5-0.7$ (단기 1초~10초 horizon)
- 거래량 (volume) 보다 OFI 가 더 강한 가격 영향 예측 변수 — Kyle (1985) lambda 보다 정확

### 3.3 본 프로젝트 적용

`src/features/orderbook_flow.py::order_flow_imbalance(bid_vol, ask_vol, bid_vol_prev, ask_vol_prev)` — 1초 또는 1분 집계 OFI. 1m 집계 시 `ofi_cumsum` 컬럼.

## 4. Microprice (Stoikov 2018)

### 4.1 정의

Mid-price ($P^{\text{mid}}_t = (P^{\text{bid}}_t + P^{\text{ask}}_t)/2$) 는 잔량을 무시한다. Microprice 는 OBI 가중:

$$
P^{\text{micro}}_t = \frac{V^{\text{ask}}_t \cdot P^{\text{bid}}_t + V^{\text{bid}}_t \cdot P^{\text{ask}}_t}{V^{\text{bid}}_t + V^{\text{ask}}_t}
$$

직관: bid 잔량이 많으면 ($V^{\text{bid}} \gg V^{\text{ask}}$) microprice 가 ask 에 가까워짐 (다음 체결이 ask hit 일 가능성 high → "fair price" 가 ask 쪽).

### 4.2 Microprice - Mid Gap

$$
\text{gap}_t = P^{\text{micro}}_t - P^{\text{mid}}_t
$$

- $\text{gap}_t > 0$: 매수 압력 (next move up likely)
- $\text{gap}_t < 0$: 매도 압력
- 범위: 약 $[-\text{spread}/2, +\text{spread}/2]$

### 4.3 Stoikov (2018) 핵심

Microprice 가 mid-price 보다 다음 체결가의 unbiased estimator 에 가깝다는 입증. 특히 spread 가 wide 한 종목·시간대에서 mid-price 의 bias 가 큼.

### 4.4 본 프로젝트 적용

`src/features/orderbook_flow.py::microprice_mid_gap(bid_price, ask_price, bid_vol, ask_vol)` — 1초 또는 1분 집계.

## 5. Hawkes Process — Trade Arrival Intensity

### 5.1 Hawkes Process 정의

자기 자극 (self-exciting) 점 과정: 과거 이벤트가 미래 이벤트의 도착 강도를 증가시킴.

$$
\lambda(t) = \mu + \sum_{t_i < t} \alpha \cdot e^{-\beta (t - t_i)}
$$

- $\mu$: baseline intensity
- $\alpha$: 자극 강도
- $\beta$: 감쇠율
- $t_i$: 과거 이벤트 시점

Bacry, Mastromatteo, Muzy (2015) 는 금융에 Hawkes 적용:
- 거래 도착이 self-exciting (한 거래가 다음 거래를 부른다)
- mid-price 변화도 self-exciting + cross-exciting (up-tick → down-tick 가능성 변화)

### 5.2 Trade Arrival Rate as Feature

본 프로젝트는 풀 Hawkes fitting 대신 단순 proxy 사용:
- 1m 윈도우 내 체결 횟수 (n_trades)
- 1m 평균 거래대금 (avg trade size)
- 거래 도착 간격 분포 (median, std)

이를 `aggregate_orderbook_features()` 에서 컬럼으로 추가 가능 (선택).

## 6. Kyle (1985) Lambda — 가격 영향 측정

Kyle (1985) 의 lambda:
- $\Delta P = \lambda \cdot Q$
- $Q$: net order flow (signed volume)
- $\lambda$: price impact 계수
- 정보 비대칭이 클수록 lambda 큼

OFI 는 Kyle lambda 의 high-frequency 일반화 (Cont et al. 2014).

## 7. 영상 매핑 정리

영상의 단편적 호가창 발언:

| 영상 라인 | 발언 | 자동화 feature |
|---------|------|---------------|
| 185-187 | "호가창 보고 그냥 힘 빠진다 싶을 때" | OBI 감소 + OFI 음수 누적 |
| 134-141 | "거래량 팡 터졌다가 안 갈래들 안 간다고" | Trade arrival intensity 감소 |
| 196-220 | "매물대가 되어 버려서 거기를 뚫고 올라가지 못하고" | OBI 비대칭 + microprice 하향 |

→ 본 이슈 Variant G = `OBI + OFI + microprice_gap` AND-gate

## 8. 1m 집계 vs 1s Raw Tick — Trade-off

**Decision** (이슈 #99 Open Q3 해결): 1m 집계 우선, 1s raw zstd parquet 보존.

| 차원 | 1s raw | 1m 집계 |
|------|--------|---------|
| 정보 손실 | 최소 | OBI/OFI 의 within-minute dynamics 손실 |
| 스토리지 | 수십 GB / 일 / 심볼 | ~수백 MB / 일 / 심볼 |
| 연산 비용 | 高 (Hawkes fitting 등) | 低 |
| Feature 적합 | Hawkes intensity, micro-jump | OBI mean, OFI cumsum, microprice mean |

본 이슈 1m 집계 로 시작, 후속 이슈에서 1s raw 활용한 micro-jump prediction 별도.

## 9. 한계 및 비판

### 9.1 거래소·exchange 별 차이

호가창 깊이·체결 logic 차이 (lit pool vs dark pool, KRX vs Binance). 본 프로젝트는 Binance L2 tick (paper broker #80) 만 대상. KRX 는 별도 후속.

### 9.2 Adversarial Quoting

HFT 의 spoofing / quote stuffing 으로 OBI / OFI 가 manipulated 될 수 있음. 본 프로젝트는 1m 집계라 분 단위 noise 평균화로 부분 완화.

### 9.3 Variant G 의 데이터 의존

Variant G/H 는 L2 tick 데이터 의존. #80 paper broker 미가용 시 `DATA_UNAVAILABLE` 플래그 → DSR N 동적 감소 ([[01_plan]] Stage 4).

### 9.4 오버피팅

Microstructure feature 는 매우 high-frequency → 데이터 스누핑 위험 높음. PurgedKFold (embargo_frac=0.01) + DSR 보정 필수 ([[12-validation-protocol]]).

## 10. 본 프로젝트 활용 (Variant G + H)

`src/features/orderbook_flow.py`:
- `order_book_imbalance(bid_vol, ask_vol)` — OBI ∈ [-1, 1]
- `order_flow_imbalance(bid_vol, ask_vol, bid_vol_prev, ask_vol_prev)` — OFI cumulative
- `microprice_mid_gap(bid_price, ask_price, bid_vol, ask_vol)` — gap ∈ [-spread/2, +spread/2]
- `aggregate_orderbook_features(orderbook_1s, resample_freq="1min")` — 1s → 1m, label='right' closed='right' 인과

Variant G = A + (OBI + OFI + microprice_gap AND-gate)
Variant H = full stack 9 features (G 포함)

## 관련 노트
- [[iranyi-vwma-2026-04-27]] — 영상 원문
- [[36-vwma-volume-weighted-ma]] — Variant A baseline
- [[37-multi-tf-fractal-trading]] — multi-TF 보완
- [[38-cross-sectional-momentum-crypto]] — RS 필터 (Variant E)
- [[12-validation-protocol]] — 검증 SOP
- [[13-feature-alpha-catalog]] — feature catalog 차원 비교

## 출처

1. **이랑이 인터뷰** (2026-01, 새로운 부자TV) — https://youtu.be/j_0FRRgYYN8
2. **Cont, R., Kukanov, A., Stoikov, S.** (2014). *The Price Impact of Order Book Events*. Journal of Financial Econometrics, 12(1), 47-88. https://doi.org/10.1093/jjfinec/nbt003
3. **Stoikov, S.** (2018). *The Micro-Price: A High-Frequency Estimator of Future Prices*. Quantitative Finance, 18(12), 1959-1966. https://doi.org/10.1080/14697688.2018.1489139
4. **Bacry, E., Mastromatteo, I., Muzy, J.-F.** (2015). *Hawkes Processes in Finance*. Market Microstructure and Liquidity, 1(1), 1550005. https://doi.org/10.1142/S2382626615500057
5. **Kyle, A.S.** (1985). *Continuous Auctions and Insider Trading*. Econometrica, 53(6), 1315-1335. https://doi.org/10.2307/1913210
6. **Cartea, Á., Jaimungal, S., Penalva, J.** (2015). *Algorithmic and High-Frequency Trading*. Cambridge University Press. ISBN: 978-1-107-09114-6.
   - Ch.3 Limit Order Book modelling, OBI 통계량
7. **Hasbrouck, J.** (2007). *Empirical Market Microstructure: The Institutions, Economics, and Econometrics of Securities Trading*. Oxford University Press.
8. **Easley, D., López de Prado, M., O'Hara, M.** (2012). *Flow Toxicity and Liquidity in a High-Frequency World*. Review of Financial Studies, 25(5), 1457-1493.
   - VPIN (Volume-synchronized Probability of Informed Trading) — OFI 의 친척
