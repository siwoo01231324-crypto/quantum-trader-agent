---
type: research
id: 42-cross-sectional-momentum-crypto
name: "Cross-Sectional Momentum in Crypto — UBAI 기반 상대강도 필터"
sources:
  - "출처: https://youtu.be/j_0FRRgYYN8 (이랑이 인터뷰, 새로운 부자TV, 2026-01)"
  - "Jegadeesh, N. & Titman, S. (1993). Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency. Journal of Finance, 48(1), 65-91."
  - "Liu, Y., Tsyvinski, A., Wu, X. (2022). Common Risk Factors in Cryptocurrency. Journal of Finance, 77(2), 1133-1177."
  - "Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H. (2012). Time Series Momentum. Journal of Financial Economics, 104(2), 228-250."
  - "Carhart, M.M. (1997). On Persistence in Mutual Fund Performance. Journal of Finance, 52(1), 57-82."
  - "Asness, C.S., Moskowitz, T.J., Pedersen, L.H. (2013). Value and Momentum Everywhere. Journal of Finance, 68(3), 929-985."
---

# Cross-Sectional Momentum in Crypto — 상대강도 필터 (UBAI 기반)

> 본 노트는 이슈 #99 영상 인터뷰 ([[iranyi-vwma-2026-04-27]]) 의 "상대강도 필터" 와 "Time-of-Day / Day-of-Week 필터" (Variant E + D) 의 학술 근거를 정리한다. 영상 화자의 직관적 종목 선정 ("거래대금 높으면서 상대 강도 좋은 애들") 을 Cross-sectional Momentum 문헌으로 형식화한다.

## 1. Cross-sectional vs Time-series Momentum

| 차원 | Cross-sectional | Time-series |
|------|-----------------|-------------|
| 비교 대상 | 같은 시점, 다른 자산들 | 같은 자산, 다른 시점 |
| 시그널 | 자산 i 가 자산 universe 의 상위 N% 인가 | 자산 i 의 과거 수익률 > 0 인가 |
| 핵심 논문 | Jegadeesh & Titman (1993) | Moskowitz, Ooi, Pedersen (2012) |
| 응용 | Long-short ranking strategy | Trend-following |

본 이슈는 **Cross-sectional** 에 초점 (Variant E). Time-series momentum 은 [[40-vwma-volume-weighted-ma]] 의 VWMA cross 가 부분적으로 흡수.

## 2. Jegadeesh & Titman (1993) — 모멘텀의 기원

미국 주식 시장 (1965-1989) 에서:
- 과거 3-12개월 수익률 상위 decile 종목 (winners) 매수
- 과거 3-12개월 수익률 하위 decile 종목 (losers) 매도
- 향후 3-12개월 보유 → **연 12% 의 risk-adjusted alpha**

이는 Fama-French (1993) 의 SMB/HML factor 로 설명 안 되는 anomaly. Carhart (1997) 가 4-factor model 에 momentum (MOM) 을 추가하여 표준화.

## 3. Asness-Moskowitz-Pedersen (2013) — Value and Momentum Everywhere

**핵심**: Momentum factor 는 미국 주식뿐 아니라:
- 글로벌 주식 (UK, Japan, Europe)
- 채권
- 외환
- 원자재

**모든 자산군에서 동시에 alpha 를 생성**. 이는 momentum 이 데이터 마이닝 결과가 아닌 **robust empirical regularity** 임을 시사.

- Sharpe ratio: 자산군별 0.4-0.8
- 자산군 간 momentum 의 상관관계 양수 (공통 요인 시사)

## 4. Liu-Tsyvinski-Wu (2022) — 크립토 모멘텀

암호화폐 시장에 cross-sectional momentum 이 적용되는지 검증:
- **Universe**: Top 200+ coins by market cap (1700+ coins 분석)
- **기간**: 2014-2018 (5년)
- **결과**: 1주 / 2주 / 4주 lookback 의 winner-loser 전략이 **유의한 alpha 생성** (월 18% 단순 수익률, t-stat > 4)
- 결론: "Crypto market exhibits the same momentum factor as equity markets"

**Caveat**:
- 거래비용 후 수익률은 더 낮음 (slippage + spread + fee)
- 2018-2019 bear market 에서 momentum 효과 약화 (regime dependence)

## 5. UBAI (업비트 알트코인 인덱스) — 영상 화자의 벤치마크

영상 라인 545-563:
> "종목 고르는 기준이요. 저는 [UBAI] 라는 걸 많이 쓰거든요가. 업비트 알트코인 인덱스라고 이 업비트에 있는 알트코인들을 측정하는 거예요. 그래서 오늘은 -0.33% 보합장 있잖아요. 그런데도 이 보합 못 한 애들이 있잖아요... 이런 애들은 안 보는 거죠 시장보다 약하니까. 일단은 그 위에 있는 애들에 보는 거죠. 그 위에 있는 애들 중에서 거래대금으로 봐서... 거래대금 높으면서 상대 강도는 좋은 애들 위주로"

**자동화 정의** (본 이슈 결정사항):
- **UBAI = 업비트 KRW 페어 상위 20 알트코인 (BTC, ETH 제외) 시총 가중 일별 인덱스**
- **리밸런스: 매월 1일** (universe 변경)
- **데이터 소스**: 업비트 public REST API (`/v1/market/all` + `/v1/ticker`, rate limit 600/min, key 불요)
- **Fallback**: UBAI API 불가용 시 BTC dominance 역수 (CoinGecko `/global`)

**상대강도 (RS) 계산**:
- $\text{RS}_i^t(w) = \text{rolling\_mean}(\text{ret}_i, w) - \text{rolling\_mean}(\text{ret}_{\text{UBAI}}, w)$
- $w = 20$ (영업일 약 1개월)
- Variant E: RS 가 양수 + 거래대금 상위 quartile 인 자산만 진입 허용

**구현**: `src/features/cross_sectional_rs.py` — `relative_strength()`, `rs_quartile()`, `compute_ubai()` (Stage 3.5).

## 6. Time-of-Day / Day-of-Week Effect (영상 D variant 보충)

영상 라인 134-141, 564-601 의 시간대·요일 패턴:
- 오전 10:30~11:00 KST: 펌핑 종료 후 실망매물 (KRX 동시호가 후 변동성 패턴)
- 주말: 거래량·변동성 감소 ("세력 휴식")

**학술 연결**:
- **Berument & Kiymaz (2001)** — *The day of the week effect on stock market volatility*: 요일별 변동성 차이 입증 (월요일 변동성 高)
- **Harris (1986)** — *A transaction data study of weekly and intradaily patterns in stock returns*: 시간대·요일 returns 패턴 (U-shaped intraday vol)
- **Cont (2001) stylized fact #6**: U-shaped intraday volatility (open + close 시간 변동성 高)

크립토는 24h 시장이지만:
- 한국 거래소 (Upbit, Bithumb) 는 KST 사용자 비중 高 → KST 영업시간 특성 잔존
- BTC/ETH 는 글로벌 시장이지만 KST 한국 retail flow 가 일정 비중 차지 (Kim premium 현상)

본 이슈 Variant D 는 **KST 10:30~11:00 + 주말** 을 영상 충실 (faithful-to-source) 하게 적용. UTC 기반 또는 data-driven 시간대 최적화는 후속 이슈.

## 7. 한계 및 비판

### 7.1 Momentum Crash (모멘텀 붕괴)

Daniel & Moskowitz (2016) — *Momentum crashes*: 모멘텀 전략은 시장 회복 직후 (예: 2009-03) 큰 drawdown 경험. 변동성 스케일링 (Barroso & Santa-Clara 2015) 로 완화 가능하나, 본 이슈는 단기 (1m intraday) 라 일별 momentum crash 의 영향은 제한적.

### 7.2 Regime Dependence

Liu et al. (2022) 도 인정: bear market 에서 momentum 효과 감소. [[30-market-regime-detection]] 의 regime gating 과 결합 가능 (본 이슈 범위 밖).

### 7.3 Survivor Bias in Crypto

암호화폐는 거래소 상장 폐지·종목 소멸 빈번 → universe 의 시점별 정의 (point-in-time) 가 까다로움. 본 이슈는 매월 리밸런스 시 그 시점의 상위 20 만 사용 (look-ahead 방지).

### 7.4 거래비용

영상 화자는 단타 (1m intraday) → 거래비용·슬리피지가 net Sharpe 에 큰 영향. 본 이슈 bench (`scripts/bench_iranyi_variants.py`) 는 Binance taker fee 0.04% round-trip 을 기본 포함.

### 7.5 Wash Trading (Cong et al. 2023)

업비트는 KRW 페어이므로 wash trading 비중이 글로벌 거래소보다 낮으나 (시장 특성) 0 은 아님. UBAI 시총 가중에서 거래량이 아닌 시총 사용으로 부분 완화.

## 8. 본 프로젝트 활용 (Variant E + D)

- **`src/features/cross_sectional_rs.py`**:
  - `compute_ubai(start, end) -> pd.Series` — 일별 UBAI return
  - `relative_strength(asset_returns, benchmark_returns, window=20) -> pd.Series` — 자산 vs UBAI rolling RS
  - `rs_quartile(asset_returns_df, benchmark_returns, window=20) -> pd.DataFrame` — 시점별 quartile assign
- **`src/features/time_of_day.py::time_gate(...)`** — KST 10:30~11:00 + weekends 차단
- Variant E = A + RS quartile 1 (top)
- Variant D = A + time_gate

검증: PurgedKFold + DSR + PBO ([[12-validation-protocol]] §3.7).

## 관련 노트
- [[iranyi-vwma-2026-04-27]] — 영상 원문
- [[40-vwma-volume-weighted-ma]] — Variant A baseline
- [[41-multi-tf-fractal-trading]] — 멀티프레임 alignment
- [[12-validation-protocol]] — 검증 SOP
- [[13-feature-alpha-catalog]] — 한국 주식 momentum factor (12-1M, monthly horizon — 본 이슈와 차원 다름)
- [[30-market-regime-detection]] — regime 의존성 보완

## 출처

1. **이랑이 인터뷰** (2026-01, 새로운 부자TV) — https://youtu.be/j_0FRRgYYN8
2. **Jegadeesh, N. & Titman, S.** (1993). *Returns to Buying Winners and Selling Losers: Implications for Stock Market Efficiency*. Journal of Finance, 48(1), 65-91. https://doi.org/10.1111/j.1540-6261.1993.tb04702.x
3. **Liu, Y., Tsyvinski, A., Wu, X.** (2022). *Common Risk Factors in Cryptocurrency*. Journal of Finance, 77(2), 1133-1177. https://doi.org/10.1111/jofi.13119
4. **Moskowitz, T.J., Ooi, Y.H., Pedersen, L.H.** (2012). *Time Series Momentum*. Journal of Financial Economics, 104(2), 228-250. https://doi.org/10.1016/j.jfineco.2011.11.003
5. **Carhart, M.M.** (1997). *On Persistence in Mutual Fund Performance*. Journal of Finance, 52(1), 57-82. https://doi.org/10.1111/j.1540-6261.1997.tb03808.x
6. **Asness, C.S., Moskowitz, T.J., Pedersen, L.H.** (2013). *Value and Momentum Everywhere*. Journal of Finance, 68(3), 929-985. https://doi.org/10.1111/jofi.12021
7. **Daniel, K. & Moskowitz, T.J.** (2016). *Momentum crashes*. Journal of Financial Economics, 122(2), 221-247.
8. **Barroso, P. & Santa-Clara, P.** (2015). *Momentum has its moments*. Journal of Financial Economics, 116(1), 111-120.
9. **Berument, H. & Kiymaz, H.** (2001). *The day of the week effect on stock market volatility*. Journal of Economics and Finance, 25(2), 181-193.
10. **Harris, L.** (1986). *A transaction data study of weekly and intradaily patterns in stock returns*. Journal of Financial Economics, 16(1), 99-117.
11. **Cont, R.** (2001). *Empirical properties of asset returns: stylized facts and statistical issues*. Quantitative Finance, 1(2), 223-236.
12. **Cong, L.W., Li, X., Tang, K., Yang, Y.** (2023). *Crypto Wash Trading*. Management Science, 69(11), 6427-6454.
13. **Upbit Open API Documentation.** https://docs.upbit.com/ — `/v1/market/all`, `/v1/ticker`
