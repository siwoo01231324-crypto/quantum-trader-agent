---
type: strategy
id: cs-macd-vol-crypto
name: Crypto Cross-Sectional MACD + Volatility Filter
status: backtest
paradigm: universe-scan
instruments:
- binance-usdt-spot-top30
market: crypto
timeframe: 1d
uses_signals:
- macd
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-08
sharpe_bt: 1.012
sharpe_live: null
mdd_bt: -0.8451
annual_return_bt: 0.6873
backtest_period: "2020-01-01/2025-12-30"
last_updated: 2026-05-08
summary_ko: |
  Binance USDT spot 24h 거래량 top-30 풀에서 MACD bullish 강도 + 변동성
  ceiling (annualized) 미만 조건 동시 만족 종목 top-10 주간 동일가중 보유.
  단일종목 momo_vol_filtered 의 universe-scan 변환본.
tags:
- pattern:universe-scan
- momentum
- crypto
- binance
- cross-sectional
---

# Crypto Cross-Sectional MACD + Volatility Filter

`momo_vol_filtered` (BTC 4h MACD + realized_vol < 80%) 의 universe-scan 변환본 (#218).

Universe pin-date: **2026-05-08** (현재 Binance USDT spot 24h 거래량 top-30 기준 → 스테이블/페그/레버리지 제외, 생존편향 인정).

가설: "추세는 양수 + 변동성은 ceiling 미만" 조건이 동시 충족된 종목만 안전한 추세 추종.

## Score

```
macd_strength = max(0, MACD - signal_line) / abs(close)   # 정규화
vol_pass = (realized_vol < vol_ceiling)                   # bool
score = macd_strength * vol_pass
```

기본 vol_window = 30 일, vol_ceiling = 0.80 (annualized 80%).

## 진입 / 리밸 / 청산 / 비용 / 리스크 연동

- 주간 top-10 동일가중
- 유동성 ≥ 10M USDT 거래대금
- 비용 16bp 라운드트립
- BTC 252d drawdown ≤ -30% 청산
- `register_strategy_returns("cs_macd_vol_crypto", ...)`

## 코드

`src/backtest/strategies/cs_macd_vol_crypto.py`.

## 한계

vol_ceiling 80% 가 BTC 4h 기준 보정값 — 알트 일봉에서는 ceiling 재조정 가능성 (대부분 알트 vol > 80% annualized). 후속 bench 시 grid 적용.

## 관련 노트

- [[universe-scan-strategy-pattern]]
- [[cs-tsmom-crypto-daily]]
- [[cs-rsi-div-crypto]]

## 출처

- Appel (1979) — MACD.
- 본 레포 #218.
