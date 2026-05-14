---
type: strategy
id: cs-bb-macd-kr
name: KRX Cross-Sectional Bollinger Rebound + MACD Cross
status: inactive
paradigm: universe-scan
instruments:
- kospi200+kosdaq150
market: krx
timeframe: 1d
uses_signals:
- bollinger
- macd
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-08
sharpe_bt: -0.323
sharpe_live: null
mdd_bt: -0.766
annual_return_bt: -0.1343
backtest_period: "2020-01-01/2025-12-30"
last_updated: 2026-05-08
summary_ko: |
  KRX 시총 top-200 + KOSDAQ top-150 풀에서 BB 하단 이탈 후 회복 +
  MACD bullish 교차가 동시에 있는 종목을 cross-sectional 로 점수화하여
  상위 20 종목을 주간 동일가중 보유. 단일종목 swing_bb_macd 의 universe-scan
  변환본.
tags:
- pattern:universe-scan
- mean-reversion
- krx
- equity
- cross-sectional
---

# KRX Cross-Sectional Bollinger Rebound + MACD Cross

> ⚠️ **status: inactive (2026-05-08)**. 5y bench Sharpe **-0.323** / MDD **-76.6%** / Ann **-13.4%** — 패턴 부적합. 코드는 유지하되 production.yaml 미등록. score 정의 재설계 시도 가능 (BB 하단 strict 조건 완화).

`swing_kr_daily.swing_bb_macd` 의 universe-scan 변환본 (#218). KRX top-350 풀에서 BB 하단 이탈 흔적 + 회복도 + MACD 양수 강도 곱으로 cross-sectional 점수화.

Universe pin-date: **2026-05-06**.

## Score

```
breached     = max(close < lower_band) over last 5 bars   # 최근 5봉 BB 하단 이탈
bb_recovery  = (close - lower) / (mid - lower)            # 0~1
macd_strength = max(0, MACD - signal_line)
score = bb_recovery * macd_strength * breached
```

look-ahead 방지: lower/mid 모두 lookback rolling.

## 진입 / 리밸 / 청산 / 비용 / 리스크 연동

universe-scan 패턴 표준 — 매주 금요일 마감 → top-20 동일가중 → 55bp / KOSPI -15% drawdown crash guard / `register_strategy_returns("cs_bb_macd_kr", ...)`.

## 코드

- 모듈: `src/backtest/strategies/cs_bb_macd_kr.py`

## 한계

5y bench 미실시. 신호 빈도가 KRX 단일종목에서는 매우 낮았으므로 (1 trade) universe 확장 효과 미검증 — 후속 bench 필요.

## 관련 노트

- [[universe-scan-strategy-pattern]]
- [[bollinger-breakout]]

## 출처

- Bollinger (1980s) — Bollinger Bands.
- Appel (1979) — MACD.
- 본 레포 #218.
