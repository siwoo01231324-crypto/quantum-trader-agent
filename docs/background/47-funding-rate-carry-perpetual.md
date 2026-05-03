---
type: research
id: 47-funding-rate-carry-perpetual
name: "Funding Rate Carry — BTC 무기한 선물 캐리 전략"
created: 2026-04-27
tags: [funding-rate, carry, perpetual, delta-neutral, crypto, btc]
sources:
  - "Avellaneda, M. & Stoikov, S. (2008). High-frequency trading in a limit order book. Quantitative Finance, 8(3), 217-224. https://doi.org/10.1080/14697680701381228 (verify before commit — delta-neutral carry 맥락)"
  - "Liu, Y., Tsyvinski, A., Wu, X. (2022). Common Risk Factors in Cryptocurrency. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119"
  - "BIS Working Paper No.1013 (2022). Crypto carry. Bank for International Settlements. https://www.bis.org/publ/work1013.htm"
  - "Cong, L.W., Li, X., Tang, K., Yang, Y. (2023). Crypto Wash Trading. Management Science, 69(11), 6427-6454. https://doi.org/10.1287/mnsc.2023.4538"
  - "Binance Futures Funding Rate API: https://fapi.binance.com/fapi/v1/fundingRate (자칭/single-case 표기 없음 — 공식 API)"
tested_in: 02_implementation
backtest_period: '2020-01-01 to 2025-12-31'
backtest_asset: BTCUSDT
backtest_timeframe: 4h
variant_id: 'S4/S4a/W2'
realized_sharpe: 0.961
realized_mdd: -0.171
realized_mhr: 0.29
n_trades: 300
gate_status: 'winner-but-low-mhr'
best_variant: 'S4 (long-only when funding < -0.005%)'
---

# Funding Rate Carry — BTC 무기한 선물 캐리 전략

> 본 노트는 후속 backtest (BTC perpetual@8h funding, 5년 SOP) 의 사전 등록 가설 S4 이다.
> 암호화폐 무기한 선물 (perpetual) 의 funding rate 캐리 전략의 학술적 근거를 정리한다.
> 검증은 [[12-validation-protocol]] SOP 를 따른다.

---

## 1. 무기한 선물 (Perpetual Futures) 메커니즘

### 1.1 Funding Rate 정의

**무기한 선물**은 만기가 없는 선물 계약으로 BitMEX 가 2016년 비트코인 무기한 선물을 최초 도입했다 (Bybit, Binance, OKX 등이 이후 채택).

**Funding rate** 는 현물 가격과 선물 가격의 괴리를 해소하기 위해 롱·숏 보유자 간에 8시간마다 정산되는 비용이다:

$$
\text{Funding Payment} = \text{Position Size} \times \text{Funding Rate}
$$

- **Funding Rate > 0** (양수): 롱 포지션 보유자가 숏 보유자에게 지급 → 선물 가격 > 현물 가격 (contango)
- **Funding Rate < 0** (음수): 숏 보유자가 롱 보유자에게 지급 → 선물 가격 < 현물 가격 (backwardation)

Binance 기본 funding rate 계산식:
$$
\text{FR} = \text{Premium Index} + \text{clamp}(\text{Interest Rate} - \text{Premium Index},\ -0.05\%,\ +0.05\%)
$$

여기서 Interest Rate = 0.01%/8h (고정), Premium Index = (선물 - 현물) / 현물.

### 1.2 경제적 해석

Funding rate 는 암호화폐 시장의 **레버리지 수요 지표**다:
- 불장 (bull market) → 대부분 투기자가 롱 → funding 양수 지속 → 롱 보유 비용 발생
- 역추세 상황 → funding 음수 → 숏 보유 비용 발생

따라서 funding rate 의 지속적 양수/음수 편향은 **캐리 (carry)** 기회를 창출한다.

---

## 2. 학술 근거

### 2.1 BIS Working Paper No.1013 (2022)

Bank for International Settlements (2022, WP#1013) *Crypto carry*:
- 주요 내용: 암호화폐 무기한 선물의 funding rate 는 예측 가능한 패턴이 있으며, 특히 양수 funding 이 지속되는 기간에 숏 선물 + 롱 현물 delta-neutral carry 전략이 유의한 excess return 생성
- 단, "carry trade" 수익의 상당 부분은 crash risk premium (극단적 시장 하락 시 손실) 에 대한 보상
- **주의**: BIS WP 는 peer-reviewed journal 이 아님 — 방향성 근거로 인용 (verify before commit: 정확한 표본 기간·Sharpe 수치는 원문 확인 필요)

### 2.2 Liu-Tsyvinski-Wu (2022) — 크립토 Risk Premia

Liu, Tsyvinski, Wu (2022, JF 77(2) 1133-1177) 는 크립토 시장에서 carry factor 를 포함한 risk premia 를 검증:
- Carry factor (high funding − low funding portfolios) 가 cross-sectional returns 를 설명
- Momentum + Carry 조합이 단일 factor 보다 Sharpe 개선

### 2.3 전통 금융의 Carry Trade

Koijen et al. (2018, JF 73(2) — verify before commit: DOI 필요) — "Carry" across asset classes:
- 채권·외환·주식·원자재에서 carry 전략이 일관된 양의 수익
- 공통 메커니즘: **risk compensation for crash exposure** (bad-state loading)

크립토 funding carry 는 전통 외환 carry trade 와 유사한 구조 — 양의 funding 을 수취하는 대가로 극단적 하락 위험 부담.

---

## 3. 전략 설계 (S4 가설)

### 3.1 Delta-Neutral Carry

**목표**: 가격 방향 리스크를 제거하고 funding rate 만 수취.

```
조건:
  funding_rate_8h < threshold_neg  (예: -0.005%)
  → Long perpetual + Short spot (delta neutral)
  → 수취: |funding_rate| per 8h

  funding_rate_8h > threshold_pos  (예: +0.005%)
  → Short perpetual + Long spot (delta neutral)
  → 수취: funding_rate per 8h

  |funding_rate_8h| < min_threshold (예: 0.002%)
  → No position (carry insufficient to cover costs)
```

**데이터 소스**: `https://fapi.binance.com/fapi/v1/fundingRate`
- 매 8시간 업데이트 (00:00, 08:00, 16:00 UTC)
- 무료, API key 불필요 (공개 endpoint)

### 3.2 단순화 (백테스트 목적)

완전 delta-neutral (현물 헤지) 구현은 복잡하므로, 본 백테스트에서는:
- **Long perpetual only** 조건: funding_rate < 0 (숏 포지션 보유자로부터 받는 구조)
- **Flat** 조건: funding_rate ≥ 0

이는 "funding 수취 방향으로만 롱" 하는 semi-carry 전략. 완전 중립 대비 방향 리스크 잔존.

### 3.3 예상 Sharpe

| 항목 | 내용 |
|------|------|
| 예상 Sharpe | 1.0-1.5 (시장 중립 carry 기준) |
| 근거 | BIS WP#1013 방향성 근거; Liu et al. (2022) carry factor 유효성 |
| 표본 기간 | BIS WP: 2016-2022 (verify) |
| 자산 클래스 | BTC/USDT 무기한 선물 (Binance) |
| 주의 | 단순화된 구현 (semi-carry) 시 Sharpe 하락 가능 |

---

## 4. 데이터 특성

### 4.1 Funding Rate 역사적 패턴

Binance BTC/USDT 무기한 선물 (2020-2024) 관측 특성 (공식 API 기준, peer-reviewed 실증 없음 — 직접 분석 필요):
- 강세장 (2020-2021): funding 양수 지속 → 롱 보유 비용 高
- 횡보·약세 구간: funding 음수 빈도 증가 → S4 전략 active 구간
- Funding 의 극단값 (±0.1%+) 은 포지션 청산 리스크 신호

### 4.2 데이터 접근

```python
# Binance Futures Funding Rate API
import requests
resp = requests.get(
    "https://fapi.binance.com/fapi/v1/fundingRate",
    params={"symbol": "BTCUSDT", "limit": 1000}
)
# returns: [{"symbol", "fundingRate", "fundingTime"}, ...]
```

5년 히스토리는 Binance Vision (`https://data.binance.vision`) 에서 일괄 다운로드 가능.

---

## 5. 한계 및 리스크

### 5.1 Extreme Funding → Forced Liquidation

극단적 funding rate (±0.5%+ per 8h) 구간에서 반대 포지션 보유자의 청산이 연쇄 발생 → 급격한 가격 움직임 → 헤지 불완전 시 대규모 손실.

2021-05 BTC 급락 시 funding rate 가 극단 양수 → 급속 반전 → 롱 포지션 대량 청산 cascading.

### 5.2 Exchange Counterparty Risk

Binance 등 CEX 에 자산 예치 필수 → FTX 붕괴 (2022-11) 와 같은 exchange risk. 분산 보관 불가.

### 5.3 Cross-Exchange Basis Risk

현물-선물 헤지를 다른 거래소에서 실행 시 basis divergence 가능. 동일 거래소 내 헤지가 이상적이나 margin 요구량이 2배.

### 5.4 Carry Crash Risk

전통 carry trade 와 동일하게, funding carry 는 **나쁜 시장 상태 (bad state)** 에서 손실 집중. "수익은 평소에 조금씩, 손실은 crash 시 한 번에" 의 asymmetric 패턴 — 코인 급락 시 funding 방향성도 동시에 역전.

### 5.5 Single-Exchange Data Bias

본 전략은 Binance 데이터만 사용. Binance funding 은 자체 공식으로 계산되며 다른 거래소 (Bybit, OKX) 와 약간 다름. 타 거래소 arbitrage 는 별도 전략.

---

## 6. 백테스트 사전 등록 가설

- **자산**: BTCUSDT Binance 무기한 선물 (funding rate 기반)
- **기간**: 5년 (2020-01 ~ 2024-12)
- **가설 (단순화 semi-carry)**: funding_rate < 0 시 long perpetual, 그 외 flat → Sharpe ≥ 0.5
- **가설 (full carry 목표)**: delta-neutral 구현 시 Sharpe ≥ 1.0
- **검증**: PurgedKFold (n_splits=5) + DSR ≥ 0.95 + PBO ≤ 0.2
- **파라미터 탐색 범위**: threshold_neg ∈ {-0.002%, -0.005%, -0.01%} (3 시행, N=3)

---

## 관련 노트

- [[12-validation-protocol]] — PurgedKFold + DSR/PBO 검증 SOP
- [[35-meta-labeling-lopez-de-prado]] — 2차 메타라벨러로 crash 리스크 구간 필터링
- [[44-time-series-momentum-crypto]] — S1: 방향성 전략 (funding carry 와 상관 분석)
- [[45-donchian-breakout-turtle]] — S2: trend-following (funding 방향과 추세의 상관)
- [[46-ema-pullback-mean-reversion]] — S3: mean reversion 전략과 포트폴리오 구성
- [[48-pairs-trading-btc-eth]] — S5: market-neutral 포트폴리오 구성 비교
- [[40-vwma-volume-weighted-ma]] — VWMA 와 funding signal 결합 가능성

---

## 출처

1. **BIS Working Paper No.1013** (2022). *Crypto carry*. Bank for International Settlements. https://www.bis.org/publ/work1013.htm
   - 무기한 선물 funding rate carry 의 실증 분석 (verify before commit: 정확한 표본 기간·수치)
   - 주의: BIS WP 는 peer-reviewed journal 이 아님

2. **Liu, Y., Tsyvinski, A., Wu, X.** (2022). *Common Risk Factors in Cryptocurrency*. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119
   - Carry factor 포함 크립토 risk premia 검증

3. **Avellaneda, M. & Stoikov, S.** (2008). *High-frequency trading in a limit order book*. Quantitative Finance, 8(3), 217-224. https://doi.org/10.1080/14697680701381228
   - Delta-neutral 포지션 관리의 이론적 기반 (verify before commit — funding carry 와 직접 연관성 확인 필요)

4. **Cong, L.W., Li, X., Tang, K., Yang, Y.** (2023). *Crypto Wash Trading*. Management Science, 69(11), 6427-6454. https://doi.org/10.1287/mnsc.2023.4538
   - Binance 거래량·가격 데이터 품질 맥락

5. **Binance Futures API Documentation.** https://fapi.binance.com/fapi/v1/fundingRate
   - Funding rate 데이터 소스 (공식 API, 무료)
   - 관련: https://data.binance.vision (히스토리 bulk download)
