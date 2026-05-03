---
type: research
id: 44-time-series-momentum-crypto
name: "Time-Series Momentum (TSMOM) — 크립토 4h 스윙 적용"
created: 2026-04-27
tags: [momentum, time-series, crypto, btc, 4h, swing]
sources:
  - "Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H. (2012). Time Series Momentum. Journal of Financial Economics, 104(2), 228-250. https://doi.org/10.1016/j.jfineco.2011.11.003"
  - "Liu, Y., Tsyvinski, A., Wu, X. (2022). Common Risk Factors in Cryptocurrency. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119"
  - "Daniel, K. & Moskowitz, T.J. (2016). Momentum crashes. Journal of Financial Economics, 122(2), 221-247. https://doi.org/10.1016/j.jfineco.2015.12.002"
  - "Barroso, P. & Santa-Clara, P. (2015). Momentum has its moments. Journal of Financial Economics, 116(1), 111-120. https://doi.org/10.1016/j.jfineco.2014.11.010"
  - "Jegadeesh, N. & Titman, S. (1993). Returns to Buying Winners and Selling Losers. Journal of Finance, 48(1), 65-91. https://doi.org/10.1111/j.1540-6261.1993.tb04702.x"
tested_in: 02_implementation
backtest_period: '2020-01-01 to 2025-12-31'
backtest_asset: BTCUSDT
backtest_timeframe: 4h
variant_id: 'S1'
realized_sharpe: -0.466
realized_mdd: -0.929
realized_mhr: 0.39
n_trades: 2703
gate_status: 'failed'
---

# Time-Series Momentum (TSMOM) — 크립토 4h 스윙 적용

> 본 노트는 후속 backtest (BTC@4h, 5년 SOP) 의 사전 등록 가설 S1 이다.
> 학술 근거를 먼저 확정하고 파라미터·검증 계획을 작성한다.
> 검증은 [[12-validation-protocol]] SOP 를 따른다.

---

## 1. 핵심 개념 — Time-series Momentum

**Time-series momentum (TSMOM)** 은 동일 자산의 과거 수익률 부호가 미래 단기 수익률 방향을 예측한다는 현상이다. Cross-sectional momentum (Jegadeesh & Titman 1993) 이 "다른 자산들 대비 우열" 을 보는 것과 달리, TSMOM 은 **자산 자체의 추세** 만 이용한다.

### 1.1 Moskowitz-Ooi-Pedersen (2012) — 정의

Moskowitz, Ooi, Pedersen (2012, JFE 104(2) 228-250) 는 58 개 선물 자산 (주식, 채권, 통화, 원자재) 에서 1985-2009 기간을 분석하여 TSMOM 을 공식화했다:

$$
r_{t \to t+1}^{\text{TSMOM}} = \text{sign}(r_{t-12m \to t}) \times \sigma_t^{-1}
$$

- 12-month lookback return 의 부호에 따라 long/short 포지션 취함
- 변동성 $\sigma_t$ 로 position sizing (risk-parity)
- **58 개 자산 모두에서 유의한 양의 수익 (월 Sharpe ~0.7-1.2)**
- 전략 Sharpe (full sample, equal-weighted): ~1.28 (논문 Table 2, 표본 1985-2009)

핵심 메커니즘 가설:
1. **Under-reaction** — 뉴스에 초기 반응이 느린 투자자들이 가격 지속성을 만든다
2. **Trend-chasing** — 후기 진입자들이 추세를 연장한다
3. **Risk premium** — 추세 추종은 나쁜 시장 상태에서 long position 을 hedge 함

### 1.2 Liu-Tsyvinski-Wu (2022) — 크립토 적용

Liu, Tsyvinski, Wu (2022, JF 77(2) 1133-1177) 는 암호화폐 시장에서 Fama-French 형 risk factor 를 검증:

- **Universe**: 상위 시총 코인 (2014-2018, 주별 rebalancing)
- **1-week momentum**: 과거 1주 수익률 상위 10% → 하위 10% long-short
  - 월 excess return ~18% (거래비용 전), t-stat > 4
- **4-week momentum**: 유사하게 유의미한 alpha 존재
- 결론: "Cryptocurrency markets exhibit the same momentum factor structure as equity markets"

**Sharpe 추정** (1주 momentum portfolio, 논문 Table 4, 2014-2018, daily aggregation):
~1.5 (표본 내). 4h 타임프레임, 거래비용 반영 시 **보수적 추정 0.7-1.2** 가 합리적.

---

## 2. 4h 봉 적용 설계 (S1 가설)

### 2.1 시그널 정의

```
lookback = 6 bars (4h × 6 = 24시간)
signal = sign(close[t] - close[t - lookback])
  → signal > 0: long
  → signal < 0: flat (long-only 운용 시 short 생략)
```

**합리화**:
- Moskowitz et al. (2012) 의 12-month 는 주식/선물 대상
- 크립토는 변동성이 10-15x 높아 lookback 을 축소 (단위 정보량 보존)
- 24h (= 6 × 4h bars) 는 Liu et al. (2022) 의 "1-week" 과 비슷한 정보 반감기

### 2.2 포지션 사이징

논문 원본 (Moskowitz et al. 2012) 의 inverse-volatility 스케일링:

$$
w_t = \frac{c}{\sigma_t^{\text{ex ante}}}
$$

- $\sigma_t^{\text{ex ante}}$: 과거 20-bar 일간 수익률의 표준편차 (exponentially weighted)
- $c$: target annualized volatility (예: 40% for BTC)

### 2.3 청산 조건

- 반대 신호 발생 (signal flip)
- 또는 ATR-기반 stop (Wilder 1978 ATR, 2×ATR)

---

## 3. 학술적 근거 요약

| 항목 | 내용 | 출처 |
|------|------|------|
| TSMOM 존재 | 58 자산, 1985-2009, 월 Sharpe ~1.28 | Moskowitz et al. (2012, JFE 104(2)) |
| 크립토 모멘텀 | Top coins 2014-2018, 1주 t-stat > 4 | Liu et al. (2022, JF 77(2)) |
| 추정 Sharpe | 4h BTC, 거래비용 반영 후 | 보수적 추정: 0.7-1.2 |
| 표본 기간 | 2014-2018 (크립토), 1985-2009 (주식) | 논문 각각 |
| 자산 클래스 | 암호화폐 (BTC/ETH), 주식·선물 | 논문 각각 |

---

## 4. 한계 및 리스크

### 4.1 Momentum Crashes (Daniel & Moskowitz 2016)

Daniel & Moskowitz (2016, JFE 122(2) 221-247) — 강세장 이후 급반전 시기에 momentum 전략은 extreme drawdown 경험 (예: 2009년 3월 반등 직후 momentum long-short 전략 -50%). 크립토 2018 bear market, 2020 COVID crash 시기에도 유사 패턴 우려.

**완화**: Barroso & Santa-Clara (2015) 의 volatility scaling 적용 → 상기 포지션 사이징 수식에 내장.

### 4.2 Regime Dependence

Liu et al. (2022) 도 인정: bear market 에서 alpha 감소. [[30-market-regime-detection]] 의 regime gating 과 결합 가능.

### 4.3 거래비용

4h 봉 스윙은 turnover 가 낮아 거래비용 영향이 상대적으로 작음. Binance taker 0.04% + slippage 1bps 가정.

### 4.4 크립토 특수성

- Wash trading (Cong et al. 2023) → volume 기반 filter 사용 시 주의
- Exchange-specific basis risk
- 24/7 시장 → 주말 유동성 감소 구간

---

## 5. 백테스트 사전 등록 가설

> 본 섹션은 [[12-validation-protocol]] §3 의 "사전 등록" 요건을 충족한다.
> 가설을 backtest 실행 전에 기록하여 data snooping 리스크를 통제한다.

- **자산**: BTCUSDT (Binance, 4h)
- **기간**: 5년 (2020-01 ~ 2024-12)
- **가설**: lookback=6 bar TSMOM signal 은 거래비용 포함 Sharpe ≥ 0.5 를 달성
- **검증**: PurgedKFold (n_splits=5, embargo=1%) + DSR ≥ 0.95 + PBO ≤ 0.2
- **비교 baseline**: Buy-and-hold BTC
- **파라미터 탐색 범위**: lookback ∈ {3, 6, 12, 24} bars (4개 시행 → N=4 으로 DSR 보정)

---

## 관련 노트

- [[12-validation-protocol]] — PurgedKFold + DSR/PBO 검증 SOP
- [[35-meta-labeling-lopez-de-prado]] — 2차 메타라벨러로 false positive 제거 가능
- [[40-vwma-volume-weighted-ma]] — VWMA cross 와 TSMOM 의 상호보완 (trend filter)
- [[42-cross-sectional-momentum-crypto]] — Cross-sectional momentum 자매 노트
- [[30-market-regime-detection]] — Regime 의존성 보완
- [[45-donchian-breakout-turtle]] — S2: Donchian breakout, trend-following 계열 동일
- [[46-ema-pullback-mean-reversion]] — S3: 역방향 (mean reversion) 전략과 비교
- [[47-funding-rate-carry-perpetual]] — S4: carry 전략과 상관 분석
- [[48-pairs-trading-btc-eth]] — S5: market-neutral 전략과 포트폴리오 구성

---

## 출처

1. **Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H.** (2012). *Time Series Momentum*. Journal of Financial Economics, 104(2), 228-250. https://doi.org/10.1016/j.jfineco.2011.11.003
   - Table 2: 전략 Sharpe 1.28 (1985-2009, 58 자산)
   - §3.1: TSMOM 정의, 12-month lookback

2. **Liu, Y., Tsyvinski, A., Wu, X.** (2022). *Common Risk Factors in Cryptocurrency*. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119
   - Table 4: 1-week momentum t-stat > 4, 2014-2018
   - §3: 크립토 factor 검증 방법론

3. **Daniel, K. & Moskowitz, T.J.** (2016). *Momentum crashes*. Journal of Financial Economics, 122(2), 221-247. https://doi.org/10.1016/j.jfineco.2015.12.002
   - Momentum crash 메커니즘과 완화 방법

4. **Barroso, P. & Santa-Clara, P.** (2015). *Momentum has its moments*. Journal of Financial Economics, 116(1), 111-120. https://doi.org/10.1016/j.jfineco.2014.11.010
   - Volatility scaling 으로 crash 완화

5. **Jegadeesh, N. & Titman, S.** (1993). *Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency*. Journal of Finance, 48(1), 65-91. https://doi.org/10.1111/j.1540-6261.1993.tb04702.x
   - Cross-sectional momentum 의 기원 (비교 참조용)
