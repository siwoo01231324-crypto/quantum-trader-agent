---
id: smoke-1m-roundtrip-kis
name: Smoke 1m Round-trip (KIS 005930)
type: strategy
status: smoke
paradigm: single-ticker
instruments: ["005930"]
timeframe: 1m
owner: 성시우
created: 2026-05-15
tags: [smoke, operator-only, no-alpha]
summary_ko: |
  대시보드 "거래 시작" → KIS paper 005930 통로 검증 스모크.
  매 1분 buy/sell 토글. SMOKE_TEST_ENABLED=1 없으면 hold 만 반환.
sharpe_bt: null
mdd_bt: null
annual_return_bt: null
backtest_period: "N/A (smoke only)"
last_updated: 2026-05-15
---

# smoke-1m-roundtrip-kis — KIS paper 005930 통로 검증

## 거동
매 1분 봉마다 005930 에 대해 buy ↔ sell 토글. size=1% (`SMOKE_SIZE_FRACTION` env 로 조절).

## 활성화 조건 (둘 다 필요)
1. production.yaml entry uncomment (default uncomment 됨 — `smoke-1m-roundtrip-kis`)
2. `SMOKE_TEST_ENABLED=1` env

env 없으면 모든 신호 hold. 운영 환경 휘발 위험 zero.

## 운영 절차
```powershell
$env:SMOKE_TEST_ENABLED = "1"
.\qta.exe         # broker 자동으로 smoke-dual 선택 → KIS + Binance 병렬
# 대시보드에서 "거래 시작" 클릭
```

## 운영 사용 금지
알파 0. 매 분 round-trip 비용만 누적. 검증 완료 즉시 env 제거.

## 리스크 연동
스모크 전용 → `register_strategy_returns` 생략 (`.ai.md` 예외 조항).

## 관련
- 코드: `src/backtest/strategies/smoke_1m_roundtrip.py`
- 테스트: `tests/backtest/test_smoke_1m_roundtrip.py`
- 짝: `smoke-1m-roundtrip-binance.md` (BTCUSDT)
- 런타임: `scripts/live_run.py` `_run_smoke_dual` (smoke-dual broker mode)
