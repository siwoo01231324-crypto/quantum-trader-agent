---
id: smoke-1m-roundtrip-binance
name: Smoke 1m Round-trip (Binance BTCUSDT)
type: strategy
status: smoke
paradigm: single-ticker
instruments: ["BTCUSDT"]
timeframe: 1m
owner: 성시우
created: 2026-05-15
tags: [smoke, operator-only, no-alpha]
summary_ko: |
  대시보드 "거래 시작" → Binance testnet BTCUSDT 통로 검증 스모크.
  매 1분 buy/sell 토글. SMOKE_TEST_ENABLED=1 없으면 hold 만 반환.
sharpe_bt: null
mdd_bt: null
annual_return_bt: null
backtest_period: "N/A (smoke only)"
last_updated: 2026-05-15
---

# smoke-1m-roundtrip-binance — Binance testnet BTCUSDT 통로 검증

## 거동
매 1분 봉마다 BTCUSDT 에 대해 buy ↔ sell 토글. size=1%.

## 활성화 조건 (둘 다 필요)
1. production.yaml entry uncomment (default uncomment 됨 — `smoke-1m-roundtrip-binance`)
2. `SMOKE_TEST_ENABLED=1` env
3. `.env` 에 BINANCE_DEMO_API_KEY (or BINANCE_TESTNET_API_KEY / BINANCE_API_KEY) + secret

## 운영 절차
```powershell
$env:SMOKE_TEST_ENABLED = "1"
.\qta.exe
# 대시보드에서 "거래 시작" 클릭 → smoke-dual 자동 — KIS + Binance 양쪽 거래
```

## 운영 사용 금지
알파 0, 매 분 거래 비용 누적. 검증 완료 즉시 env 제거.

## 리스크 연동
스모크 전용 → `register_strategy_returns` 생략 (`.ai.md` 예외 조항).

## 관련
- 코드: `src/backtest/strategies/smoke_1m_roundtrip.py`
- 테스트: `tests/backtest/test_smoke_1m_roundtrip.py`
- 짝: `smoke-1m-roundtrip-kis.md` (005930)
- 런타임: `scripts/live_run.py` `_run_smoke_dual` (smoke-dual broker mode)
