---
type: strategy
id: cs-tsmom-crypto-daily
name: Binance Crypto Cross-Sectional TSMOM 12-1 Daily
status: backtest
paradigm: universe-scan
instruments:
- binance-usdt-spot-top30
market: crypto
timeframe: 1d
uses_signals:
- tsmom-12-1
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-06
sharpe_bt: 1.328
sharpe_live: null
mdd_bt: -0.5242
annual_return_bt: 0.9085
backtest_period: "2020-01-01/2025-12-31"
last_updated: 2026-05-06
summary_ko: |
  Binance USDT spot 24h 거래량 상위 30종목 중 (스테이블·합성·레버리지 제외)
  12-1 month time-series momentum 점수 (log(close[t-21]/close[t-252])) 상위 10
  종목을 동일 비중으로 매주 리밸런싱. BTC 252d drawdown ≤ -30% 시 전량 청산.
tags:
- pattern:universe-scan
- momentum
- crypto
- binance
- cross-sectional
---

# Binance Crypto Cross-Sectional TSMOM 12-1 Daily

Binance USDT spot 페어 중 24h 거래량 상위 30종 풀 (`docs/specs/universe-scan-strategy-pattern.md` 정의 적용) 에서 12-1 month time-series momentum 점수 상위 10종을 매주 동일가중으로 보유. KRX 버전 ([[cs-tsmom-kr-daily]]) 의 자산군 미러.

Universe pin-date: **2026-05-06** (현재 24h 거래량 기준 → 생존편향 + listing bias 인정).

## 필터 및 universe

`fetch_top_universe(n=30)` 로 다음 필터 적용:
- 페어 suffix `USDT` (현물)
- 스테이블코인 base 제외: USDC, USD1, FDUSD, BUSD, TUSD, DAI, USDP, PYUSD, USDD
- 합성·페그 자산 제외: PAXG (gold), XAUT
- 정지·소멸 자산 제외: LUNC, USTC, FTT
- 레버리지 토큰 제외 (suffix UP/DOWN/BULL/BEAR)
- 가격이 $1±1% 사이이고 24h 변동 0.5% 미만이면 stablecoin-like 추가 제외

결과 (2026-05-06): BTC, ETH, SOL, TON, ZEC, BNB, XRP, DOGE, TAO, SUI, DOGS, NEAR, DASH, PEPE, LINK, ADA, FIL, VANA, IO, ICP, ENA, TRX, CHIP, AVAX, PENGU, VIRTUAL, D, APT, UNI, ONDO (30종, 백테스트 가능 29종).

## 진입

- 매주 (5 bar 마다, UTC 일자 기준):
  - score(sym) = `log(close[t-21] / close[t-252])`
  - 유동성 필터: 60d 평균 quote_volume ≥ 1천만 USDT
  - 점수 > 0 인 종목 중 score 상위 10 → 동일가중 (10%씩)
- 신생 listing 종목은 252-bar warmup 후 score 산출 가능 → 자동 entry pool 합류

look-ahead 방지: t-21, t-252 시점 close 만 사용.

## 진입 크기

- Equal-weight 1/N (N=10) per pick.
- 향후: inverse-vol weighting (60d std) — 변동성 큰 small-cap 의 영향 완화 후속.

## 청산

- 다음 리밸 시점에 top-10 에서 빠진 종목 전량 청산, 새 진입 종목으로 교체.
- BTC 252d drawdown ≤ -30% → 전량 현금. 일반 알트시즌 약세 안전망.

## 훅 소비

- `signals.compute("tsmom_12_1", close=close, long=252, skip=21)`.
- Universe builder: `src/universe/binance_top.py` (신규 — `top_n_by_volume(n, exclude_filters)`).
- Bar boundary: UTC 일자 마감 00:00. 24/7 시장이라 휴일 게이트 없음.
- Data: Binance public klines API (REST), parquet cache `data/cache/binance_daily/`.

## 비용

- Binance taker 0.04% × 2 = 8bp + 약 8bp slippage = **16bp 라운드트립**.
- VIP/maker 사용 시 5~10bp 까지 절감 가능 (실제 운영 단계 정밀화).
- `apply_cost(returns, positions, "binance_spot")` (신규 cost profile 등록 필요).

## 리스크 연동

```python
orchestrator.register_strategy("cs_tsmom_crypto_daily", strategy)
orchestrator.register_strategy_returns("cs_tsmom_crypto_daily", daily_return_series)
```

- `daily_return_series`: index=UTC 일자, 값=바스켓 일수익률 (비용 차감 후).
- BTC 252d drawdown ≤ -30% 일자는 0% 노출 → 시계열에 0 일수익률 기록.
- `intersect_trading_days` 로 KRX 전략 (주말 결측) 과 정렬 후 ENB/CVaR 평가.

## 백테스트 결과 (2026-05-06)

| Metric | Strategy | BTC |
|--------|---------:|----:|
| Sharpe | **1.328** | 0.989 |
| MDD | **-52.42%** | -76.63% |
| Ann.Return | **90.85%** | 51.61% |
| Calmar | 1.733 | — |
| Final Equity (5y) | 48.5× | ~7.7× |
| Avg Holdings | 8.1 | — |
| Annual Turnover | 20.3× one-way | — |
| Exposure | 72.4% days | 100% |

- bench: `scripts/bench_cs_tsmom_crypto.py`
- 결과: `docs/work/active/swing-strategy-portfolio/cs_tsmom_crypto_report.md`
- KRX 버전 대비: Sharpe (1.33 vs 0.87), MDD (-52% vs -43%), Ann (91% vs 23%) — 크립토 변동성 + 알트시즌 효과 반영.

## 운영 규칙

- **backtest-only (현 단계)**. 라이브 주문 발주는 후속 이슈.
- Binance public API (no auth) 가 백테스트 데이터 출처. 라이브는 Binance Spot 공식 SDK 또는 기존 `src/data_lake/fetcher.py` 확장.
- Universe pin-date 2026-05-06 고정. 분기별 (3·6·9·12월 말) 24h 거래량 재집계로 universe rotation 후속.
- 8.1 avg holdings (top-10 < 10) — 양의 점수 종목이 부족한 시기 (bear market) 가 있어 평균이 낮아짐. 정상.
- **Survivorship + listing bias 인정**: 현재 시점 top-30 universe + 신생 코인 자동 합류 → 사라진 알트 누락. 실거래 기대치는 백테스트 보다 낮음 (~ -10 ~ -20%p ann 추정).

## 한계 및 후속 작업

- MDD -52% 도 가벼운 수치는 아님. 알트시즌 -70% 폭락 (2022-LUNA, FTX) 을 BTC crash guard 가 일부만 회피.
- Avg turnover 20.3× → KRX (14.5×) 보다 회전 빠름. 비용 민감도 더 큼.
- 개선 후보:
  - inverse-vol weighting (sizer 정밀화)
  - BTC + ETH + 안정 알트 separate buckets (correlation 분산)
  - 6개월 momentum 으로 신호 decay 가속 (MDD 개선 시도)
  - PIT universe 로 listing bias 제거

## 관련 노트

- [[universe-scan-strategy-pattern]] — 본 전략이 따르는 패턴 spec
- [[cs-tsmom-kr-daily]] — KRX 자산군 자매 전략
- [[42-cross-sectional-momentum-crypto]] — 크립토 cross-sectional 학술 배경
- [[44-time-series-momentum-crypto]] — 시계열 모멘텀 학술 배경
- [[19-portfolio-risk]] — 다전략 리스크 통합

## 출처

- Moskowitz, Ooi, Pedersen (2012) — *Time Series Momentum*, JFE.
- Asness, Moskowitz, Pedersen (2013) — *Value and Momentum Everywhere*, JoF.
- 본 레포: `docs/specs/universe-scan-strategy-pattern.md`, [[42-cross-sectional-momentum-crypto]], [[44-time-series-momentum-crypto]].
