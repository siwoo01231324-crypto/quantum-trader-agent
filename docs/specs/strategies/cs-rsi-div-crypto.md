---
type: strategy
id: cs-rsi-div-crypto
name: Crypto Cross-Sectional RSI Bullish Divergence
status: backtest
paradigm: universe-scan
instruments:
- binance-usdt-spot-top30
market: crypto
timeframe: 1d
uses_signals:
- rsi-divergence
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-08
sharpe_bt: 1.015
sharpe_live: null
mdd_bt: -0.8166
annual_return_bt: 0.6282
backtest_period: "2020-01-01/2025-12-30"
last_updated: 2026-05-08
summary_ko: |
  Binance USDT spot 24h 거래량 top-30 (스테이블/페그/레버리지 제외) 풀에서
  RSI 강세 다이버전스 점수 상위 10종을 주간 동일가중 보유. 단일종목
  momo_btc_v2 의 universe-scan 변환본.
tags:
- pattern:universe-scan
- mean-reversion
- crypto
- binance
- cross-sectional
---

# Crypto Cross-Sectional RSI Bullish Divergence

`momo_btc_v2` (BTC 15m RSI 다이버전스) 의 universe-scan 변환본 (#218). KRX 자매 [[cs-rsi-div-kr]] 와 동일 score logic, 자산군 액세서리만 다름 (가격 하한 없음, 24h quote_volume 필터).

Universe pin-date: **2026-05-06** (Binance current top-30, listing bias 인정).

## Score

KRX 버전과 동일 — `cs_rsi_div_kr.score_panel` 재사용.

## 진입 / 리밸 / 청산 / 비용 / 리스크 연동

- 매주 (5봉 = 7일) 동일가중 top-10
- 유동성: 60d 평균 quote_volume ≥ 10M USDT, 종가 ≥ 0
- 비용: 16bp 라운드트립 (taker × 2 + slippage)
- BTC 252d drawdown ≤ -30% 시 전량 청산
- `register_strategy_returns("cs_rsi_div_crypto", ...)`

## 코드

`src/backtest/strategies/cs_rsi_div_crypto.py`.

## 한계

5y bench 미실시. RSI 다이버전스 점수 정의가 KRX 기반이라 알트 변동성에서 효과 별도 검증 필요.

## 관련 노트

- [[universe-scan-strategy-pattern]]
- [[cs-rsi-div-kr]]
- [[cs-tsmom-crypto-daily]]

## 출처

- Wilder (1978).
- 본 레포 #218.
