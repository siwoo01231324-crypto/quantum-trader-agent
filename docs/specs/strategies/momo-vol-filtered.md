---
type: strategy
id: momo-vol-filtered
name: BTCUSDT 4h Volatility-Filtered Momentum
status: backtest
instruments:
- BTCUSDT
timeframe: 4h
uses_signals:
- macd
- realized-vol
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
  비트코인 4시간봉. MACD 지표가 위로 돌아 모멘텀이 양수이고,
  최근 변동성이 연 80% 미만일 때만 매수.
  "오르는 추세 + 너무 위험하지 않은 구간" 만 진입하는 보수적 모멘텀 전략.
tags:
- momentum
- crypto
- vol-filter
---

# BTCUSDT 4h Volatility-Filtered Momentum

BTCUSDT 4시간봉 기준 MACD 모멘텀 + 변동성 필터 전략. 변동성이 낮을 때만 MACD 진입을 허용하여 고변동 구간 진입을 차단. momo_btc_v2(15m) 와 시간축·변동성 필터로 차별화.

## 진입

- `MACD histogram > 0 AND MACD line > signal line` (모멘텀 확인)
- `realized_vol(close, 20, annualize=365*6) < vol_ceiling` (기본 vol_ceiling=0.80, 연 80%)
- 두 조건 모두 충족 시 **buy**.

## 진입 크기

- EWMA σ (λ=0.94) 기반 `vol_target(sigma, target_annual=0.20, periods_per_year=365*6)`.
- 연 20% 변동성 목표 (4h bar 기준).

## 청산

- `MACD histogram < 0` → **sell** (정상 청산).
- `realized_vol > vol_ceiling * 1.5` → **sell** (비상 청산, 극단 변동성).
- 전량 청산 (size=1.0).

## 훅 소비

- `signals.compute("macd", close=close, slow=26)` — histogram, macd, signal 컬럼 반환.
- `signals.compute("realized_vol", close=close, window=20, annualize=365*6)` — 4h 연환산 변동성.
- `signals.compute("atr", high=high, low=low, close=close)` — confidence 계산용.
- Bar boundary: `ts.hour % 4 == 0 and ts.minute == 0 and ts.second == 0` (UTC 00/04/08/12/16/20시).

## 리스크 연동

```python
orchestrator.register_strategy("momo_vol_filtered", strategy)
orchestrator.register_strategy_returns("momo_vol_filtered", daily_return_series)
```

- `daily_return_series`: index=date, 값=비용 차감 일수익률 (instrument_type="crypto", cost_rate=0.001 편도).
- `apply_cost(returns, positions, "crypto")` 적용 후 공급.

## 운영 규칙

- momo_btc_v2 와 같은 종목(BTCUSDT) 이지만 시간축(4h vs 15m) 과 변동성 필터로 진입 구간 차별화.
- ρ(momo_vol_filtered, momo_btc_v2) > 0.6 이면 vol_ceiling 을 0.60 으로 낮춰 필터 강화.
- 파라미터 조정 이력은 `02_implementation.md` 에 기록.
- Walk-forward validation 은 #79 AC 범위 외. 후속 이슈에서 수행 예정.

## 관련 노트

- [[19-portfolio-risk]] — ENB/ρ 게이트 (enb_ratio >= 0.5, avg ρ <= 0.6)
- [[20-position-sizing]] — vol_target 이론적 근거
- [[13-feature-alpha-catalog]] — 팩터 카탈로그 (macd, realized_vol, atr 스펙)
- [[01_plan]] — 전략 카탈로그 확장 구현 계획
