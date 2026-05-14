---
type: strategy
id: cs-rsi-div-kr
name: KRX Cross-Sectional RSI Bullish Divergence
status: backtest
paradigm: universe-scan
instruments:
- kospi200+kosdaq150
market: krx
timeframe: 1d
uses_signals:
- rsi-divergence
risk_rules:
- max-drawdown-5pct
owner: siwoo
created: 2026-05-08
sharpe_bt: 0.970
sharpe_live: null
mdd_bt: -0.3546
annual_return_bt: 0.2458
backtest_period: "2020-01-01/2025-12-30"
last_updated: 2026-05-08
summary_ko: |
  KRX 시총 top-200 + KOSDAQ top-150 풀에서 직전 20봉 RSI 강세 다이버전스
  (가격은 신저점 근처인데 RSI 는 더 떨어지지 않음) 점수 상위 20 종목을
  주간 동일가중 보유. 단일종목 momo_kis_v1 의 universe-scan 변환본.
tags:
- pattern:universe-scan
- mean-reversion
- krx
- equity
- cross-sectional
---

# KRX Cross-Sectional RSI Bullish Divergence

KRX 시총 top-200 + KOSDAQ top-150 universe 에서 cross-sectional RSI 강세 다이버전스 점수 상위 20 종목을 매주 동일가중 보유. 단일종목 `momo_kis_v1` (#96, 005930) + `swing_kr_daily.momo_kis_daily` 의 universe-scan 1:1 변환본 (#218).

Universe pin-date: **2026-05-06** (current Marcap; survivorship bias 인정).

## Score 정의

```
rsi_diff = RSI(14) - RSI_lookback_min                # 현재 RSI 가 최근 20봉 최저값 대비 상승폭
price_drop = (close_lookback_min / close - 1)        # 최근 20봉 최저가 대비 하락폭
score = rsi_diff * (1 + price_drop)
```

→ "가격은 떨어졌는데 RSI 는 덜 떨어졌다" 정도가 클수록 score 가 큼.

look-ahead 방지: rsi/close 모두 shift(1) 적용된 rolling 사용.

## 진입 / 리밸 / 청산 / 비용 / 리스크 연동

universe-scan 패턴 표준 따름 ([[universe-scan-strategy-pattern]]):

- 매 5봉 (주간 금요일 마감) 에 score 상위 20 동일가중 (5%씩)
- 점수 ≤ 0 또는 유동성 (60d 평균 거래대금 ≥ 10억 KRW, 종가 ≥ 1000원) 미달이면 picks 제외
- 다음 리밸 시점에 picks 갱신 → 빠진 종목 청산, 새 종목 진입
- 비용: 55bp 라운드트립 (commission + slippage + 거래세)
- KOSPI 252d drawdown ≤ -15% 시 전량 현금
- Risk 연동: `register_strategy_returns("cs_rsi_div_kr", daily_return_series)`

## 코드

- 모듈: `src/backtest/strategies/cs_rsi_div_kr.py`
- 함수: `score_panel(close, ...)`, `compute_weights(close, turnover, ...)`
- 헬퍼: `src/backtest/strategies/_cs_helpers.py` (RSI · build_weights · liquid_mask)

## 운영 규칙

- **backtest only (현 단계)**. AsyncStrategy wrap + 라이브 발주는 #218 후속 phase.
- KIS broker 동적 universe quote 필요 (#218 §2 broker 확장).

## 한계 / 후속

- 현재 score 는 RSI 최저값 + 가격 최저값 proxy 사용 (정확한 argmin 매칭 대신). 정밀도 ↑ 위해 후속 옵션.
- 5y bench 미실시 — bench 스크립트 추가 후 frontmatter 수치 갱신.

## 관련 노트

- [[universe-scan-strategy-pattern]] — 본 전략이 따르는 패턴 spec
- [[cs-tsmom-kr-daily]] — KRX universe-scan 자매 전략 (모멘텀 score)
- [[rsi-divergence]] — RSI 다이버전스 시그널 정의

## 출처

- Wilder (1978) — *New Concepts in Technical Trading Systems* (RSI).
- 본 레포 #96 (momo_kis_v1), #218 (universe-scan 전면 전환).
