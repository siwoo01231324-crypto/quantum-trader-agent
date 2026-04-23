---
type: signal
id: bollinger-breakout
name: Bollinger Breakout
inputs: [close]
lookback: 20
tags: [technical, volatility]
---

# Bollinger Breakout

가격이 Bollinger Bands 상단/하단을 돌파하는지 여부를 `%B` 지표로 판정하는 변동성 시그널이다.

## 계산

1. `middle = close.rolling(window).mean()` (기본 20)
2. `std = close.rolling(window).std(ddof=0)`
3. `upper = middle + n_std * std`, `lower = middle - n_std * std` (기본 `n_std=2`)
4. `pct_b = (close - lower) / (upper - lower)`
5. `pct_b > 1.0` → 상단 돌파 (롱 진입 후보), `pct_b < 0.0` → 하단 이탈 (숏 진입 후보)

`pct_b` 소비 시 반드시 `shift(1)` 적용 — 현재 바의 종가는 현재 바 말에 확정되므로 같은 바에 돌파 판정 후 체결은 look-ahead 가 된다.

## 관련 노트

- [[13-feature-alpha-catalog]] — §1.4 Bands 카테고리 (Bollinger `%B` / BandWidth)
