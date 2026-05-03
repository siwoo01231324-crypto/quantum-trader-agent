---
type: research
id: 46-ema-pullback-mean-reversion
name: "EMA200 Pullback / RSI 과매도 — 평균회귀 스윙 전략 (1d)"
created: 2026-04-27
tags: [mean-reversion, ema, rsi, pullback, swing, 1d, btc]
sources:
  - "Wilder, J.W. (1978). New Concepts in Technical Trading Systems. Trend Research. ISBN 0-894-59013-1, pp.63-70: RSI 정의"
  - "Cont, R. (2001). Empirical properties of asset returns: stylized facts and statistical issues. Quantitative Finance, 1(2), 223-236. https://doi.org/10.1080/713665670"
  - "Avellaneda, M. & Lee, J.H. (2010). Statistical arbitrage in the US equities market. Quantitative Finance, 10(7), 761-782. https://doi.org/10.1080/14697680903124632"
  - "Lo, A.W., Mamaysky, H., Wang, J. (2000). Foundations of Technical Analysis: Computational Algorithms, Statistical Inference, and Empirical Implementation. Journal of Finance, 55(4), 1705-1765. https://doi.org/10.1111/0022-1082.00265"
  - "Poterba, J.M. & Summers, L.H. (1988). Mean Reversion in Stock Prices: Evidence and Implications. Journal of Financial Economics, 22(1), 27-59. https://doi.org/10.1016/0304-405X(88)90021-9"
tested_in: 02_implementation
backtest_period: '2020-01-01 to 2025-12-31'
backtest_asset: BTCUSDT
backtest_timeframe: 4h
variant_id: 'S3'
realized_sharpe: 0.473
realized_mdd: -0.392
realized_mhr: 0.1
n_trades: 42
gate_status: 'failed-low-mhr'
---

# EMA200 Pullback / RSI 과매도 — 평균회귀 스윙 전략 (1d)

> 본 노트는 후속 backtest (BTC@1d, 5년 SOP) 의 사전 등록 가설 S3 이다.
> 상승 추세 내 pullback 에서 진입하는 mean-reversion 전략의 학술적 근거를 정리한다.
> 검증은 [[12-validation-protocol]] SOP 를 따른다.

---

## 1. 핵심 개념 — Mean Reversion

**Mean reversion** 은 가격이 장기 평균에서 벗어났을 때 다시 회귀하는 경향이다. 단기 autocorrelation 이 음수임을 뜻한다.

### 1.1 Poterba & Summers (1988)

Poterba & Summers (1988, JFE 22(1) 27-59) — 미국 주식 (1871-1985) 에서:
- 단기 (1-5년) 수익률의 **negative serial correlation** 발견
- "Price levels tend to revert toward a fundamental value" 결론
- 단, 장기 (3-5년) 스케일 → 단기 스윙에 직접 적용 시 표본 기간 차이 주의

### 1.2 Cont (2001) — Stylized Facts

Cont (2001, QF 1(2) 223-236) 는 수십 개 자산의 실증적 특성을 정리:
- **Stylized Fact #5**: Short-term negative autocorrelation in returns (단기 반전)
- **Stylized Fact #6**: U-shaped intraday volatility (open/close 변동성 高)
- 단기 (분~일 단위) 수익률의 음의 자기상관 → mean reversion 의 통계적 근거

### 1.3 Avellaneda & Lee (2010)

Avellaneda, Lee (2010, QF 10(7) 761-782) — 미국 주식 시장 통계적 차익거래:
- 개별 주식의 ETF 대비 이격 (spread) 을 OU (Ornstein-Uhlenbeck) 과정으로 모델링
- **z-score > 2** 구간에서의 mean-reversion entry 가 통계적으로 유의
- Sharpe ~1.44 (거래비용 전, 2000-2005, 미국 주식)

본 전략의 RSI < 30 조건은 이 z-score 진입 논리의 proxy 로 해석 가능.

---

## 2. RSI (Relative Strength Index)

### 2.1 Wilder (1978) 정의

Wilder (1978) *New Concepts in Technical Trading Systems* (ISBN 0-894-59013-1, pp.63-70):

$$
\text{RS} = \frac{\text{Average Gain}_{n}}{\text{Average Loss}_{n}}
$$
$$
\text{RSI} = 100 - \frac{100}{1 + \text{RS}}
$$

- 기본 window: n=14 (Wilder 원본)
- RSI > 70: overbought (과매수)
- RSI < 30: oversold (과매도)

**주의**: RSI < 30 은 단순 threshold 이며 Wilder 는 "역추세 진입 신호" 로 의도했으나, 강한 하락 추세에서는 RSI 30 이 지속될 수 있음 (trend persistence vs reversion).

### 2.2 Lo, Mamaysky, Wang (2000)

Lo, Mamaysky, Wang (2000, JF 55(4) 1705-1765) — 기술적 분석 패턴의 통계적 유의성 검증:
- 패턴 인식 (head-and-shoulders, double-top 등) 이 조건부 수익률 분포를 바꾼다
- RSI 기반 oversold 신호의 단기 수익률은 랜덤 대비 통계적 차이 존재 (단, magnitude 는 작음)

---

## 3. EMA (Exponential Moving Average) — 추세 필터

### 3.1 EMA 정의

$$
\text{EMA}_t(\alpha) = \alpha \cdot P_t + (1-\alpha) \cdot \text{EMA}_{t-1}
$$

- $\alpha = 2/(n+1)$, n=200 → $\alpha = 0.00995$
- EMA200 (1일) ≈ 200영업일 ≈ 약 10개월 추세

**EMA200 의 역할**: 장기 추세 방향 필터.
- Close > EMA200 → 상승 추세 확인 → pullback 진입 허용
- Close < EMA200 → 하락 추세 → 롱 진입 금지 (long-only 전략)

이는 "추세 방향으로만 mean-reversion" 을 취하는 **trend-filtered mean reversion** 이다.

### 3.2 이랑이 영상 연결

[[iranyi-vwma-2026-04-27]] 의 "이평선 자석 이론" ([[40-vwma-volume-weighted-ma]] §3) 과 일맥상통:
> "이격이 클수록 평균회귀 압력이 강하다" — 영상 화자 이랑이

VWMA100 vs EMA200 의 차이:
- EMA200: 추세 필터 (장기), 단순 가중 지수평활
- VWMA100: 거래량 가중 (정보 흐름 반영), 중기 추세

본 전략 S3 는 EMA200 을 추세 필터로, RSI(14) 를 진입 타이밍으로 사용 — VWMA100 은 선택적 보조 필터로 추가 가능.

---

## 4. 전략 설계 (S3 가설)

### 4.1 진입 조건

```
추세 필터 : close[t] > EMA200_1d[t]       (상승 추세)
진입 트리거: RSI(14)[t] < 30              (과매도)
Long 진입  : t+1 open 에서 진입 (bar 종료 후)
```

### 4.2 청산 조건

```
Target     : RSI(14)[t] > 50              (중립 회복) 또는
             close[t] > EMA50_1d[t]       (단기 추세 회복)
Stop Loss  : close[t] < EMA200_1d[t]      (추세 붕괴)
             또는 entry_price - 2 * ATR(14)
```

**EMA50 > EMA100 → exit** 는 구현 단순화를 위해 EMA50 cross 로 대체 (verify before commit).

### 4.3 예상 Sharpe

| 항목 | 내용 |
|------|------|
| 예상 Sharpe | 0.4-0.8 (mean-reversion 단순 형태, 거래비용 반영) |
| 근거 | Avellaneda & Lee (2010) Sharpe ~1.44 를 보수적 하향 (크립토 변동성, 단순 임계값) |
| 표본 기간 | 2020-2024 (BTC 1d) |
| 자산 클래스 | 암호화폐 BTC/USDT |

---

## 5. 학술 기반 요약

| 개념 | 근거 | 출처 |
|------|------|------|
| 단기 mean reversion | negative serial corr | Cont (2001), Poterba & Summers (1988) |
| z-score 진입 유효성 | OU 모델 Sharpe ~1.44 | Avellaneda & Lee (2010) |
| RSI < 30 통계적 유의성 | 조건부 수익률 분포 변화 | Lo et al. (2000) |
| 추세 방향 필터 | EMA200 위에서만 진입 | 실무 consensus (peer-review 제한적) |

---

## 6. 한계 및 리스크

### 6.1 Trend Persistence vs Reversion

크립토는 추세 지속 (trend persistence) 이 강한 자산. RSI < 30 에서 추가 하락이 자주 발생. 특히 2018 bear market, 2022 LUNA/FTX 붕괴 구간에서 "추세 방향 내 pullback" 가정이 깨짐.

### 6.2 EMA200 의 느린 반응

EMA200 은 200일 평균이므로 추세 전환을 늦게 감지. 2022년 BTC 하락 초기에도 EMA200 아래로 크로스가 수 주 걸림 → 초기 손실 구간 진입 가능.

### 6.3 Parameter Overfitting

RSI(14), EMA200 은 실무 표준이지만 원래 주식 일간 데이터에 맞춰 조정된 값. 크립토 1d 에서 최적값이 다를 수 있음 → [[12-validation-protocol]] 의 DSR 보정 필수.

### 6.4 Sample Size

1일봉 BTC 5년 = ~1800 bars. 신호 발생 빈도가 낮아 통계 검정력 제한.

---

## 7. 백테스트 사전 등록 가설

- **자산**: BTCUSDT (Binance, 1d)
- **기간**: 5년 (2020-01 ~ 2024-12)
- **가설**: close > EMA200 AND RSI(14) < 30 진입 조건이 거래비용 포함 Sharpe ≥ 0.4 달성
- **검증**: PurgedKFold (n_splits=5) + DSR ≥ 0.95 + PBO ≤ 0.2
- **파라미터 탐색 범위**: RSI_threshold ∈ {25, 30, 35}, EMA_trend ∈ {100, 200} (6 조합, N=6)

---

## 관련 노트

- [[12-validation-protocol]] — PurgedKFold + DSR/PBO 검증 SOP
- [[35-meta-labeling-lopez-de-prado]] — 2차 메타라벨러로 false oversold 신호 필터링
- [[40-vwma-volume-weighted-ma]] — VWMA100 의 "자석 이론" 과 연결 (평균회귀 공통 메커니즘)
- [[44-time-series-momentum-crypto]] — S1: 반대 방향 (trend-following) 전략과 비교
- [[45-donchian-breakout-turtle]] — S2: 반대 방향 전략 (breakout)
- [[47-funding-rate-carry-perpetual]] — S4: carry 전략과 포트폴리오 구성
- [[48-pairs-trading-btc-eth]] — S5: market-neutral 과 포트폴리오 구성

---

## 출처

1. **Wilder, J.W.** (1978). *New Concepts in Technical Trading Systems*. Trend Research. ISBN 0-894-59013-1 (verify before commit).
   - pp.63-70: RSI (Relative Strength Index) 정의 및 계산법
   - pp.71-80: Overbought/oversold 임계값 근거

2. **Cont, R.** (2001). *Empirical properties of asset returns: stylized facts and statistical issues*. Quantitative Finance, 1(2), 223-236. https://doi.org/10.1080/713665670
   - Stylized Fact #5: 단기 음의 자기상관 (mean reversion 근거)

3. **Avellaneda, M. & Lee, J.H.** (2010). *Statistical arbitrage in the US equities market*. Quantitative Finance, 10(7), 761-782. https://doi.org/10.1080/14697680903124632
   - z-score 기반 mean reversion 진입 (Sharpe ~1.44, 거래비용 전, 2000-2005, 미국 주식)

4. **Lo, A.W., Mamaysky, H., Wang, J.** (2000). *Foundations of Technical Analysis: Computational Algorithms, Statistical Inference, and Empirical Implementation*. Journal of Finance, 55(4), 1705-1765. https://doi.org/10.1111/0022-1082.00265
   - 기술적 분석 패턴의 통계적 유의성, RSI 기반 신호의 조건부 수익률

5. **Poterba, J.M. & Summers, L.H.** (1988). *Mean Reversion in Stock Prices: Evidence and Implications*. Journal of Financial Economics, 22(1), 27-59. https://doi.org/10.1016/0304-405X(88)90021-9
   - 주식 가격의 장기 mean reversion 실증 (미국 주식 1871-1985)
