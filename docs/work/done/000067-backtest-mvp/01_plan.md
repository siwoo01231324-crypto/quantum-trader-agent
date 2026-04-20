# 01_plan.md — #67 마켓 데이터 수집 + 백테스트 + momo-btc-v2 실행

> 작성: 2026-04-17 | ralplan 합의 완료 (Planner→Architect→Critic, 1회 iteration)

---

## AC 체크리스트
- [ ] `python scripts/fetch_candles.py --symbol BTCUSDT --interval 15m` 로 1년 데이터 Parquet 저장
- [ ] ~~`zipline ingest -b qta-binance` 성공~~ → `load_ohlcv_from_parquet()` 로 커스텀 엔진에 데이터 로드 (Python 3.14 호환성 이유로 변경, AC2 Note 참조)
- [ ] `python scripts/run_backtest.py --strategy momo-btc-v2` 로 백테스트 실행, Sharpe/MDD 출력
- [ ] 테스트 코드 포함 (데이터 수집 mock + 전략 로직 + 백테스트 러너) — 31+ tests
- [ ] 결과 메트릭이 `momo-btc-v2.md` 프론트매터에 반영
- [ ] 불변식 위반 없음

## 개발 체크리스트
- [ ] 테스트 코드 포함
- [ ] 관련 .ai.md 갱신
- [ ] 불변식 위반 없음

## 선행 조건
- 없음 (첫 이슈)

## 리스크
- Zipline-reloaded Python 3.14 비호환 → **커스텀 경량 엔진 (Option A) 선택** (ADR 참조)
- Binance API rate limit → 0.5s sleep + 429 retry
- RSI divergence 룩어헤드 바이어스 → `shift(1)` 강제 + 전용 테스트
- 커스텀 엔진 포트폴리오 회계 버그 → TDD + 수작업 검증 테스트

---

## 구현 계획

### RALPLAN-DR Summary

**Principles (5)**
1. **Data flows end-to-end before optimization** — 한 전략이 실제 Sharpe/MDD 결과를 내는 것이 목표
2. **TDD always** — 매 Phase에서 테스트 먼저 작성; 외부 API는 mock
3. **Repo conventions sacred** — 새 디렉토리마다 `.ai.md`, `.parquet`/`.csv` 커밋 금지, 프론트매터 스키마 준수
4. **No lookahead bias** — RSI divergence 시그널은 `shift(1)` / lag-1 필수 (per `13-feature-alpha-catalog`)
5. **Fallback-first for Python 3.14** — 별도 venv 없이 현재 런타임에서 작동하는 엔진 선택

**Decision Drivers (Top 3)**
1. Python 3.14 호환성 — Zipline-reloaded는 3.10-3.13만 지원, bcolz-zipline C 확장 빌드 실패
2. Time-to-MVP — 첫 end-to-end 데이터 흐름, 속도 우선
3. 미래 확장성 — Zipline/NautilusTrader 마이그레이션 차단 금지

**Engine Decision: Option A — Lightweight Custom Event Engine (pandas + numpy)**
- Python 3.14 네이티브, venv 불필요
- 크립토 24/7 → exchange calendar 불필요
- `Strategy` protocol로 미래 엔진 교체 가능
- 대안 평가: Option B (Zipline 3.11 venv) — 24/7 crypto calendar 미지원 + dual-Python CI 부담으로 기각. Option C (VectorBT) — numba Python 3.14 미지원으로 무효화.

**ADR**: `docs/background/11-backtest-engine-selection.md`의 Zipline 선택을 Python 3.14 런타임 제약 + 크립토 24/7 요구사항으로 오버라이드. `Strategy` protocol interface로 향후 엔진 교체 보장.

---

### Guardrails

**Must Have**
- TDD: 매 Phase에서 테스트 파일을 구현 파일보다 먼저 작성
- 새 디렉토리마다 `.ai.md`
- RSI divergence는 `shift(1)` (lag-1) 사용 — 룩어헤드 바이어스 방지
- Parquet 파일 gitignore; `lake/` 디렉토리도 `.gitignore`에 추가
- `Strategy` protocol은 엔진 비의존적 (향후 Zipline 교체 가능)
- Sharpe ratio는 **daily return 기반**, `sqrt(365)` (크립토 24/7)로 연환산
- MDD halt: 누적 drawdown 5% 초과 시 거래 중단
- MVP는 **long-only**: bullish divergence → 롱, bearish divergence → 현금 전환. 숏 포지션은 향후 이터레이션
- `partition_path`를 `src/data_lake/__init__.py`에서 export (Phase 0)
- 메트릭 dict 키를 `doc_agent` 기대값에 맞춤 (`trades` not `trade_count`)

**Must NOT Have**
- `.parquet` / `.csv` 커밋 금지
- 전략 로직에 LLM 호출 금지 (레포 불변식 #6)
- 자동 커밋 금지 (사용자 확인 필수)
- 하드코딩 API 키 금지 (env vars 사용)
- 시그널 계산에 룩어헤드 바이어스 금지
- Zipline/VectorBT 의존성 금지 (Python 3.14 비호환)
- sub-daily return으로 Sharpe 계산 금지 (daily resample 필수)

---

### Task Flow (5 Phases, Sequential)

```
Phase 0: Project Setup (pyproject.toml, deps, directories)
    ↓
Phase 1: Data Fetching (Binance REST → Parquet)
    ↓
Phase 2: Backtest Engine (event loop, Strategy protocol, metrics)
    ↓
Phase 3: Strategy Implementation (RSI divergence signal, momo-btc-v2, MDD halt)
    ↓
Phase 4: CLI Runner + Integration (run_backtest.py, frontmatter update, doc_agent draft)
```

---

### Phase 0: Project Setup

**목표**: `pyproject.toml`, 의존성 설치, 디렉토리 스켈레톤 + `.ai.md`

**생성 파일**:

`pyproject.toml` (project root):
```toml
[project]
name = "quantum-trader-agent"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "pandas>=2.1", "numpy>=1.26", "pyarrow>=14.0",
    "pyyaml>=6.0", "pydantic>=2.0", "requests>=2.31", "python-dotenv>=1.0",
]
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov", "responses>=0.25"]
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src", "."]
```

**새 디렉토리 + `.ai.md`**:
- `src/backtest/` — 경량 이벤트 기반 백테스트 엔진 (engine.py, protocol.py, metrics.py, bundle.py)
- `src/backtest/strategies/` — Strategy protocol 구현체 (momo_btc_v2.py)
- `src/signals/` — 무상태 시그널 계산 함수 (rsi.py). 모든 시그널 lag-1 필수.

**추가 작업**: `partition_path`를 `src/data_lake/__init__.py`에서 export, `lake/`를 `.gitignore`에 추가

**Phase 0 AC**: `pip install -e ".[dev]"` 성공, 새 디렉토리 `.ai.md` 존재, `pytest --collect-only` 정상

---

### Phase 1: Data Fetching (Binance REST → Parquet)

**목표**: `python scripts/fetch_candles.py --symbol BTCUSDT --interval 15m` → 1년 데이터 Parquet 저장

**테스트 먼저** (`tests/test_fetch_candles.py` — 6 tests):
- `test_binance_response_parsed_to_ohlcv_schema()` — Mock 응답 → OHLCV_SCHEMA 컬럼 검증
- `test_fetcher_paginates_with_limit_1000()` — 1000개 제한 페이지네이션
- `test_parquet_saved_to_correct_partition_path()` — `partition_path()` 규약 준수
- `test_parquet_schema_matches_ohlcv()` — 12 컬럼 전체 존재
- `test_rate_limit_retry_on_429()` — 429 → exponential backoff 재시도
- `test_cli_args_parsed_correctly()` — CLI 인수 파싱

**구현 파일**:

`src/data_lake/fetcher.py`:
```python
def fetch_binance_klines(symbol: str, interval: str, start: str, end: str) -> pd.DataFrame:
    """Binance REST API에서 캔들 조회. 1000개씩 페이지네이션, 0.5s sleep, 429 retry."""

def save_ohlcv_parquet(df: pd.DataFrame, output_dir: Path, symbol: str, freq: str) -> list[Path]:
    """OHLCV DataFrame → Parquet. year/month 기준 파티션, OHLCV_SCHEMA 검증."""
```

Binance API 매핑: `ts=openTime(UTC), vwap=quoteVol/volume, trade_count=trades, source="binance", ingested_at=now(UTC)`

`scripts/fetch_candles.py`: CLI — `--symbol BTCUSDT --interval 15m --start 2025-04-01 --end 2026-04-01 --output-dir lake/`

---

### Phase 2: Backtest Engine

**목표**: Strategy protocol + 이벤트 기반 엔진 + 메트릭 계산

**테스트 먼저** (`tests/test_backtest_engine.py` — 8 tests):
- `test_engine_iterates_all_bars()`, `test_engine_tracks_positions()`, `test_engine_computes_equity_curve()`
- `test_engine_computes_sharpe()` — 일정 1% 일일 수익률 → Sharpe ≈ 15.87
- `test_engine_computes_max_drawdown()` — Equity [100, 110, 95, 105] → MDD = 13.6%
- `test_engine_halt_stops_trading()`, `test_engine_no_position_when_halted()`, `test_strategy_protocol_enforced()`

**구현 파일**:

`src/backtest/protocol.py`:
```python
@dataclass
class Bar:
    ts: pd.Timestamp; open: float; high: float; low: float; close: float; volume: float

@dataclass
class Signal:
    action: str  # "buy" | "sell" | "hold"
    size: float  # 0.0-1.0 equity 비율
    reason: str

@runtime_checkable
class Strategy(Protocol):
    def on_init(self, context: dict) -> None: ...
    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal: ...
```

`src/backtest/engine.py`:
```python
@dataclass
class BacktestConfig:
    initial_cash: float = 100_000.0
    commission_pct: float = 0.001    # Binance taker 0.1%
    slippage_pct: float = 0.0005     # 0.05%
    max_drawdown_halt_pct: float = 0.05

def run_backtest(ohlcv: pd.DataFrame, strategy: Strategy, config: BacktestConfig) -> BacktestResult:
    """Bar-by-bar: mark-to-market → MDD halt 체크 → on_bar() → 주문 실행 → equity 기록"""
```

`src/backtest/metrics.py`:
```python
def compute_sharpe(equity_curve: pd.Series, periods_per_year: int = 365) -> float:
    """Daily return 기반 연환산 Sharpe. equity curve를 daily로 resample 후 log return 계산."""

def compute_max_drawdown(equity_curve: pd.Series) -> float:
def compute_total_return(equity_curve: pd.Series) -> float:
def compute_win_rate(trades: list[dict]) -> float:
def compute_all_metrics(equity_curve, trades) -> dict:
    """Returns: {sharpe, mdd, total_return, trades(count), win_rate} — doc_agent 키 호환"""
```

`src/backtest/bundle.py`:
```python
def load_ohlcv_from_parquet(data_dir, symbol, freq, start=None, end=None) -> pd.DataFrame:
    """파티션 Parquet 읽기 → concat → ts 정렬 → DatetimeIndex"""
```

---

### Phase 3: Strategy Implementation

**목표**: RSI divergence 시그널 + momo-btc-v2 전략

**테스트 먼저**:

`tests/test_signals.py` (7 tests):
- `test_rsi_calculation_matches_manual()` — Wilder smoothing 수작업 검증
- `test_rsi_with_all_gains_is_100()`, `test_rsi_with_all_losses_is_0()`
- `test_bullish_divergence_detected()`, `test_bearish_divergence_detected()`, `test_no_divergence_when_aligned()`
- `test_signal_uses_lag1()` — bar N의 divergence는 [N-14, N-1] 범위만 사용

`tests/test_momo_btc_v2.py` (5 tests):
- `test_strategy_conforms_to_protocol()`, `test_buy_on_bullish_divergence()`, `test_sell_on_bearish_divergence()`
- `test_hold_when_no_divergence()`, `test_strategy_with_engine_produces_results()`

**구현 파일**:

`src/signals/rsi.py`:
```python
def compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder smoothing RSI. alpha = 1/period. 처음 period개 = NaN."""

def detect_divergence(close: pd.Series, rsi: pd.Series, lookback: int = 14) -> pd.Series:
    """Rolling min/max 알고리즘:
    1. close.shift(1), rsi.shift(1) 로 lag-1 적용 (룩어헤드 방지)
    2. rolling(lookback).min/max 로 현재 윈도우 극값 계산
    3. .shift(lookback) 로 이전 윈도우 극값과 비교
    Bullish: price_low_curr < price_low_prev AND rsi_low_curr > rsi_low_prev
    Bearish: price_high_curr > price_high_prev AND rsi_high_curr < rsi_high_prev
    Returns: 'bullish' | 'bearish' | None"""
```

`src/backtest/strategies/momo_btc_v2.py`:
```python
class MomoBtcV2:
    """BTC 15m Momentum v2 (MVP: long-only).
    Bullish divergence → long (100% equity). Bearish divergence → exit to cash.
    숏 포지션은 향후 이터레이션으로 연기."""
    RSI_PERIOD = 14; LOOKBACK = 14
    def on_init(self, context): ...
    def on_bar(self, bar, history, context) -> Signal: ...
```

---

### Phase 4: CLI Runner + Integration

**목표**: `run_backtest.py` CLI + 프론트매터 업데이트 + doc_agent 초안

**테스트 먼저** (`tests/test_run_backtest.py` — 5 tests):
- `test_cli_loads_strategy_by_name()`, `test_cli_outputs_metrics_to_stdout()`
- `test_frontmatter_update_writes_sharpe_bt()`, `test_doc_agent_draft_generated()`
- `test_full_pipeline_synthetic_data()` — temp Parquet → 백테스트 → 전체 출력 검증

**구현 파일**:

`scripts/run_backtest.py`:
```python
STRATEGY_REGISTRY = {"momo-btc-v2": MomoBtcV2}
def main():
    # 1. Parse args
    # 2. Load OHLCV (데이터 없으면: "No data found. Run: python scripts/fetch_candles.py first")
    # 3. 전략 인스턴스화
    # 4. run_backtest() 실행
    # 5. 메트릭 stdout 출력
    # 6. 프론트매터 sharpe_bt 업데이트
    # 7. doc_agent 키 매핑: bt_result_json = {strategy, period=[start,end], metrics}
    # 8. generate_backtest_draft() 호출 → .draft.md 생성
```

`src/backtest/frontmatter.py`:
```python
def update_strategy_frontmatter(strategy_id: str, metrics: dict, docs_dir: Path) -> Path:
    """docs/specs/strategies/{strategy_id}.md 의 sharpe_bt 필드 업데이트"""
```

**Phase 4 후 추가 업데이트**: `AGENTS.md` (src/backtest/, src/signals/ 추가), `tests/.ai.md`

---

### Complete File Manifest

**신규 (22)**:

| 파일 | Phase | 역할 |
|------|-------|------|
| `pyproject.toml` | 0 | 프로젝트 메타데이터 + 의존성 |
| `src/backtest/__init__.py` | 0 | 패키지 init |
| `src/backtest/.ai.md` | 0 | 디렉토리 목적 문서 |
| `src/backtest/protocol.py` | 2 | Strategy protocol, Bar, Signal |
| `src/backtest/engine.py` | 2 | 이벤트 기반 백테스트 루프 |
| `src/backtest/metrics.py` | 2 | Sharpe/MDD/수익률/승률 계산 |
| `src/backtest/bundle.py` | 2 | Parquet 데이터 로더 |
| `src/backtest/frontmatter.py` | 4 | 전략 프론트매터 업데이터 |
| `src/backtest/strategies/__init__.py` | 3 | 패키지 init |
| `src/backtest/strategies/.ai.md` | 3 | 디렉토리 목적 문서 |
| `src/backtest/strategies/momo_btc_v2.py` | 3 | BTC 모멘텀 전략 |
| `src/signals/__init__.py` | 3 | 패키지 init |
| `src/signals/.ai.md` | 3 | 디렉토리 목적 문서 |
| `src/signals/rsi.py` | 3 | RSI 계산 + divergence 감지 |
| `src/data_lake/fetcher.py` | 1 | Binance REST fetcher + Parquet writer |
| `scripts/fetch_candles.py` | 1 | 데이터 수집 CLI |
| `scripts/run_backtest.py` | 4 | 백테스트 러너 CLI |
| `tests/test_fetch_candles.py` | 1 | 데이터 수집 테스트 |
| `tests/test_backtest_engine.py` | 2 | 엔진 단위 테스트 |
| `tests/test_signals.py` | 3 | RSI + divergence 테스트 |
| `tests/test_momo_btc_v2.py` | 3 | 전략 통합 테스트 |
| `tests/test_run_backtest.py` | 4 | 러너 통합 테스트 |

**수정 (3)**:

| 파일 | Phase | 변경 내용 |
|------|-------|----------|
| `src/data_lake/.ai.md` | 1 | fetcher.py 추가 |
| `scripts/.ai.md` | 1,4 | fetch_candles.py, run_backtest.py 추가 |
| `docs/specs/strategies/momo-btc-v2.md` | 4 | sharpe_bt 프론트매터 업데이트 |

**후속 업데이트**: `AGENTS.md` (구조 반영), `tests/.ai.md` (테스트 파일 반영)

---

### AC2 변경 안내

원본 AC2: `zipline ingest -b qta-binance` 성공
변경: Python 3.14 비호환으로 커스텀 엔진 선택. → `load_ohlcv_from_parquet(lake/, "BTCUSDT", "15m")` 이 ~35,000행 DataFrame을 성공적으로 반환하면 AC2 충족. OHLCV Parquet 포맷은 동일하므로 향후 Zipline bundle adapter 추가 가능.
**⚠️ 이 변경은 사용자 확인 필요.**

---

### Edge Cases & Gotchas

1. **Binance API 페이지네이션**: 1000개/요청 × 36회 = ~35,040 bars. `startTime` 올바르게 전진
2. **Rate limit**: 1200 weight/min 중 180 사용 (36×5). 0.5s sleep 안전 마진
3. **RSI warmup**: 처음 14 bars NaN → divergence는 bar 28+ 부터만 감지
4. **Lookahead bias**: divergence at bar N = bars [N-lookback, N-1] only. `shift(1)` 필수
5. **Parquet 파티셔닝**: ~12 monthly 파티션, 각 ~3000 rows
6. **MDD halt**: (a) 포지션 청산 (b) on_bar() 호출 중단 (c) halt 기록
7. **Crypto 24/7**: Sharpe는 daily return resample → `sqrt(365)`. MDD/equity는 15m 해상도 유지
8. **수수료/슬리피지**: Binance taker 0.1% + 슬리피지 0.05% fixed
9. **타임스탬프**: Binance ms UTC → `pd.Timestamp` UTC. Parquet는 `datetime[us, UTC]`
10. **빈 divergence 기간**: 장기간 시그널 없음 정상. 현재 포지션 유지 (hold = 포지션 변경 없음)
