---
type: strategy
id: breakout-donchian
name: KOSPI200 Donchian Channel Breakout
status: backtest
instruments:
- KOSPI200
market: krx
timeframe: 1d
uses_signals:
- donchian
- atr
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-04-25
sharpe_bt: null
sharpe_live: null
mdd_bt: null
annual_return_bt: null
backtest_period: null
last_updated: 2026-05-05
summary_ko: |
  코스피200 종목 일봉. 20일 신고가를 돌파한 종목 중,
  ATR(변동성) 대비 돌파 강도 상위 10종목을 동일 비중으로 매수.
  10일 저점을 깨면 청산. "신고가 돌파 = 추세 시작" 가설로 따라붙는 추세추종 전략.
tags:
- breakout
- krx
- kospi200
- equity
---

# KOSPI200 Donchian Channel Breakout

KOSPI200 구성종목 일봉 기준 Donchian 채널 돌파 전략. 20일 신고가 돌파 종목 중 ATR 정규화 돌파강도 상위 10개 종목을 equal-weight 으로 보유. 10일 저점 이탈 시 개별 청산.

Universe pin-date: **2026-04-25** (survivorship 투명성 확보).

## 진입

- 각 종목: `upper_t = rolling_max(high, 20).shift(1)` (look-ahead 방지)
- `close[t] > upper_t` 인 종목이 돌파 후보.
- 복수 후보 → `(close[t] - upper_t) / atr_14.shift(1)` 내림차순 → **상위 10 종목** 선택.
- 빈 slot 에만 신규 진입 (기존 보유 유지).

## 진입 크기

- Equal-weight 10 slots × Half-Kelly (`fractional_kelly(k=0.5)`).
- `kelly_continuous(μ, σ)` — 바스켓 수익률 60일 이동평균/EWMA σ 기반.
- 사이징 수학은 `risk.sizing` 순수 함수만 경유; LLM 개입 금지.

## 청산

- `lower_t = rolling_min(low, 10).shift(1)` (10일 저점)
- `close[t] < lower_t` 이면 개별 종목 청산, 빈 slot 에 신규 돌파 종목 보충.

## 훅 소비

- `signals.compute("donchian", high=high, low=low, window=20)` — upper/lower/middle 반환.
- `signals.compute("atr", high=high, low=low, close=close, window=14).shift(1)` — look-ahead 방지.
- Bar boundary: KRX 장마감 `time(15, 30) KST`, 평일, `not is_krx_holiday(date)`.
- `market_snapshot["ohlcv_history"]` 에서 KOSPI200 전 종목 OHLCV 수신 필요 (lookback >= 21).

## 리스크 연동

```python
orchestrator.register_strategy("breakout_donchian", strategy)
orchestrator.register_strategy_returns("breakout_donchian", daily_return_series)
```

- `daily_return_series`: index=KRX거래일, 값=바스켓 일수익률 (비용 차감 후).
- 비용: `apply_cost(returns, positions, "krx")` — 매수 0.015%, 매도 0.245% (비대칭).
- `intersect_trading_days` 로 crypto 전략과 교집합 날짜 정렬 후 `compute_portfolio_risk_from_df` 에 투입.

## 운영 규칙

- **backtest-only**: #79 는 포트폴리오 레벨 단일 Signal 반환. 개별 종목 주문 생성은 #80 후속.
- KIS TR `FHKST03010100` (inquiry) 을 통해 일봉 수집; paper 환경에서만 실행.
- KOSPI200 universe pin-date 2026-04-25 고정. 이후 편입/편출 반영은 후속 이슈.
- Walk-forward validation 은 #79 AC 범위 외. 후속 이슈에서 수행 예정.

## 관련 노트

- [[19-portfolio-risk]] — ENB/ρ 게이트 (enb_ratio >= 0.5, avg ρ <= 0.6)
- [[20-position-sizing]] — Half-Kelly 이론적 근거
- [[13-feature-alpha-catalog]] — 팩터 카탈로그 (donchian, atr 스펙)
- [[01_plan]] — 전략 카탈로그 확장 구현 계획 (B안 KRX 전면 대체)
