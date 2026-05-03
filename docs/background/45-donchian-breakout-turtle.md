---
type: research
id: 45-donchian-breakout-turtle
name: "Donchian Breakout / Turtle System — 4h 채널 돌파 전략"
created: 2026-04-27
tags: [breakout, donchian, turtle, trend-following, atr, 4h, swing]
sources:
  - "Wilder, J.W. (1978). New Concepts in Technical Trading Systems. Trend Research. ISBN 0-9monetario-find (verify before commit — ISBN: 0-894-59013-1, pp.21-36: ATR 정의)"
  - "Faith, C. (2007). Way of the Turtle: The Secret Methods that Turned Ordinary People into Legendary Traders. McGraw-Hill. ISBN 0-07-148664-2"
  - "Brock, W., Lakonishok, J., LeBaron, B. (1992). Simple Technical Trading Rules and the Stochastic Properties of Stock Returns. Journal of Finance, 47(5), 1731-1764. https://doi.org/10.1111/j.1540-6261.1992.tb04681.x"
  - "Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H. (2012). Time Series Momentum. Journal of Financial Economics, 104(2), 228-250. https://doi.org/10.1016/j.jfineco.2011.11.003"
  - "Daniel, K. & Moskowitz, T.J. (2016). Momentum crashes. Journal of Financial Economics, 122(2), 221-247. https://doi.org/10.1016/j.jfineco.2015.12.002"
tested_in: 02_implementation
backtest_period: '2020-01-01 to 2025-12-31'
backtest_asset: BTCUSDT
backtest_timeframe: 4h
variant_id: 'S2/S2a/S2b/S2c/W1'
realized_sharpe: 0.814
realized_mdd: -0.187
realized_mhr: 0.51
n_trades: 602
gate_status: 'passed-single-mdd-mhr'
best_variant: 'S2c (Donchian+vol-target)'
---

# Donchian Breakout / Turtle System — 4h 채널 돌파 전략

> 본 노트는 후속 backtest (BTC@4h, 5년 SOP) 의 사전 등록 가설 S2 이다.
> Donchian 채널 돌파 + ATR stop 의 학술적·실증적 근거를 정리한다.
> 검증은 [[12-validation-protocol]] SOP 를 따른다.

---

## 1. Donchian 채널 — 기원과 정의

**Richard Donchian** (1905-1993) 은 1960년대 처음 채널 breakout 시스템을 소개했다.
핵심 개념: n-period 최고가를 돌파하면 uptrend 신호, n-period 최저가를 이탈하면 downtrend 신호.

$$
\text{Upper}_t(n) = \max(H_{t-n+1}, \ldots, H_t)
$$
$$
\text{Lower}_t(n) = \min(L_{t-n+1}, \ldots, L_t)
$$

- $H_i$: i 시점 고가, $L_i$: i 시점 저가, $n$: lookback window

**Long 신호**: $\text{close}_t > \text{Upper}_{t-1}(n)$ (전 bar 고점 돌파)
**Exit 신호**: $\text{close}_t < \text{Lower}_{t-1}(m)$, $m < n$ (더 빠른 하단 채널)

---

## 2. Turtle Trading System (1983-1988)

### 2.1 배경

Richard Dennis 와 William Eckhardt 는 1983년 "트레이더는 만들어지는가 타고나는가" 논쟁을 해결하기 위해 13명의 비전문가 (Turtle Traders) 를 모집해 Donchian 기반 시스템을 훈련시켰다.

Curtis Faith (2007) *Way of the Turtle* (ISBN 0-07-148664-2) 에 따르면:
- **1983-1988 기간**: Turtle 그룹 전체 평균 CAGR ~80% (자기보고)
- 개인별 편차 매우 큼 — 일부는 200%+ , 일부는 시스템 이탈 후 손실
- **주의**: self-reported, N=13, no peer review, 1980s 시장 환경 (trend-friendly), 현재 재현 불가능성 높음

### 2.2 Turtle System 1 & 2

| 항목 | System 1 | System 2 |
|------|----------|----------|
| Entry lookback | 20-period high | 55-period high |
| Exit lookback | 10-period low | 20-period low |
| Stop | 2×ATR | 2×ATR |
| 본 노트 채택 | **채택 (S2 기반)** | 참고 |

---

## 3. ATR (Average True Range)

### 3.1 Wilder (1978) 정의

Wilder, J.W. (1978) *New Concepts in Technical Trading Systems* (Trend Research, ISBN 0-894-59013-1, pp.21-36):

$$
TR_t = \max(H_t - L_t,\ |H_t - C_{t-1}|,\ |L_t - C_{t-1}|)
$$
$$
\text{ATR}_t(n) = \frac{1}{n} \sum_{i=t-n+1}^{t} TR_i
$$

(Wilder 원본은 smoothed MA 사용; 본 구현은 단순 rolling mean 사용 — verify before commit)

ATR 의 역할:
1. **변동성 표준화**: 절대 가격 대신 변동성 단위로 stop 설정 → 자산·시간대 간 비교 가능
2. **Position sizing**: 단위 ATR 당 고정 리스크 → 변동성 스케일 포지션

### 3.2 본 전략 Stop 설정

$$
\text{stop\_long} = \text{entry\_price} - 2 \times \text{ATR}_{t}(14)
$$

2×ATR stop 은 Turtle 원본 (N unit = 1% equity / ATR) 과 일치. ATR window=14 는 Wilder 원본.

---

## 4. 학술 실증 — Brock-Lakonishok-LeBaron (1992)

Brock, Lakonishok, LeBaron (1992, JF 47(5) 1731-1764) 는 Dow Jones Industrial Average (1897-1986) 에서 기술적 분석 규칙을 엄밀하게 검증:

- 테스트 대상: **Moving Average Crossover + Trading Range Breakout (= Donchian 유사)**
- 결과: 매수 신호 이후 평균 일간 수익률 = +0.042%, 매도 이후 = -0.025%
  - 차이 = 0.067%/day → 연율 약 17%, **t-stat > 2**
- 랜덤워크·GARCH·EGARCH 모델로 설명 안 됨

**한계**: 1897-1986 미국 주식 시장. 거래비용 미포함. 크립토 4h 에 직접 적용 불가 — 방향성 근거로만 인용.

---

## 5. Trend-Following의 이론적 기반

Moskowitz et al. (2012) TSMOM (→ [[44-time-series-momentum-crypto]]) 은 Donchian breakout 과 동일한 under-reaction 메커니즘에 기반한다:

- 채널 돌파 = "과거 n-period 최고가 돌파" = n-period TSMOM 의 proxy
- 차이: TSMOM 은 수익률 부호 사용, Donchian 은 가격 레벨 사용

---

## 6. 4h 봉 적용 설계 (S2 가설)

### 6.1 진입 조건

```
entry_lookback = 20 bars (4h × 20 = 80시간 ≈ 3.3일)
exit_lookback  = 10 bars (4h × 10 = 40시간 ≈ 1.7일)

Long 진입: close[t] > rolling_max(high, 20)[t-1]
Long 청산: close[t] < rolling_min(low, 10)[t-1]
  OR close[t] < entry_price - 2 * ATR(14)[t]
```

### 6.2 포지션 사이징

Turtle 원본:
$$
\text{units} = \frac{\text{account} \times 0.01}{\text{ATR} \times \text{point\_value}}
$$

본 백테스트에서는 단순화하여 고정 1 unit (1 BTC) 또는 volatility-adjusted 사이징.

---

## 7. 예상 Sharpe 및 성과 추정

| 항목 | 내용 |
|------|------|
| 예상 Sharpe | 0.5-1.0 (현대 시장 + 거래비용 반영) |
| 표본 기간 | Turtle 1983-1988 (자기보고), BLL 1992 1897-1986 |
| 자산 클래스 | 선물·주식 (크립토 직접 실증 부족) |
| MDD 주의 | Trend-following 은 MDD 50%+ 일상 — 자본 버퍼 필수 |

**Turtle 80% CAGR 은 현재 시장에 재현 불가** — self-reported, 1980s 저효율 시장, peer-review 없음. 학술 Sharpe 는 BLL (1992) 의 방향성 실증과 Moskowitz et al. (2012) 의 TSMOM Sharpe ~1.28 (pre-cost) 을 상한 참조.

---

## 8. 한계 및 리스크

### 8.1 High MDD (Drawdown)

Trend-following 은 횡보장에서 연속 손절 발생. MDD 50% 이상이 일상적. 크립토 2018 bear market 전체가 drawdown 구간.

### 8.2 Momentum Crashes (Daniel & Moskowitz 2016)

급반전 시기 (V-bottom) 에서 신호가 반전되기 전 큰 손실. BTC 2020-03-12 "Black Thursday" (하루 -40%) 가 대표 사례.

### 8.3 Crowding Effect

동일 시스템 사용자가 많아질수록 동시 청산 → 슬리피지 증가. 2000년대 이후 CTA / quant 펀드의 Donchian 활용이 보편화되며 edge 축소 우려.

### 8.4 크립토 특수성

24/7 시장 → weekend gap 없음 (유리). 그러나 거래소 별 funding rate 이 포지션 보유비용에 영향.

---

## 9. 백테스트 사전 등록 가설

- **자산**: BTCUSDT (Binance, 4h)
- **기간**: 5년 (2020-01 ~ 2024-12)
- **가설**: entry_lookback=20, exit_lookback=10, stop=2×ATR(14) 조합이 거래비용 포함 Sharpe ≥ 0.5 달성
- **검증**: PurgedKFold (n_splits=5) + DSR ≥ 0.95 + PBO ≤ 0.2
- **파라미터 탐색 범위**: entry_lookback ∈ {10, 20, 55}, exit_lookback ∈ {5, 10, 20} (9 조합, N=9)

---

## 관련 노트

- [[12-validation-protocol]] — PurgedKFold + DSR/PBO 검증 SOP
- [[44-time-series-momentum-crypto]] — S1: TSMOM, 동일 trend-following 계열
- [[35-meta-labeling-lopez-de-prado]] — 메타라벨러로 false breakout 필터링 가능
- [[40-vwma-volume-weighted-ma]] — VWMA 를 breakout filter 로 결합 가능
- [[46-ema-pullback-mean-reversion]] — S3: 역방향 (mean reversion) 전략과 비교
- [[47-funding-rate-carry-perpetual]] — S4: 보유비용 고려
- [[48-pairs-trading-btc-eth]] — S5: market-neutral 과 포트폴리오 구성

---

## 출처

1. **Wilder, J.W.** (1978). *New Concepts in Technical Trading Systems*. Trend Research. ISBN 0-894-59013-1 (verify before commit).
   - pp.21-36: ATR (Average True Range) 정의 및 계산법
   - pp.63-70: Parabolic SAR (참고용)

2. **Faith, C.** (2007). *Way of the Turtle: The Secret Methods that Turned Ordinary People into Legendary Traders*. McGraw-Hill. ISBN 0-07-148664-2.
   - Ch.4-6: Turtle System 1/2 파라미터 (entry/exit lookback, ATR stop)
   - Ch.10: 1983-1988 Turtle 결과 (self-reported, N=13, no peer review, 1980s 환경)

3. **Brock, W., Lakonishok, J., LeBaron, B.** (1992). *Simple Technical Trading Rules and the Stochastic Properties of Stock Returns*. Journal of Finance, 47(5), 1731-1764. https://doi.org/10.1111/j.1540-6261.1992.tb04681.x
   - Table 1-4: Moving average crossover + breakout 규칙의 통계적 유의성 (DJIA 1897-1986)

4. **Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H.** (2012). *Time Series Momentum*. Journal of Financial Economics, 104(2), 228-250. https://doi.org/10.1016/j.jfineco.2011.11.003
   - Trend-following 의 이론적 기반 (TSMOM)

5. **Daniel, K. & Moskowitz, T.J.** (2016). *Momentum crashes*. Journal of Financial Economics, 122(2), 221-247. https://doi.org/10.1016/j.jfineco.2015.12.002
   - Trend-following 전략의 crash 리스크
