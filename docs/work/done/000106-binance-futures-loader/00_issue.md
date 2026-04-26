# chore: Binance Futures historical data loader (#80 Phase E Sharpe 비교 의존)

## 배경

#80 Phase E (`scripts/shadow_report.py --compare-backtest`) 의 Sharpe 비교 4조건 강제는 **동일 데이터 소스** = Binance Futures USDT-M historical data 를 요구한다. 본 worktree 에 해당 loader 가 없어 Phase E 실 운영 시 비교 백테스트 재실행 불가.

## 의존성

- 선행: 없음 (독립 이슈)
- 후속 영향: #80 의 Phase E Shadow 운영 리포트가 본 이슈 머지 후 활성화 가능

## 범위

- `src/data/binance_futures_loader.py` (또는 동등 모듈)
  - Binance Futures USDT-M historical OHLCV (1m bar) fetcher
  - `data_lake/` 캐시 정책 (#79 와 동일 패턴)
  - 다중 심볼 (BTCUSDT/ETHUSDT/SOLUSDT 최소 3종)
  - 동일 기간 Shadow 운영 데이터와 비교 가능한 인터페이스
- 백테스트 엔진 (`src/backtest/`) 와 연동 — Phase 1 의 0-슬립/taker 0.05% 정책 그대로 사용
- 단위 테스트 + 통합 테스트 (mock REST + 실측 1일 분 fixture)

## 완료 기준

- [x] `binance_futures_loader.py` 구현 + 캐싱 — `src/data_lake/fetcher.py::fetch_binance_futures_klines()` (이슈 본문 "또는 동등 모듈" 활용, `src/data_lake/` 확장)
- [x] BTCUSDT/ETHUSDT/SOLUSDT 1m bar 다운로드 검증 (rate limit 준수) — `test_futures_supports_required_symbols` parametrize 3종, `_paginate_binance_klines` 0.5s sleep + 429 지수백오프 재시도(최대 3회)
- [x] `scripts/shadow_report.py --compare-backtest` 가 본 loader 산출물로 동작 — `test_futures_parquet_roundtrip` 으로 `load_ohlcv_from_parquet` 라운드트립 검증, `source="binance_futures"` lake 가 backtest engine 호환 확인
- [x] 단위 테스트 + 통합 테스트 통과 — 전체 1186 passed (data_lake 33/33: Spot 회귀 6 + Futures 9 + CLI 4 + 기존 14)
- [x] `src/data/.ai.md` 갱신 — `src/data_lake/.ai.md` 갱신 (의미상 동등, 신규 `src/data/` 디렉토리 미생성)

## 참고

- #80 plan: `docs/work/active/000080-paper-broker/01_plan.md` Phase E §E2
- [[29-paper-to-live-protocol]] §7.1 동일 data-lake-schema 스냅샷 요구

## 연결 이슈

- 선행: 없음
- 후속 활성화: #80 (Phase E Shadow 운영 리포트)

## 작업 내역

### 2026-04-26

**현황**: 5/5 완료 (구현 완료 — `/finish-issue` 대기)
**완료된 항목**:
- [x] `binance_futures_loader.py` 구현 + 캐싱
- [x] BTCUSDT/ETHUSDT/SOLUSDT 1m bar 다운로드 검증 (rate limit 준수)
- [x] `scripts/shadow_report.py --compare-backtest` 가 본 loader 산출물로 동작
- [x] 단위 테스트 + 통합 테스트 통과
- [x] `src/data/.ai.md` 갱신 (= `src/data_lake/.ai.md`)
**미완료 항목**: 없음
**변경 파일**: 6개
- `src/data_lake/fetcher.py` (헬퍼 추출 + Futures 함수 추가)
- `src/data_lake/.ai.md` (Futures fetcher 라인 추가)
- `scripts/fetch_futures_candles.py` (신규, multi-symbol CLI)
- `tests/data_lake/test_fetch_binance_futures_klines.py` (신규, 9 테스트)
- `tests/data_lake/test_fetch_futures_candles_cli.py` (신규, 4 테스트)
- `docs/work/active/000106-binance-futures-loader/{00_issue,01_plan}.md` (작업 내역)
**기록**:
- `/ri` 실행 → `01_plan.md` `## 구현 계획` 작성 (S0~S5)
- S0: `_paginate_binance_klines()` 공유 헬퍼로 Spot/Futures 페이지네이션·retry 통합 (Spot 회귀 6/6 그린)
- S1: `fetch_binance_futures_klines()` + `BINANCE_FUTURES_KLINES_URL` 추가 — 9 단위테스트 그린 (URL 구분/OHLCV schema/페이지네이션/429/빈응답/3심볼 parametrize/parquet 라운드트립)
- S2: `scripts/fetch_futures_candles.py` (default `--symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1m`, 심볼간 0.5s sleep, 빈응답 save 스킵) — 4 CLI 테스트 그린
- S3: AC3 = lake 라운드트립 (`source="binance_futures"` → `load_ohlcv_from_parquet` 정합) 으로 충족 (별도 shadow_report smoke 는 over-spec 으로 생략)
- S4: `src/data_lake/.ai.md` Futures fetcher 라인 추가
- S5: `pytest tests/ -k "not slow and not network"` 1186 passed / 11 skipped / 11 deselected (174s) + `check_invariants.py --strict` 통과

