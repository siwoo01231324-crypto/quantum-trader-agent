---
type: work-done
id: 02_implementation
name: "#96 KIS 분봉 fetcher + momo_kis_v1 구현 결과"
status: active
---

# #96 구현 결과

## 구현 파라미터

| 파라미터 | 값 | 설명 |
|---------|-----|------|
| `symbol` | `005930` | 삼성전자 (기본값) |
| `RSI_PERIOD` | 14 | RSI 계산 기간 |
| `LOOKBACK` | 14 | divergence 탐지 윈도우 |
| `INTERVAL_MIN` | 15 | 15분봉 |
| `sizing_mode` | `"half-kelly"` | 기본 진입 크기 전략 |
| `sizing_lookback` | 60 | 사이징에 사용하는 bar 수 |
| `kelly_k` | 0.5 | Half-Kelly 배율 |
| `ewma_lam` | 0.94 | EWMA σ 감쇠 계수 (RiskMetrics 1996) |
| `target_annual` | 0.15 | vol-target 연간 목표 (참고용) |
| `periods_per_year` | 6552 | 15m × 26bars/day × 252일 |
| `warmup` | 43 bars | RSI_PERIOD + LOOKBACK×2 + 1 |

## 일수익률 시계열 샘플 (mock, 10거래일)

아래는 `register_strategy_returns` 통합 테스트에서 사용한 mock daily returns (단위: 소수점):

| 날짜 | 수익률 |
|------|--------|
| 2026-04-01 | +1.00% |
| 2026-04-02 | -0.50% |
| 2026-04-03 | +0.80% |
| 2026-04-06 | -0.30% |
| 2026-04-07 | +1.20% |
| 2026-04-08 | +0.20% |
| 2026-04-09 | -0.70% |
| 2026-04-10 | +0.50% |
| 2026-04-13 | +0.30% |
| 2026-04-14 | -0.10% |

- `refresh_portfolio_risk()` — 2개 전략 등록 + 10일치 returns → `PortfolioRiskReport` 반환 확인 (ShortSampleWarning 발생하나 정상 처리)

## 테스트 결과

| 테스트 파일 | 건수 | 결과 |
|------------|------|------|
| `tests/brokers/kis/test_intraday_schemas.py` | 5 | 전체 PASS |
| `tests/brokers/kis/test_broker_kis_intraday.py` | 7 | 전체 PASS |
| `tests/data_lake/test_fetch_kis_intraday_ohlcv.py` | 5 | 전체 PASS |
| `tests/backtest/test_momo_kis_v1.py` | 7 | 전체 PASS |
| `tests/test_portfolio_orchestrator_async.py` | 14 | 전체 PASS (기존 12 + 신규 2) |
| **합계** | **38** | **전체 PASS** |

불변식 체크: `python scripts/check_invariants.py --strict` → 통과 (110 노트 검증)

## 구현 산출물

| 파일 | 변경 종류 |
|------|---------|
| `src/brokers/kis/tr_ids.py` | `TR_ID_INTRADAY_PRICE = "FHKST03010200"` append |
| `src/brokers/kis/schemas.py` | `KISIntradayBar` Pydantic 모델 append |
| `src/brokers/kis/price_client.py` | `fetch_intraday_ohlcv_raw`, `_PATH_INTRADAY`, `_call_intraday_with_429_retry` append |
| `src/data_lake/fetcher.py` | `fetch_kis_intraday_ohlcv` append |
| `src/backtest/strategies/momo_kis_v1.py` | 신규 |
| `src/backtest/strategies/.ai.md` | 구조 섹션 한 줄 추가 |
| `docs/specs/strategies/momo-kis-v1.md` | 신규 |
| `tests/brokers/kis/test_intraday_schemas.py` | 신규 |
| `tests/brokers/kis/test_broker_kis_intraday.py` | 신규 |
| `tests/data_lake/test_fetch_kis_intraday_ohlcv.py` | 신규 |
| `tests/backtest/test_momo_kis_v1.py` | 신규 |
| `tests/test_portfolio_orchestrator_async.py` | 2건 추가 |

## 바 바운더리 2중 안전망 검증

- `_is_my_bar_boundary(ts)`: KST 변환 → weekday<5 → `is_krx_holiday` → 09:00≤t≤15:30 → `minute%15==0 and second==0`
- harness(`run_bar`) + self-guard 양쪽에서 거래시간 외 ts → hold 반환 확인 (`test_momo_kis_v1_run_bar_trading_hours`)

## KIS 분봉 API 제약 사항 (docstring 명시)

- 당일 + 최근 30일까지만 조회 가능
- `target_date < today - 30days` 인 경우 warning 로그 + skip (raise 하지 않음)
- 1년치 학습 데이터는 cron 매일 1회 누적 적재 방식 권장
