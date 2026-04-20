# feat: 마켓 데이터 수집 + Zipline 백테스트 + momo-btc-v2 실행

## 사용자 관점 목표
**전략 하나가 실제로 돌아서 Sharpe/MDD 결과가 나오는** end-to-end 백테스트 파이프라인을 만든다.

## 배경
- 현재 아키텍처 스펙 7개, 코드 6개 모듈 있지만 **실제 데이터가 흐르는 경로가 없음**
- `docs/background/11-backtest-engine-selection.md` 에서 **Zipline-reloaded** MVP 확정
- `docs/specs/data-lake-schema.md` 에 Parquet wide 포맷(OHLCV) 규약 정의됨
- `docs/specs/strategies/momo-btc-v2.md` — BTC 15m 모멘텀, RSI divergence 진입, max-drawdown-5pct 리스크

## 범위

### 포함
- **Phase 1 — 마켓 데이터 수집**
  - Binance REST API → BTC/USDT 과거 캔들 (15m, 최소 1년) 다운로드
  - `data-lake-schema.md` 의 wide 포맷으로 Parquet 저장 (`lake/ohlcv/exchange=binance/symbol=BTCUSDT/`)
  - DuckDB 카탈로그 등록 (선택)
  - `scripts/fetch_candles.py` — CLI: `--symbol BTCUSDT --interval 15m --start 2024-01-01 --end 2025-12-31`
- **Phase 2 — Zipline 커스텀 Bundle**
  - Parquet → Zipline bundle 변환 (`src/backtest/bundle.py`)
  - `zipline ingest -b qta-binance` 로 등록
- **Phase 3 — momo-btc-v2 전략 코드**
  - `src/backtest/strategies/momo_btc_v2.py` — Zipline `TradingAlgorithm` 서브클래스
  - RSI divergence 시그널 구현 (rsi-divergence spec 기반)
  - max-drawdown-5pct halt 로직
- **Phase 4 — 백테스트 실행 + 결과**
  - `scripts/run_backtest.py --strategy momo-btc-v2` CLI
  - 결과 메트릭: Sharpe, MDD, 승률, 총수익률, 거래 횟수
  - 결과를 `docs/specs/strategies/momo-btc-v2.md` 의 `sharpe_bt` 필드에 업데이트
  - `doc_agent` 로 백테스트 결과 노트 자동 생성 (`.draft.md`)

### 제외
- 라이브 트레이딩 / 브로커 연결 (별도 이슈)
- 멀티 전략 / 포트폴리오 레벨 (별도 이슈)
- KRX 데이터 (이번은 Binance 크립토만)
- walk-forward / 교차검증 (후속 이슈)

## 완료 기준
- [x] `python scripts/fetch_candles.py --symbol BTCUSDT --interval 15m` 로 1년 데이터 Parquet 저장
- [x] ~~`zipline ingest -b qta-binance` 성공~~ → `load_ohlcv_from_parquet()` 커스텀 엔진 데이터 로드 (Python 3.14 비호환으로 변경)
- [x] `python scripts/run_backtest.py --strategy momo-btc-v2` 로 백테스트 실행, Sharpe/MDD 출력
- [x] 테스트 코드 포함 (데이터 수집 mock + 전략 로직 + 백테스트 러너) — 33 tests (28 pass, 1 skip)
- [x] 결과 메트릭이 `momo-btc-v2.md` 프론트매터에 반영
- [x] 불변식 위반 없음

## 구현 플랜
1. Phase 1 → Phase 2 → Phase 3 → Phase 4 순차
2. Zipline-reloaded 가 Python 3.14 비호환이면 VectorBT 또는 자체 이벤트 엔진으로 전환 (리스크)

## 리스크
- Zipline-reloaded 가 Python 3.14 에서 안 돌 수 있음 → fallback: VectorBT 또는 lightweight 자체 엔진
- Binance API rate limit → 데이터 수집 시 sleep/retry 필요
- RSI divergence 구현 시 룩어헤드 바이어스 주의 (`13-feature-alpha-catalog` 참조)

## 선행 조건
- 없음 (첫 이슈)

## 개발 체크리스트
- [x] 테스트 코드 포함
- [x] 관련 .ai.md 갱신
- [x] 불변식 위반 없음
## 작업 내역

### 2026-04-19

**현황**: 0/6 완료
**완료된 항목**:
- (없음)
**미완료 항목**:
- fetch_candles.py 데이터 수집
- 커스텀 엔진 데이터 로드 (AC2 변경: Zipline → 커스텀 엔진)
- run_backtest.py 백테스트 실행
- 테스트 코드 (31+ tests)
- momo-btc-v2.md 프론트매터 메트릭 반영
- 불변식 위반 없음
**변경 파일**: 2개 (00_issue.md, 01_plan.md — ralplan 합의 기반 구현 계획 작성 완료)

### 2026-04-20

**현황**: 6/6 완료
**완료된 항목**:
- fetch_candles.py 데이터 수집 (Binance REST → Parquet, 페이지네이션, 429 retry)
- 커스텀 이벤트 엔진 (protocol.py, engine.py, metrics.py, bundle.py)
- RSI divergence 시그널 (Wilder smoothing, rolling min/max, shift(1) lag-1)
- momo-btc-v2 전략 (long-only MVP, Strategy protocol 준수)
- run_backtest.py CLI (메트릭 출력, 프론트매터 업데이트, doc_agent 초안)
- 불변식 위반 없음 (79 노트 검증 통과)
**미완료 항목**:
- (없음)
**변경 파일**: 27개 (22 신규 + 5 수정)
**테스트**: 33 passed, 1 skipped
