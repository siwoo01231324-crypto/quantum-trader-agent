---
type: signal
id: rsi-divergence
name: RSI Divergence
inputs: [close, volume]
lookback: 14
source_model: null
tags: [technical, oscillator]
---

# RSI Divergence

가격과 RSI(14) 의 다이버전스를 탐지하는 기술적 신호. 주로 [[momo-btc-v2]] 에서 진입·청산 시그널로 사용된다.

## 계산
- 최근 14 bar 의 종가·거래량에서 RSI 를 계산하고, 가격 고점/저점과 RSI 고점/저점의 방향이 반대일 때 divergence 플래그.

## Confidence 산출 규칙

`Signal.confidence` 필드에 할당되는 값은 아래 결정적 수식으로만 계산된다. LLM 출력을 이 필드에 직접 할당하는 것은 금지다 — no LLM on exec path — invariant #6.

### 공식

```
confidence = clip(|div_magnitude| / atr_14 × min(bars_since_pivot / LOOKBACK, 1.0), 0.0, 1.0)
```

- `div_magnitude` = RSI divergence 크기 (RSI 고점/저점 차이의 절대값)
- `atr_14` = 14-bar ATR (정규화 분모)
- `bars_since_pivot` = 다이버전스 발생 후 경과 bar 수
- `LOOKBACK` = 14 (기본값)

### 작동 예시 (6-bar 테이블)

| bar | close | RSI | div_magnitude | atr_14 | bars_since_pivot | confidence |
|---|---|---|---|---|---|---|
| 0 | 100.0 | 35.0 | — | 2.0 | — | — |
| 1 | 98.0 | 33.0 | — | 2.1 | — | — |
| 2 | 97.5 | 34.5 | 1.5 | 2.0 | 1 | clip(1.5/2.0 × 1/14, 0,1) ≈ 0.054 |
| 3 | 97.0 | 36.0 | 3.0 | 2.0 | 2 | clip(3.0/2.0 × 2/14, 0,1) ≈ 0.214 |
| 4 | 96.5 | 38.0 | 5.0 | 2.0 | 7 | clip(5.0/2.0 × 7/14, 0,1) ≈ 1.000 (clamped) |
| 5 | 96.0 | 40.0 | 7.0 | 2.0 | 14 | clip(7.0/2.0 × 14/14, 0,1) = 1.000 |

### 구현 위치

`src/backtest/strategies/momo_btc_v2.py::_compute_confidence`

### 불변식 #6 준수

이 confidence 값은 RSI·ATR 기반 결정적 계산의 결과다. `anthropic`, `openai`, `langchain` 등 LLM API 호출 결과를 직접 할당하는 것은 `scripts/check_invariants.py::_check_llm_delegation` 이 정적 검출한다.

## 관련 노트

- [[13-feature-alpha-catalog]] — §1.1 RSI 표준 정의·KRX 적용 주의점
- [[momo-btc-v2]] — 본 신호를 소비하는 전략
- [[12-validation-protocol]] — 신호 검증 시 룩어헤드 방지 (lag 1)
- [[data-lake-schema]] — 신호 계산에 쓰이는 OHLCV 스키마
- [[signal-interface]] — Signal.confidence 필드 계약
