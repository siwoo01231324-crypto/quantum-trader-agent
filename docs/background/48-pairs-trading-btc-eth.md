---
type: research
id: 48-pairs-trading-btc-eth
name: "BTC-ETH 페어 트레이딩 — 공적분 기반 시장 중립 전략 (1d)"
created: 2026-04-27
tags: [pairs-trading, cointegration, btc, eth, market-neutral, statistical-arbitrage, 1d]
sources:
  - "Gatev, E., Goetzmann, W.N., Rouwenhorst, K.G. (2006). Pairs Trading: Performance of a Relative-Value Arbitrage Rule. Review of Financial Studies, 19(3), 797-827. https://doi.org/10.1093/rfs/hhj020"
  - "Vidyamurthy, G. (2004). Pairs Trading: Quantitative Methods and Analysis. Wiley. ISBN 0-471-46067-2"
  - "Avellaneda, M. & Lee, J.H. (2010). Statistical arbitrage in the US equities market. Quantitative Finance, 10(7), 761-782. https://doi.org/10.1080/14697680903124632"
  - "Engle, R.F. & Granger, C.W.J. (1987). Co-integration and Error Correction: Representation, Estimation, and Testing. Econometrica, 55(2), 251-276. https://doi.org/10.2307/1913236"
  - "Liu, Y., Tsyvinski, A., Wu, X. (2022). Common Risk Factors in Cryptocurrency. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119"
tested_in: 02_implementation
backtest_period: '2020-01-01 to 2025-12-31'
backtest_asset: BTCUSDT
backtest_timeframe: 4h
variant_id: 'S5'
realized_sharpe: -0.355
realized_mdd: -0.85
realized_mhr: 0.36
n_trades: 420
gate_status: 'failed'
---

# BTC-ETH 페어 트레이딩 — 공적분 기반 시장 중립 전략 (1d)

> 본 노트는 후속 backtest (BTC-ETH@1d, 5년 SOP) 의 사전 등록 가설 S5 이다.
> BTC 와 ETH 간의 공적분 관계를 이용한 시장 중립 pairs trading 의 학술적 근거를 정리한다.
> 검증은 [[12-validation-protocol]] SOP 를 따른다.

---

## 1. 핵심 개념 — 페어 트레이딩

**페어 트레이딩 (Pairs Trading)** 은 두 자산의 가격 비율 (또는 spread) 이 장기 균형 (공적분) 을 유지할 때, 일시적 이탈을 이용해 이익을 취하는 **시장 중립 (market-neutral)** 전략이다.

시장 방향에 관계없이 spread 의 mean-reversion 만 이용하므로 이론적으로 시장 beta = 0.

---

## 2. Gatev-Goetzmann-Rouwenhorst (2006) — 페어 트레이딩의 기원

Gatev, Goetzmann, Rouwenhorst (2006, RFS 19(3) 797-827) 는 미국 주식 (1962-2002) 에서 페어 트레이딩을 최초로 학술적으로 검증:

- **Universe**: NYSE/AMEX/NASDAQ 전체
- **방법**: 12개월 가격 이력으로 "가장 함께 움직이는" 종목 쌍 선택 (SSD 최소화)
- **진입 기준**: 가격 비율이 역사적 표준편차의 2배 이탈 시 진입
- **결과**:
  - 연 excess return: ~11% (거래비용 전), ~6% (거래비용 후, 추정)
  - 전략 Sharpe: ~0.7-1.2 (자기보고 표본 내, 1962-2002, 표 3 참조)
  - 수익의 90%가 같은 업종 내 페어에서 발생

**한계**:
- 1962-2002 미국 주식 — 크립토 직접 적용 불가
- 현대 시장 (2003+) 에서 alpha 감소 (market efficiency 증가, HFT 경쟁)
- 거래비용 후 수익이 크게 감소

---

## 3. Vidyamurthy (2004) — 공적분 방법론

Vidyamurthy, G. (2004) *Pairs Trading: Quantitative Methods and Analysis* (Wiley, ISBN 0-471-46067-2):

- **공적분 (Cointegration)** 개념 (Engle & Granger 1987 기반) 의 페어 트레이딩 적용
- 두 I(1) (단위근) 시계열이 cointegrated 이면 선형 조합이 I(0) (정상 과정) 이 됨:

$$
\text{Spread}_t = \log P_t^{\text{BTC}} - \beta \cdot \log P_t^{\text{ETH}} \sim I(0)
$$

- $\beta$: 공적분 계수 (OLS 또는 Johansen 검정으로 추정)
- spread 가 정상 과정이면 → mean reversion → 진입/청산 가능

---

## 4. Engle & Granger (1987) — 공적분 이론

Engle, Granger (1987, Econometrica 55(2) 251-276) — 공적분 (Cointegration) 이론:
- 두 I(1) 시계열의 선형 조합이 I(0) 이면 공적분 관계 성립
- **Engle-Granger 2단계 검정**: (1) 개별 단위근 검정, (2) 잔차의 ADF 검정
- ECM (Error Correction Model) — 장기 균형으로의 수렴 속도 ($\lambda$) 추정

본 전략의 이론적 기반: $\text{log(BTC)} - \beta \cdot \text{log(ETH)}$ 가 공적분 관계가 있으면 z-score 기반 진입이 통계적으로 정당화됨.

---

## 5. BTC-ETH 공적분 관계 — 실증 배경

### 5.1 상관관계

BTC 와 ETH 는 역사적으로 높은 가격 상관관계를 보임 (암호화폐 시장 공통 요인 — Liu et al. 2022):
- 2017-2024 기간 일간 수익률 상관: 대략 0.7-0.9 (기간별 변동)
- 공통 요인: 규제 뉴스, 기관 투자, macro crypto 수요

### 5.2 공적분 존재 여부

**중요**: 상관관계가 높다고 공적분이 보장되지 않는다. BTC 와 ETH 의 log 가격이 공적분 관계를 갖는지는 실제 ADF/Johansen 검정이 필요 — verify before commit.

기존 연구:
- Alexander & Dimitriu (2005, verify before commit) — 일부 코인 쌍에서 공적분 확인
- 2018 이후 ETH 의 독자적 DeFi 생태계 성장으로 BTC-ETH 관계가 약화 가능성

### 5.3 Spread 구성

$$
z_t = \frac{\text{Spread}_t - \mu_{\text{Spread}}}{\sigma_{\text{Spread}}}
$$

$$
\text{Spread}_t = \log P_t^{\text{BTC}} - \hat{\beta} \cdot \log P_t^{\text{ETH}}
$$

- $\hat{\beta}$: rolling OLS (lookback=60일) 로 동적 추정
- $\mu, \sigma$: 동일 lookback 의 rolling 통계

---

## 6. 전략 설계 (S5 가설)

### 6.1 진입·청산 조건

```
Long spread  (BTC short / ETH long):  z_t > +2.0  →  BTC 매도, ETH 매수
Short spread (BTC long / ETH short): z_t < -2.0  →  BTC 매수, ETH 매도
청산:  |z_t| < 0.5  (spread 수렴)
Stop:  |z_t| > 3.5  (spread 더 벌어짐 → 모델 붕괴 가능성)
```

**진입 z-score = 2.0**: Gatev et al. (2006) 의 2σ 기준.

### 6.2 포지션 사이징

달러 중립 (dollar-neutral):
$$
\text{BTC position size} = \$ N
$$
$$
\text{ETH position size} = \$ N \times \hat{\beta} \times \frac{P_t^{\text{ETH}}}{P_t^{\text{BTC}}}
$$

$\beta$ 를 매일 재계산하여 hedging ratio 업데이트.

---

## 7. 예상 성과

| 항목 | 내용 |
|------|------|
| 예상 Sharpe | 0.5-1.0 (시장 중립이지만 BTC-ETH 강한 상관 → spread 변동성 작음 → alpha 제한) |
| 근거 | Gatev et al. (2006) 미국 주식 ~11% excess return; 크립토는 spread vol 작아 보수적 하향 |
| 표본 기간 | Gatev et al. 1962-2002; 크립토 직접 실증 부재 |
| 자산 클래스 | BTC, ETH (Binance USDT 페어) |

---

## 8. 한계 및 리스크

### 8.1 BTC-ETH Co-movement (Regime Change)

BTC 와 ETH 가 동반 하락·상승 시 spread 가 좁아 trading opportunity 감소. 2022 암호화폐 전반 하락 (LUNA 붕괴, FTX 붕괴) 시 BTC-ETH spread 는 거의 0 — 전략 작동 불가.

### 8.2 공적분 불안정성

$\hat{\beta}$ 는 시간에 따라 변한다:
- 2020 DeFi summer → ETH 독립적 수요 급증 → 공적분 계수 변화
- Rolling window 로 동적 추정하지만 급격한 regime 변화에 적응 지연

### 8.3 작은 Alpha

BTC-ETH 의 spread 변동성이 작아서 z-score 2 도달 빈도가 낮고, 진입 기회가 적음. 연간 거래 횟수 제한 → 통계 검정력 부족 위험.

### 8.4 Transaction Costs

두 자산을 동시에 매수/매도해야 하므로 왕복 거래비용이 2배. Binance taker 0.04% × 2 자산 × 2 (왕복) = 0.16% per trade.

### 8.5 실행 위험 (Execution Risk)

두 자산의 동시 진입/청산이 필요. 한쪽만 체결되는 partial fill 위험. 고유동성 BTC/ETH 에서 낮지만 극단 변동성 시 증가.

---

## 9. 백테스트 사전 등록 가설

- **자산**: BTCUSDT + ETHUSDT (Binance, 1d)
- **기간**: 5년 (2020-01 ~ 2024-12)
- **가설**: rolling OLS (lookback=60d) + z-score ≥ 2 진입 조건이 거래비용 포함 Sharpe ≥ 0.4 달성
- **사전 검증**: Engle-Granger 또는 Johansen 공적분 검정 통과 (p < 0.05) 가 선행 조건
- **검증**: PurgedKFold (n_splits=5) + DSR ≥ 0.95 + PBO ≤ 0.2
- **파라미터 탐색 범위**: z_entry ∈ {1.5, 2.0, 2.5}, lookback ∈ {30, 60, 90} (9 조합, N=9)

---

## 관련 노트

- [[12-validation-protocol]] — PurgedKFold + DSR/PBO 검증 SOP
- [[35-meta-labeling-lopez-de-prado]] — 메타라벨러로 spread 이탈 신호 필터링
- [[44-time-series-momentum-crypto]] — S1: 방향성 전략 (S5 와 portfolio 상관 확인)
- [[45-donchian-breakout-turtle]] — S2: trend-following (S5 와 독립성)
- [[46-ema-pullback-mean-reversion]] — S3: mean reversion (S5 와 유사 메커니즘, 다른 구현)
- [[47-funding-rate-carry-perpetual]] — S4: carry 전략 (S5 와 포트폴리오 조합)
- [[40-vwma-volume-weighted-ma]] — VWMA 의 정보 이론적 근거 (spread 필터 보조 가능)
- [[42-cross-sectional-momentum-crypto]] — BTC-ETH cross-sectional 상관 맥락

---

## 출처

1. **Gatev, E., Goetzmann, W.N., Rouwenhorst, K.G.** (2006). *Pairs Trading: Performance of a Relative-Value Arbitrage Rule*. Review of Financial Studies, 19(3), 797-827. https://doi.org/10.1093/rfs/hhj020
   - Table 3: 연 excess return ~11% (거래비용 전), Sharpe 추정 0.7-1.2 (1962-2002, 미국 주식)
   - §3: 진입 기준 2σ deviation, 청산 조건

2. **Vidyamurthy, G.** (2004). *Pairs Trading: Quantitative Methods and Analysis*. Wiley. ISBN 0-471-46067-2.
   - Ch.5-7: 공적분 기반 페어 트레이딩 방법론, OLS hedging ratio

3. **Engle, R.F. & Granger, C.W.J.** (1987). *Co-integration and Error Correction: Representation, Estimation, and Testing*. Econometrica, 55(2), 251-276. https://doi.org/10.2307/1913236
   - 공적분 이론의 기원, Engle-Granger 2단계 검정

4. **Avellaneda, M. & Lee, J.H.** (2010). *Statistical arbitrage in the US equities market*. Quantitative Finance, 10(7), 761-782. https://doi.org/10.1080/14697680903124632
   - OU 과정 기반 mean-reversion 모델 (spread 의 반감기 추정)

5. **Liu, Y., Tsyvinski, A., Wu, X.** (2022). *Common Risk Factors in Cryptocurrency*. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119
   - BTC-ETH 공통 risk factor 구조 (co-movement 맥락)
