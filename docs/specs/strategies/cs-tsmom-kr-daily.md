---
type: strategy
id: cs-tsmom-kr-daily
name: KRX Cross-Sectional TSMOM 12-1 Daily
status: backtest
paradigm: universe-scan
instruments:
- kospi200+kosdaq150
market: krx
timeframe: 1d
uses_signals:
- tsmom-12-1
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-06
sharpe_bt: 0.871
sharpe_live: null
mdd_bt: -0.4299
annual_return_bt: 0.2299
backtest_period: "2020-01-01/2025-12-31"
last_updated: 2026-05-06
summary_ko: |
  KOSPI 시총 상위 200 + KOSDAQ 시총 상위 150 종목 중에서
  12-1 month time-series momentum 점수 (log(close[t-21]/close[t-252]))
  상위 20 종목을 동일 비중으로 매주 금요일 마감에 리밸런싱.
  "강한 추세 종목을 따라가되 직전 1개월은 reversal 회피" 가설.
tags:
- pattern:universe-scan
- momentum
- krx
- equity
- cross-sectional
---

# KRX Cross-Sectional TSMOM 12-1 Daily

KOSPI 시총 상위 200 + KOSDAQ 시총 상위 150 종목 = 약 350 종목 풀에서 12-1 month time-series momentum 점수 상위 20 종목을 매주 금요일 마감에 동일가중으로 보유. "장기 추세는 따라가되 직전 1개월 reversal 은 회피" (Moskowitz/Ooi/Pedersen 2012; Asness/Moskowitz/Pedersen 2013).

Universe pin-date: **2026-05-06** (현재 시총 기준 → 생존편향 인정).

## 진입

- 매 금요일 마감 (KRX 15:30 KST):
  - score(code) = `log(close[t-21] / close[t-252])` — 12-1 momentum (log return, 직전 21바 reversal skip)
  - 유동성 필터: 60일 평균 거래대금 ≥ 10억 KRW, 종가 ≥ 1,000 KRW
  - 점수 > 0 인 종목 중 score 상위 20 → 동일가중 (5%씩)
- 비-리밸일은 가중치 유지 (가격 drift 허용)

look-ahead 방지: score 계산 시 사용하는 close 는 t-21, t-252 시점 — 현재 t 의 정보 미사용.

## 진입 크기

- Equal-weight 1/N (N=20) per pick.
- 향후 정밀화 옵션: inverse-vol (60d std), Half-Kelly. 1차 spec 은 동일가중 고정.

## 청산

- 다음 금요일 리밸 시점에 top-20 에서 빠지면 0% (전량 청산), 새로 진입한 종목으로 교체.
- 강제 청산 (crash guard): KOSPI 252d drawdown ≤ -15% → 전량 현금 (지속 시 재진입 보류).

## 훅 소비

- `signals.compute("tsmom_12_1", close=close, long=252, skip=21)` — Series[code → score]. (신규 시그널 등록 필요)
- Universe builder: `src/universe/krx_top.py` — `top_n_by_marcap(market, n, as_of)` (신규).
- Bar boundary: KRX 장마감 `time(15, 30) KST`, 평일, `not is_krx_holiday(date)`.
- Data: `market_snapshot["ohlcv_history"]` — 350 종목 × 252+ bars.

## 비용

- KRX 라운드트립 평균 55bp (commission 15 + slippage 20 + 거래세 25/2 ≈ 보수적).
- 종목별 차등 (대형 vs 소형) 후속 정밀화. 1차는 평균값 적용.
- `apply_cost(returns, positions, "krx")` 활용.

## 리스크 연동

```python
orchestrator.register_strategy("cs_tsmom_kr_daily", strategy)
orchestrator.register_strategy_returns("cs_tsmom_kr_daily", daily_return_series)
```

- `daily_return_series`: index=KRX거래일, 값=바스켓 일수익률 (비용 차감 후).
- KOSPI 252d drawdown ≤ -15% 일자는 0% 노출 → 시계열에 0 일수익률 기록.
- `intersect_trading_days` 로 crypto / single-ticker 전략과 정렬 후 ENB/CVaR 평가.

## 백테스트 결과 (2026-05-06)

| Metric | Strategy | KOSPI |
|--------|---------:|------:|
| Sharpe | **0.871** | 0.656 |
| MDD | -42.99% | -35.71% |
| Ann.Return | **22.99%** | 11.98% |
| Calmar | 0.535 | — |
| Final Equity | 3.35× | 1.94× |
| Avg Holdings | 20.0 | — |
| Annual Turnover | 14.5× one-way | — |
| Exposure | 78.4% days | 100% |

- bench: `scripts/bench_cs_tsmom_kr.py` (cache: `data/cache/krx_daily/`)
- 결과: `docs/work/active/swing-strategy-portfolio/cs_tsmom_v1_baseline.md`
- 변형 비교: v1 baseline (위) > v2 MA200+dd-10% (Sharpe 0.711) > v3 top-10 (Sharpe 0.672)

## 운영 규칙

- **backtest-only (현 단계)**. 라이브 주문 발주는 #80 후속 (orchestrator weights → orders 변환).
- KIS TR `FHKST03010100` (inquiry) 일봉 수집; paper 환경에서만 실행.
- Universe pin-date 2026-05-06 고정. 분기별 (3·6·9·12월 말) 시총 재집계로 universe rotation 후속 이슈.
- Walk-forward validation 후속 이슈에서 수행. 1차 spec 은 in-sample.
- **Survivorship bias 인정**: 현재 시총 기준 universe → 사라진 종목 누락. 실거래 기대치는 백테스트 보다 낮음 (~ -3 ~ -5%p ann 추정).

## 한계 및 후속 작업

- MDD -43% 는 KOSPI -36% 보다 더 큼 → 모멘텀 종목이 regime shift 직후 더 빨리 무너짐 (검증된 momentum 약점). MA200 regime filter, top-N 축소 모두 개선 안 됨 (v2/v3 검증).
- 개선 후보 (#220-series 후속):
  - inverse-vol weighting per pick (sizer 정밀화)
  - 6개월 momentum (lookback 126) 으로 신호 decay 가속
  - PIT (point-in-time) universe 로 survivorship bias 제거
  - KOSDAQ 슬리피지 종목별 차등

## 관련 노트

- [[universe-scan-strategy-pattern]] — 본 전략이 따르는 패턴 spec
- [[breakout-donchian]] — 동일 패턴의 다른 사례
- [[19-portfolio-risk]] — 다전략 리스크 통합
- [[20-position-sizing]] — 사이징 이론
- [[42-cross-sectional-momentum-crypto]] — cross-sectional 모멘텀 학술 배경
- [[44-time-series-momentum-crypto]] — 시계열 모멘텀 학술 배경

## 출처

- Moskowitz, Ooi, Pedersen (2012) — *Time Series Momentum*, JFE.
- Asness, Moskowitz, Pedersen (2013) — *Value and Momentum Everywhere*, JoF.
- 본 레포: `docs/specs/universe-scan-strategy-pattern.md`, #79 (전략 카탈로그 확장), #70 (리스크 모듈).
