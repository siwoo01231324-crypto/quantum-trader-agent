# [#106] Binance Futures historical data loader — 작업 계획 (초안)

> 작성: 2026-04-26
> 본 문서는 `/start-issue` 가 생성한 **AC 체크리스트 초안** 이다.
> 구현 시작 전 `/plan 106` 으로 구체적 구현 계획을 작성해야 한다.

## 완료 기준 (Acceptance Criteria)

- [ ] `binance_futures_loader.py` 구현 + 캐싱
- [ ] BTCUSDT/ETHUSDT/SOLUSDT 1m bar 다운로드 검증 (rate limit 준수)
- [ ] `scripts/shadow_report.py --compare-backtest` 가 본 loader 산출물로 동작
- [ ] 단위 테스트 + 통합 테스트 통과
- [ ] `src/data/.ai.md` 갱신

## 의존성

- 선행: 없음 (독립 이슈)
- 후속 영향: #80 의 Phase E Shadow 운영 리포트가 본 이슈 머지 후 활성화 가능

## 범위 (이슈 본문)

- `src/data/binance_futures_loader.py` (또는 동등 모듈)
  - Binance Futures USDT-M historical OHLCV (1m bar) fetcher
  - `data_lake/` 캐시 정책 (#79 와 동일 패턴)
  - 다중 심볼 (BTCUSDT/ETHUSDT/SOLUSDT 최소 3종)
  - 동일 기간 Shadow 운영 데이터와 비교 가능한 인터페이스
- 백테스트 엔진 (`src/backtest/`) 와 연동 — Phase 1 의 0-슬립/taker 0.05% 정책 그대로 사용
- 단위 테스트 + 통합 테스트 (mock REST + 실측 1일 분 fixture)

## 참고

- #80 plan: `docs/work/active/000080-paper-broker/01_plan.md` Phase E §E2
- [[29-paper-to-live-protocol]] §7.1 동일 data-lake-schema 스냅샷 요구
- #79 (전략 카탈로그 확장) 의 `src/brokers/kis/price_client.py` — 캐싱 패턴 참고
- Binance Futures REST API: https://binance-docs.github.io/apidocs/futures/en/

## 다음 단계

1. `/plan 106` 으로 구체적 구현 계획 (`01_plan.md`) 확장
2. `src/data/`, `src/brokers/binance/` 디렉토리의 `.ai.md` 사전 검토
3. 기존 #79 의 KIS price_client 캐싱 패턴 정독
4. 테스트 우선 — Red → Green → Refactor

---

## 구현 계획

> 작성: 2026-04-26 (`/plan 106`)

### 0. 사전 실측 (이미 확정)

| 가정 | 실측 결과 |
|------|----------|
| `src/data/` 신규 디렉토리 필요 | ❌ — `src/data_lake/fetcher.py` 가 이미 Binance Spot klines + KIS daily/intraday + parquet 저장 인프라를 보유. **이슈 본문 "또는 동등 모듈" 단서 활용해 `src/data_lake/` 확장**. 신규 `src/data/` 디렉토리는 만들지 않는다. |
| Binance 데이터 = Spot | ❌ — `fetch_binance_klines()` 은 `https://api.binance.com/api/v3/klines` (Spot). #106 은 **Futures USDT-M** (`https://fapi.binance.com/fapi/v1/klines`). 별도 함수 신설 필수 (Spot 호환성 보존). |
| `data_lake/` 디렉토리 존재 | ❌ — 미존재. 백테스트 데이터는 `lake/` 디렉토리에 적재 (`scripts/run_backtest.py --data-dir lake/`, `scripts/fetch_candles.py --output-dir lake/`). 이슈 본문 `data_lake/` 표현은 개념적 "데이터 레이크"이며 실제 경로는 `lake/`. |
| #80 / #79 | ✅ 둘 다 CLOSED — 본 이슈 머지 후 #80 Phase E `compare-backtest` 활성화 가능. |
| `scripts/shadow_report.py --compare-backtest` 의 비교 4조건 | `data_source="binance_futures_usdtm"`, `slippage_model="zero_slip"`, `taker_fee_bps=5.0`, `sizing_method="resolve_size_v1"` 가 **하드코딩**됨 (line 361-366). Shadow 측·Backtest 측 WAL 모두 동일 데이터에서 재생성되면 자동 통과. |
| 1m bar 시계열 데이터 | 현재 `scripts/fetch_candles.py` default 는 `--interval 15m`. 1m 다운로드는 가능하나 페이지네이션 호출 수가 15배 증가 (Futures fapi weight 5/req → klines limit 1000 → 1년분 1m ≈ 525회 호출 ≈ 4분 소요). |

### 1. AC ↔ 구현 단계 매핑

| AC | 구현 단계 | 변경/신규 파일 | 검증 |
|----|----------|---------------|------|
| `binance_futures_loader.py` 구현 + 캐싱 | S1 | `src/data_lake/fetcher.py` (수정) | `fetch_binance_futures_klines()` 신규 함수 export, OHLCV_SCHEMA 준수, parquet 저장은 기존 `save_ohlcv_parquet` 재사용 |
| BTCUSDT/ETHUSDT/SOLUSDT 1m bar 다운로드 (rate limit 준수) | S2 | `scripts/fetch_futures_candles.py` (신규) | mock REST 단위테스트 + 다중심볼 1일분 통합테스트 |
| `scripts/shadow_report.py --compare-backtest` 가 본 loader 산출물로 동작 | S3 | (코드 변경 없음 — 검증만) | 통합테스트: Futures lake → backtest WAL → shadow WAL → compare 4조건 일치 smoke |
| 단위테스트 + 통합테스트 통과 | S4 | `tests/data_lake/test_fetch_binance_futures_klines.py`, `tests/data_lake/test_binance_futures_integration.py` (신규) | pytest 그린 |
| `src/data/.ai.md` 갱신 | S5 | `src/data_lake/.ai.md` (수정 — 이슈 본문 `src/data/` 는 의미상 `src/data_lake/`) | 신규 함수·source 라벨 명시 |

### 2. 구현 순서 (TDD: Red → Green → Refactor)

#### S0. 폴더 구조·헬퍼 분리 사전 정리 (Refactor-first)
1. `src/data_lake/fetcher.py` 의 Spot 페이지네이션 루프(`fetch_binance_klines` 본체)를 **공유 헬퍼 `_paginate_binance_klines(base_url, symbol, interval, start, end, source_label)`** 로 추출. Spot 함수는 헬퍼를 호출하도록 단순화.
2. 기존 `tests/data_lake/test_fetch_candles.py` 그린 유지 확인 (회귀 가드).

#### S1. Futures klines 함수 추가 (Red → Green)
1. **Red**: `tests/data_lake/test_fetch_binance_futures_klines.py` 작성
   - 페이지네이션 1회/N회 케이스
   - 429 retry → 200 회복
   - `source="binance_futures"` 필드 검증 (Spot 와 구분)
   - 빈 응답 → 빈 DataFrame (OHLCV_SCHEMA 컬럼 보존)
   - `BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"` 상수
2. **Green**: `src/data_lake/fetcher.py` 에 `fetch_binance_futures_klines(symbol, interval, start, end) -> pd.DataFrame` 추가 — S0 헬퍼 호출 + `source_label="binance_futures"`.
3. **Refactor**: 공통 timeout/retry 매개변수만 노출, OHLCV_SCHEMA validate 100%.

#### S2. CLI 다중 심볼 fetcher
1. **Red**: `tests/scripts/test_fetch_futures_candles_cli.py` (또는 함수 단위 테스트로 충분하면 생략)
   - `--symbols BTCUSDT,ETHUSDT,SOLUSDT --interval 1m --start ... --end ... --output-dir lake_futures/`
   - 각 심볼당 1회 fetch + 1회 save 호출
2. **Green**: `scripts/fetch_futures_candles.py` 작성 — `argparse` + `fetch_binance_futures_klines` 루프 + `save_ohlcv_parquet`. 심볼 사이 0.5s sleep 유지.
3. **Refactor**: `fetch_candles.py` 와의 중복 최소화 (공유 main 헬퍼는 후속 이슈로 미룬다 — 본 이슈 범위 안 함).

#### S3. shadow_report 호환 통합테스트
1. **Red**: `tests/data_lake/test_binance_futures_integration.py`
   - 1일분 BTCUSDT 1m 모킹 → `fetch_binance_futures_klines` → `save_ohlcv_parquet` → `load_ohlcv_from_parquet` 라운드트립 동등성 검증
   - 별도 smoke: `scripts/shadow_report.py` 의 `compare_sharpe()` 함수에 동일 returns 시계열 양쪽 주입 → `passed=True` (조건 4종 hard-coded 일치). **CLI 직접 호출은 하지 않는다 (본 이슈 범위는 loader; #80 통합 검증은 후속 이슈).**
2. **Green**: 위 테스트가 green 이면 AC3 자동 충족 (loader 산출물로 비교 가능 = 동일 OHLCV_SCHEMA·`source` 가 lake 에 들어감).

#### S4. `.ai.md` 갱신
1. `src/data_lake/.ai.md` 의 `구조` 섹션에 `fetch_binance_futures_klines(symbol, interval, start, end) -> pd.DataFrame — Binance Futures USDT-M (fapi.binance.com), source="binance_futures"` 추가.
2. `scripts/.ai.md` (있으면) 에 `fetch_futures_candles.py` 라인 추가 — 없으면 스킵.

#### S5. 회귀·문서 마감
1. `pytest tests/data_lake/ -q` 그린 확인.
2. `pytest tests/ -q -k "not slow and not network"` 전체 그린 (Spot fetcher BC 회귀 가드).
3. `python scripts/check_invariants.py --strict` 통과.
4. 본 `01_plan.md` 의 "다음 단계" 체크 + `00_issue.md` 작업 내역 업데이트.

### 3. 외부 인터페이스 스펙

```python
# src/data_lake/fetcher.py — 신규 export
BINANCE_FUTURES_KLINES_URL = "https://fapi.binance.com/fapi/v1/klines"

def fetch_binance_futures_klines(
    symbol: str,        # e.g. "BTCUSDT"
    interval: str,      # e.g. "1m"
    start: str,         # ISO "YYYY-MM-DD"
    end: str,           # ISO "YYYY-MM-DD"
) -> pd.DataFrame:
    """OHLCV_SCHEMA DataFrame. source='binance_futures'. Empty df with schema cols if no data."""
```

```bash
# scripts/fetch_futures_candles.py — 신규 CLI
python scripts/fetch_futures_candles.py \
    --symbols BTCUSDT,ETHUSDT,SOLUSDT \
    --interval 1m \
    --start 2026-04-01 --end 2026-04-26 \
    --output-dir lake/
```

### 4. Guardrails

#### Must Have
- **퍼블릭 fapi 엔드포인트만** — `https://fapi.binance.com/fapi/v1/klines` (HMAC sign 불요, API key 불요).
- **Spot fetcher 회귀 보호** — `fetch_binance_klines` 시그니처·동작 100% 보존, 기존 테스트 그린 유지.
- **OHLCV_SCHEMA 컬럼 100% 일치** — `source="binance_futures"` 만 차이 (Spot 은 `"binance"`).
- **Rate limit 준수** — 페이지네이션 sleep 0.5s, 429 지수백오프 (1s → 2s → 4s, 최대 3회) — 기존 헬퍼 재사용.
- **TZ 일관성** — 모든 `ts` 컬럼은 UTC `pd.Timestamp` (기존 패턴 동일).
- **다중 심볼 검증** — BTCUSDT·ETHUSDT·SOLUSDT 3종 모두 단위테스트에 등장.
- **1m interval 명시 검증** — AC 가 "1m bar" 명시 → 단위테스트 fixture 의 `interval="1m"` 한 케이스 이상.
- **공유 헬퍼 추출 (S0)** — Spot/Futures 페이지네이션·429 로직 중복 금지.

#### Must NOT Have
- **testnet 사용 금지** — `https://testnet.binancefuture.com/fapi/v1/klines` 는 historical data 가 비어 있어 부적합. production fapi 의 public klines (인증 불요) 사용.
- **HMAC sign 추가 금지** — klines 는 public, sign 추가 시 의미 없는 의존만 늘어남.
- **신규 `src/data/` 디렉토리 생성 금지** — 이슈 본문 "또는 동등 모듈" 활용, 기존 `src/data_lake/` 확장.
- **CI 에서 라이브 1년치 다운로드 금지** — fixture/responses-mock 사용. 라이브 검증은 로컬 1일분 BTCUSDT 1m smoke 로만.
- **`scripts/run_backtest.py` 동작 변경 금지** — 본 이슈 범위는 loader. `--source` 분기 등은 후속 이슈로 분리.
- **`shadow_report.py` 코드 수정 금지** — `compare_sharpe` 4조건은 hard-coded 일치, loader 산출물이 lake 에 적재되면 자동 통과.
- **`fetch_candles.py` BC 깨기 금지** — Spot CLI 는 그대로. Futures 는 별도 `scripts/fetch_futures_candles.py`.
- **#79 의 KIS `price_client.py` 패턴 직접 import 금지** — KIS 와 Binance 는 인증·페이지네이션 토큰 구조가 달라 비교는 참고만, 코드 재사용은 안 한다.

### 5. 엣지 케이스

| 케이스 | 처리 |
|--------|------|
| `start > end` | 현재 Spot 함수와 동일하게 빈 DataFrame 반환 (예외 raise 안 함) |
| 거래정지 심볼 | klines 는 단순 빈 응답 → 빈 df (`columns=OHLCV_SCHEMA.keys()`) |
| 1m 페이지네이션 시 1000 초과 | `last_open_ms + 1` 진행. 마지막 페이지 `len(raw) < LIMIT` 종료. |
| 429 4회 연속 | 4번째 호출 `raise_for_status()` → `HTTPError` 전파 (기존 동일) |
| 네트워크 타임아웃 (>30s) | `requests.Timeout` 전파 (기존 동일) — 본 이슈에서 graceful retry 추가 안 함 |
| Futures 신규 상장 심볼 (start 가 상장일 이전) | Binance 는 상장일부터 데이터 반환 → 빈 부분 자동 skip |
| `--output-dir` 미존재 | `save_ohlcv_parquet` 가 `mkdir(parents=True, exist_ok=True)` — 기존 동작 |

### 6. 검증 체크리스트 (S5 마감 시)

- [ ] `pytest tests/data_lake/test_fetch_binance_futures_klines.py -q` 그린
- [ ] `pytest tests/data_lake/test_binance_futures_integration.py -q` 그린
- [ ] `pytest tests/data_lake/test_fetch_candles.py -q` 그린 (Spot 회귀 가드)
- [ ] `pytest tests/ -q -k "not slow and not network"` 전체 그린
- [ ] `python scripts/check_invariants.py --strict` 통과
- [ ] 로컬 smoke: `python scripts/fetch_futures_candles.py --symbols BTCUSDT --interval 1m --start <어제> --end <오늘> --output-dir /tmp/lake_smoke/` → parquet 파일 1개 이상 생성, `load_ohlcv_from_parquet` 로 읽기 성공
- [ ] `src/data_lake/.ai.md` 신규 함수 라인 추가 확인
- [ ] `00_issue.md` 작업 내역 섹션 업데이트

