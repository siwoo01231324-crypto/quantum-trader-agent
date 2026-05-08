---
type: strategy
id: cs-adx-ma-kr
name: KRX Cross-Sectional EMA Cross + ADX Filter
status: backtest
instruments:
- kospi200+kosdaq150
market: krx
timeframe: 1d
uses_signals:
- sma-cross
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-08
sharpe_bt: 1.031
sharpe_live: null
mdd_bt: -0.4536
annual_return_bt: 0.3355
backtest_period: "2020-01-01/2025-12-30"
last_updated: 2026-05-08
summary_ko: |
  KRX 시총 top-200 + KOSDAQ top-150 풀에서 5일/20일 EMA 골든크로스 +
  ADX(14) ≥ 25 양 조건 만족 종목을 cross-sectional 점수화하여 상위 20 동일가중 주간 보유.
  단일종목 swing_adx_ma 의 universe-scan 변환본.
tags:
- pattern:universe-scan
- trend
- krx
- equity
- cross-sectional
---

# KRX Cross-Sectional EMA Cross + ADX Filter

`swing_kr_daily.swing_adx_ma` 의 universe-scan 변환본 (#218).

## Score

```
ema_gap = max(0, ema_fast - ema_slow) / ema_slow   # 양수면 골든크로스 영역
adx_norm = max(0, ADX - 25) / 100                  # 임계값 초과분
score = ema_gap * adx_norm
```

## 진입 / 리밸 / 청산 / 비용 / 리스크 연동

universe-scan 표준. `register_strategy_returns("cs_adx_ma_kr", ...)`.

## 코드

`src/backtest/strategies/cs_adx_ma_kr.py`.

## 한계

단일종목 bench 에서 0 trades — 조건 너무 strict. universe 확장 시 신호 빈도 회복 가능성 있으나 미검증.

## 관련 노트

- [[universe-scan-strategy-pattern]]
- [[sma-cross]]

## 출처

- Wilder (1978) — ADX.
- 본 레포 #218.
