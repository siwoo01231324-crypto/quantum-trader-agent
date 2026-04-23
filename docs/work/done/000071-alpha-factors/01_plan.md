# [#71] 알파 팩터 파이프라인 — 구현 계획 (확정)

> 작성: 2026-04-23

---

## RALPLAN-DR Summary

### Principles (repo-specific)

1. **Causality required, lag-1 enforced at signal boundary** — indicator functions (RSI, SMA, ATR, ...) must be causal (no forward leakage) but do **not** need to call `shift(1)` internally. Signal functions (outputs that trigger trades: `detect_divergence`, future `sma_cross`, `bollinger_breakout`) **must** apply `shift(1)` to their inputs before decision logic. This matches the current `src/signals/rsi.py` pattern (Wilder RSI has no shift; `detect_divergence` does).
2. **Signal frontmatter validated by CI** — `scripts/check_invariants.py --strict`가 `type`, `id`, `name`, `inputs`, `lookback` 필수 필드 검증
3. **No runtime deps beyond pyproject core** — Python 3.14 호환 목표, TA-Lib C 빌드 금지, pandas-ta는 dev optional only
4. **Pure functions** — `src/signals/.ai.md` 규칙: 외부 상태 의존 없음, 동일 입력 → 동일 출력
5. **FACTOR_SCHEMA conformance** — `src/data_lake/schema.py`의 `{symbol, ts, factor_set, factor_name, value}` 장형식 스키마 준수

### Decision Drivers (top 3)

1. **Python 3.14 호환**: Zipline/TA-Lib C 의존성 배제, pandas+numpy 벡터화 직접구현
2. **MomoBtcV2 import path 보존**: `from signals.rsi import compute_rsi, detect_divergence` 경로 불변, `Strategy` protocol 하위호환
3. **CI invariants 통과**: 새 signal note의 frontmatter, `[[wikilink]]` 대상 존재, `trading.ttl` 파싱 가능

### Viable Options

**Registry Interface**

| | Option A: `@register` decorator + dict | Option B: Entry-point / pluggy plugin |
|---|---|---|
| 구현 | `@register("rsi")` decorator가 `FACTOR_REGISTRY` dict에 자동 등록 | `setuptools` entry_points 또는 pluggy hook |
| Pros | 단순, 의존성 없음, import 시 즉시 등록, IDE 자동완성 | 서드파티 확장 가능, 런타임 discovery |
| Cons | 모듈이 import되어야 등록됨 (explicit import 필요) | 과잉 설계, 설치 필요, 디버깅 어려움 |
| 적합성 | 6개 내부 팩터에 최적 | 플러그인 생태계 불필요 시 과잉 |

**선택: Option A** — 6개 내부 팩터 규모에 decorator + explicit import가 단순하고 충분. Entry-point는 외부 플러그인 생태계가 없는 현 단계에서 과잉 설계로 무효화.

**Engine Integration Point**

| | Option A: Engine precompute via registry | Option B: Strategy 내부 직접 호출 (현행) |
|---|---|---|
| 구현 | `run_backtest`가 `strategy.required_factors`를 읽어 `context["factors"]`에 주입 | 각 strategy `on_bar` 내에서 직접 `compute_rsi()` 호출 |
| Pros | 캐싱/최적화 단일 지점, 팩터 재사용, 선언적 의존성 | 변경 없음, 단순 |
| Cons | engine.py 수정 필요, `required_factors=[]` default로 하위호환 | 팩터 중복 계산, 레지스트리 무용화, 확장 불가 |
| 적합성 | 팩터 파이프라인 목적에 부합 | 현행 MomoBtcV2에는 충분하나 확장성 없음 |

**선택: Option A** — AC "백테스트 엔진에서 팩터 라이브러리 호출"을 충족하려면 엔진 수준 통합 필수. `required_factors` default `[]`로 MomoBtcV2 하위호환 보장. Option B는 AC 미충족으로 무효화.

---

## 완료 기준

- [ ] 5+ 팩터 구현 + 단위 테스트
- [ ] 룩어헤드 검증 테스트
- [ ] 백테스트 엔진에서 팩터 라이브러리 호출

## 구현 계획

### 1. 아키텍처 결정

| 항목 | 결정 | 근거 |
|---|---|---|
| 팩터 구현 | pandas+numpy 벡터화 직접구현 | Python 3.14 호환, TA-Lib C 빌드 금지 |
| pandas-ta | `[project.optional-dependencies].dev` only | 테스트 cross-check 전용, 런타임 import 금지 |
| 레지스트리 | `@register(name)` decorator + `FACTOR_REGISTRY: dict` | 6개 내부 팩터 규모에 단순·충분 |
| 캐시 전송 | 팩터 함수는 `pd.Series`/`pd.DataFrame` 반환, 별도 adapter가 `FACTOR_SCHEMA` 장형식 변환 | 관심사 분리 |
| 엔진 통합 | `required_factors` classvar (default `[]`) → engine precompute → `context["factors"]` | 하위호환, AC 충족 |
| 팩터 셋 | `DEFAULT_FACTOR_SET = "v1"` in `registry.py` | partition_path 기본값 |
| 시간대 | cache adapter에서 `pd.to_datetime(..., utc=True)` 강제 | FACTOR_SCHEMA `datetime[us, UTC]` 준수 |
| signal note | `sma-cross`, `bollinger-breakout`만 신규 생성 | ATR/MACD/RealizedVol은 순수 계산 유틸리티 |

### 2. 단계별 구현 순서 (TDD Red→Green)

#### Phase 1: Registry 기반 (Day 1)

**Step 1.1 — Registry 모듈**
- RED: `tests/test_signals_registry.py` 작성
  - `test_register_and_lookup` — `@register("dummy")` 후 `FACTOR_REGISTRY["dummy"]` 존재 확인
  - `test_duplicate_rejection` — 동일 이름 중복 등록 시 `ValueError`
  - `test_unknown_name_error` — `compute("nonexistent", ...)` 시 `KeyError`
  - `test_compute_delegates_to_function` — `compute("dummy", close=series)` 결과가 등록된 함수 출력과 일치
  - `test_list_registered_factors` — `list_factors()` 반환값에 등록된 이름 포함
  - `test_compute_forwards_only_declared_inputs` — `inputs=["close"]`인 fake factor 등록 후 `compute("fake", close=Series, high=Series)` 호출 시 extra `high`가 함수에 전달되지 않음을 검증
- GREEN: `src/signals/registry.py` 구현
  ```python
  # 핵심 인터페이스
  FACTOR_REGISTRY: dict[str, FactorSpec] = {}
  DEFAULT_FACTOR_SET: str = "v1"

  @dataclass
  class FactorSpec:
      name: str
      func: Callable
      inputs: list[str]       # ["close"] or ["high", "low", "close"]
      default_params: dict    # {"window": 14}

  def register(name: str, *, inputs: list[str], **defaults) -> Callable:
      """Decorator: @register("rsi", inputs=["close"], window=14)"""

  def compute(name: str, **kwargs) -> pd.Series | pd.DataFrame:
      """Registry dispatch: compute("rsi", close=series, window=14)
      Forwards only kwargs matching FactorSpec.inputs — extra kwargs are silently dropped.
      @register must validate that the decorated function's signature matches `inputs` at registration time (using inspect.signature).
      """

  def list_factors() -> list[str]:
      """Return sorted list of registered factor names."""
  ```

**Step 1.2 — RSI를 registry에 등록**
- RED: `tests/test_signals_registry.py`에 `test_rsi_registered` 추가 — `"rsi"` in `FACTOR_REGISTRY`
- GREEN: `src/signals/rsi.py`에 `@register("rsi", inputs=["close"], window=14)` 적용. 기존 `compute_rsi` 시그니처 불변.
- VERIFY: 기존 `tests/test_signals.py` 6개 테스트 전부 통과 확인

#### Phase 2: 5개 신규 팩터 (Day 1-2)

각 팩터에 대해 동일 패턴: RED(테스트) → GREEN(구현) → `@register` 등록.

**Step 2.1 — SMA (`src/signals/sma.py`)**
- RED: `tests/test_factor_sma.py`
  - `test_sma_basic` — 5-bar SMA 수동 계산 일치 (e.g., `[1,2,3,4,5]` → SMA(3) = `[NaN, NaN, 2, 3, 4]`)
  - `test_sma_cross_signal` — golden cross (short > long) / dead cross 감지
  - `test_sma_length_matches_input`
  - `test_sma_cross_check_pandas_ta` — `@pytest.mark.skipif(pandas_ta is None, reason="pandas-ta not installed")`
- GREEN: `src/signals/sma.py`
  ```python
  @register("sma", inputs=["close"], window=20)
  def compute_sma(close: pd.Series, window: int = 20) -> pd.Series: ...

  @register("sma_cross", inputs=["close"], short_window=20, long_window=60)
  def compute_sma_cross(close: pd.Series, short_window: int = 20, long_window: int = 60) -> pd.DataFrame: ...
  # Returns DataFrame with columns: sma_short, sma_long, signal ("golden"/"dead"/None)
  ```

**Step 2.2 — ATR (`src/signals/atr.py`)**
- RED: `tests/test_factor_atr.py`
  - `test_atr_wilder_smoothing` — 20-bar OHLC 수동 계산 대조
  - `test_atr_first_n_nan` — 첫 `window` bars NaN
  - `test_atr_cross_check_pandas_ta`
- GREEN: `src/signals/atr.py`
  ```python
  @register("atr", inputs=["high", "low", "close"], window=14)
  def compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series: ...
  ```

**Step 2.3 — MACD (`src/signals/macd.py`)**
- RED: `tests/test_factor_macd.py`
  - `test_macd_components` — EMA(12), EMA(26), signal(9), histogram 각각 검증
  - `test_macd_returns_dataframe` — columns: `["macd", "signal", "histogram"]`
  - `test_macd_cross_check_pandas_ta`
- GREEN: `src/signals/macd.py`
  ```python
  @register("macd", inputs=["close"], fast=12, slow=26, signal=9)
  def compute_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame: ...
  # Returns DataFrame with columns: macd, signal, histogram
  ```

**Step 2.4 — Bollinger Bands (`src/signals/bollinger.py`)**
- RED: `tests/test_factor_bollinger.py`
  - `test_bollinger_bands_values` — upper = SMA + 2*std, lower = SMA - 2*std
  - `test_bollinger_pct_b` — `%B = (close - lower) / (upper - lower)`
  - `test_bollinger_bandwidth` — `BW = (upper - lower) / middle`
  - `test_bollinger_cross_check_pandas_ta`
- GREEN: `src/signals/bollinger.py`
  ```python
  @register("bollinger", inputs=["close"], window=20, n_std=2.0)
  def compute_bollinger(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.DataFrame: ...
  # Returns DataFrame with columns: upper, middle, lower, pct_b, bandwidth
  ```

**Step 2.5 — Realized Volatility (`src/signals/realized_vol.py`)**
- RED: `tests/test_factor_realized_vol.py`
  - `test_realized_vol_formula` — `log_returns.rolling(20).std() * sqrt(252)` 수동 대조
  - `test_realized_vol_annualization` — `annualize=365` (crypto) vs `annualize=252` (equities) 차이 확인
  - `test_realized_vol_cross_check_pandas_ta`
- GREEN: `src/signals/realized_vol.py`
  ```python
  @register("realized_vol", inputs=["close"], window=20, annualize=252)
  def compute_realized_vol(close: pd.Series, window: int = 20, annualize: int = 252) -> pd.Series: ...
  ```

#### Phase 3: Lookahead Guard (Day 2)

**Step 3.1 — Guard 유틸리티**
- RED: `tests/test_lookahead_guard.py`
  - `test_assert_no_lookahead_pass` — 정상 팩터 (shift(1) 사용)는 통과
  - `test_assert_no_lookahead_fail` — 의도적 룩어헤드 팩터는 `AssertionError`
  - `test_all_registered_factors_no_lookahead` — `@pytest.mark.parametrize("name", list_factors())`로 모든 등록 팩터 자동 검증. append-tail-bar 불변성: bars[0..N-1]의 결과가 bars[0..N] 계산 후에도 동일
- GREEN: `src/signals/lookahead_guard.py`
  ```python
  def assert_no_lookahead(
      factor_func: Callable,
      ohlcv: pd.DataFrame,
      *,
      result_col: str | None = None,
      **kwargs
  ) -> None:
      """Append-tail-bar invariance test.

      1. Compute factor on ohlcv[:-1]
      2. Compute factor on ohlcv (full)
      3. Assert results[:-1] are bit-identical (within float tolerance)
      Raises AssertionError if lookahead detected.
      """
  ```
  > Note: the append-tail-bar invariance test verifies **causality** (bars 0..N-1 don't change when bar N is appended), not lag-1 enforcement. Factors that serve as direct trading signals must additionally apply `shift(1)` before consumption — this is the signal function's responsibility, enforced by separate signal-level tests, not by `lookahead_guard`.

#### Phase 4: Factor Cache (Day 2-3)

**Step 4.1 — Cache adapter**
- RED: `tests/test_factor_cache.py`
  - `test_to_factor_long_schema` — 출력 DataFrame 컬럼이 `FACTOR_SCHEMA` 키와 일치
  - `test_to_factor_long_values` — symbol, factor_set, factor_name 값 정확
  - `test_write_read_roundtrip` — `tmp_path` 기반 parquet 저장 → 로드 → 값 일치
  - `test_partition_path_string` — `partition_path("factor", ...)` 호출 결과 경로 형식 검증
  - `test_ts_utc_enforced` — naive datetime 입력 시 UTC 변환 확인
- GREEN: `src/signals/cache.py`
  ```python
  def to_factor_long(
      result: pd.Series | pd.DataFrame,
      *,
      symbol: str,
      factor_set: str = DEFAULT_FACTOR_SET,
      factor_name: str,
      ts_index: pd.DatetimeIndex,
  ) -> pd.DataFrame:
      """Melt factor result into FACTOR_SCHEMA long format."""

  def write_factor_parquet(
      df: pd.DataFrame,
      root: Path,
      symbol: str,
      factor_set: str = DEFAULT_FACTOR_SET,
  ) -> Path:
      """Write factor DataFrame to hive-partitioned parquet."""

  def read_factor_parquet(
      root: Path,
      symbol: str,
      factor_set: str = DEFAULT_FACTOR_SET,
      factor_name: str | None = None,
  ) -> pd.DataFrame:
      """Read factor parquet, optionally filtered by factor_name."""
  ```

#### Phase 5: Engine Integration (Day 3)

**Step 5.1 — Protocol 확장**
- `src/backtest/protocol.py`에 `required_factors: ClassVar[list[str]]` 추가 (optional, default `[]`)
- 기존 `Strategy` Protocol은 `on_init`, `on_bar` 메서드만 요구하므로 classvar 추가는 비파괴적
- MomoBtcV2는 `required_factors` 미선언 → `getattr(strategy, "required_factors", [])` fallback
- > `ClassVar` attributes are excluded from `@runtime_checkable` Protocol structural checks by design (PEP 544). So `isinstance(strategy, Strategy)` at `engine.py:31` will NOT enforce `required_factors`. The engine uses `getattr(strategy, "required_factors", [])` as the actual runtime fallback. The `ClassVar` declaration on the Protocol is **documentary only** (helps mypy, helps readers). Any implementing strategy may omit the attribute entirely and still type-check as `Strategy`.

**Step 5.2 — Engine 수정**
- RED: `tests/test_backtest_factor_integration.py`
  - `test_engine_injects_factors_into_context` — `required_factors=["rsi"]`인 더미 전략, `context["factors"]["rsi"]`가 존재하고 `compute_rsi(history.close, 14)` 결과와 bit-for-bit 일치
  - `test_engine_skips_factors_when_empty` — `required_factors=[]`인 MomoBtcV2, `context`에 `"factors"` 키 없음 (기존 동작 보존)
  - `test_engine_factor_length_matches_history` — 주입된 팩터 Series 길이 == history 길이
- GREEN: `src/backtest/engine.py` 수정
  - `strategy.on_bar(bar, history, {})` 호출 전에:
    ```python
    context = {}
    required = getattr(strategy, "required_factors", [])
    if required:
        from signals.registry import compute, FACTOR_REGISTRY
        context["factors"] = {}
        for name in required:
            spec = FACTOR_REGISTRY[name]
            kwargs = {col: history[col] for col in spec.inputs}  # only forward declared inputs
            context["factors"][name] = compute(name, **kwargs)
    signal = strategy.on_bar(bar, history, context)
    ```
  - `strategy.on_init({})` → `strategy.on_init(context)` (context 전달 통일)

#### Phase 6: Signal Notes + .ai.md (Day 3)

**Step 6.1 — Signal notes 작성**
- `docs/specs/signals/sma-cross.md` — frontmatter: `type: signal`, `id: sma-cross`, `name: SMA Crossover`, `inputs: [close]`, `lookback: 60`, `tags: [technical, trend]`. 본문: 골든/데드 크로스 설명, `[[13-feature-alpha-catalog]]` 백링크
- `docs/specs/signals/bollinger-breakout.md` — frontmatter: `type: signal`, `id: bollinger-breakout`, `name: Bollinger Breakout`, `inputs: [close]`, `lookback: 20`, `tags: [technical, volatility]`. 본문: %B 기반 breakout 설명, `[[13-feature-alpha-catalog]]` 백링크
- Both new signal notes reference only `[[13-feature-alpha-catalog]]` (existing at `docs/background/13-feature-alpha-catalog.md`) — no new wikilink dependencies.

**Step 6.2 — .ai.md 업데이트**
- `src/signals/.ai.md` — 새 모듈 목록(sma, atr, macd, bollinger, realized_vol), registry.py, cache.py, lookahead_guard.py 추가
- `src/backtest/.ai.md` — `required_factors` 확장 포인트, `context["factors"]` 주입 메커니즘 기술
- `src/data_lake/.ai.md` — factor cache consumer(`src/signals/cache.py`)가 `FACTOR_SCHEMA` + `partition_path("factor", ...)` 사용 명시

**Step 6.3 — pyproject.toml 업데이트**
- `[project.optional-dependencies].dev`에 `"pandas-ta"` 추가

**Step 6.4 — `src/signals/__init__.py` 업데이트**
- 신규 모듈 import 추가 (registry 자동 등록 트리거)
- 기존 export (`compute_rsi`, `detect_divergence`) 불변

### 3. 변경/신규 파일 목록

| 파일 | 상태 | 설명 |
|---|---|---|
| `src/signals/registry.py` | 신규 | `@register`, `FACTOR_REGISTRY`, `compute()`, `list_factors()`, `DEFAULT_FACTOR_SET` |
| `src/signals/sma.py` | 신규 | `compute_sma`, `compute_sma_cross` |
| `src/signals/atr.py` | 신규 | `compute_atr` (Wilder smoothing) |
| `src/signals/macd.py` | 신규 | `compute_macd` → DataFrame(macd, signal, histogram) |
| `src/signals/bollinger.py` | 신규 | `compute_bollinger` → DataFrame(upper, middle, lower, pct_b, bandwidth) |
| `src/signals/realized_vol.py` | 신규 | `compute_realized_vol` (log-return rolling std) |
| `src/signals/lookahead_guard.py` | 신규 | `assert_no_lookahead` append-tail-bar 검증 |
| `src/signals/cache.py` | 신규 | `to_factor_long`, `write_factor_parquet`, `read_factor_parquet` |
| `src/signals/rsi.py` | 수정 | `@register("rsi", ...)` decorator 추가 (함수 시그니처 불변) |
| `src/signals/__init__.py` | 수정 | 신규 모듈 import 추가, 기존 export 불변 |
| `src/backtest/protocol.py` | 수정 | `required_factors: ClassVar[list[str]]` 추가 (optional) |
| `src/backtest/engine.py` | 수정 | `required_factors` 기반 팩터 precompute + `context["factors"]` 주입 |
| `tests/test_signals_registry.py` | 신규 | registry 등록/조회/중복/에러 테스트 + `test_compute_forwards_only_declared_inputs` |
| `tests/test_factor_sma.py` | 신규 | SMA + cross 수치 검증 |
| `tests/test_factor_atr.py` | 신규 | ATR Wilder smoothing 검증 |
| `tests/test_factor_macd.py` | 신규 | MACD 3-component 검증 |
| `tests/test_factor_bollinger.py` | 신규 | Bollinger Bands + %B + BW 검증 |
| `tests/test_factor_realized_vol.py` | 신규 | Realized Vol 연율화 검증 |
| `tests/test_lookahead_guard.py` | 신규 | 전 팩터 lookahead 불변성 자동 검증 |
| `tests/test_factor_cache.py` | 신규 | cache round-trip, schema 키, UTC 강제 |
| `tests/test_backtest_factor_integration.py` | 신규 | engine-factor 통합 테스트 + `test_rsi_perf` O(N^2) 벤치마크 게이트 |
| `docs/specs/signals/sma-cross.md` | 신규 | SMA Crossover signal note |
| `docs/specs/signals/bollinger-breakout.md` | 신규 | Bollinger Breakout signal note |
| `src/signals/.ai.md` | 수정 | 신규 모듈/registry/cache/guard 목록 |
| `src/backtest/.ai.md` | 수정 | required_factors 확장 포인트. Document that `context["factors"]` is an engine-managed reserved key: the engine populates `context["factors"][name] = pd.Series` for each name in `strategy.required_factors` before every `on_bar`. Strategies must not write to this key. Fresh `context` per bar (no cross-bar state). |
| `src/data_lake/.ai.md` | 수정 | factor cache consumer 명시 |
| `pyproject.toml` | 수정 | dev deps에 pandas-ta 추가 |

### 4. AC 매핑

| AC | 충족 방법 | 검증 테스트 |
|---|---|---|
| **5+ 팩터 구현 + 단위 테스트** | RSI(기존) + SMA + ATR + MACD + Bollinger + RealizedVol = 6개. 각각 전용 테스트 파일. Registry input-dispatch 검증 포함. | `test_signals.py` (6), `test_factor_sma.py` (4), `test_factor_atr.py` (3), `test_factor_macd.py` (3), `test_factor_bollinger.py` (4), `test_factor_realized_vol.py` (3), `test_signals_registry.py::test_compute_forwards_only_declared_inputs` |
| **룩어헤드 검증 테스트** | `lookahead_guard.py`의 append-tail-bar 불변성 검사. 전 등록 팩터 자동 parametrize. | `test_lookahead_guard.py::test_all_registered_factors_no_lookahead` |
| **백테스트 엔진에서 팩터 라이브러리 호출** | `engine.py`가 `required_factors` 기반으로 `registry.compute()` 호출 → `context["factors"]` 주입. | `test_backtest_factor_integration.py` (3) + `test_rsi_perf` O(N^2) 벤치마크 게이트 (wall time <= 60s) |

### 5. 엣지케이스·리스크

| 리스크 | 영향 | 완화 |
|---|---|---|
| **RSI `@register` 추가 시 기존 import 파손** | `from signals.rsi import compute_rsi` 실패 가능 | decorator는 원래 함수를 반환해야 함 (`functools.wraps` 패턴). 기존 6개 테스트로 회귀 검증. |
| **팩터 모듈 import 순서 문제** | `__init__.py`에서 registry import 전에 팩터 모듈이 import되면 registry 미초기화 | `registry.py`를 먼저 정의, 팩터 모듈이 `from .registry import register`로 import. `__init__.py`에서 registry → 팩터 순서 import. |
| **engine.py context 변경** | 현재 `strategy.on_bar(bar, history, {})` → `context` dict 전달로 변경 | MomoBtcV2는 `context` 무시 (`on_bar`에서 사용 안 함). 빈 dict → 팩터 포함 dict 모두 호환. |
| **DataFrame 반환 팩터의 lookahead 검증** | MACD/Bollinger는 DataFrame 반환, guard가 Series만 처리하면 실패 | `assert_no_lookahead`가 DataFrame도 처리하도록 column-wise 비교 구현. |
| **pandas-ta 미설치 환경에서 cross-check 스킵** | CI에서 pandas-ta 미설치 시 일부 테스트 스킵 | `@pytest.mark.skipif` 조건 처리. 핵심 수치 테스트는 pandas-ta 무관하게 수동 계산으로 별도 검증. |
| **NaN 전파** | 팩터 warmup 구간 NaN이 cache/engine에서 오류 유발 | cache adapter에서 NaN 행 제거 후 parquet 저장. engine은 warmup hold 로직 기존 보존. |
| **O(N^2) factor recomputation cost** | Engine recomputes each declared factor over the full rolling history every bar. For BTCUSDT 15m over 2 years (~70k bars) x 6 factors, this is ~14.7 billion element-ops. | **Trigger**: micro-benchmark during Phase 5 — run `pytest tests/test_backtest_factor_integration.py -k rsi_perf` that calls `run_backtest` on 70k synthetic bars with `required_factors=["rsi"]`. **Gate**: wall time > 60s on the dev machine -> open a follow-up issue for incremental/cached factor computation **before** merging #71. <=60s -> defer optimization, merge as-is. |

### 6. 롤백 전략

1. **단계별 독립 커밋**: Phase별 커밋으로 분리하여 문제 시 해당 Phase만 revert
2. **기존 파일 최소 수정**: `rsi.py`는 decorator 1줄 추가, `engine.py`는 `on_bar` 호출 전 10줄 추가, `protocol.py`는 classvar 1줄 추가 — revert 범위 명확
3. **하위호환 보장**: `required_factors` 미선언 전략은 기존 동작 그대로. `getattr(..., [])` fallback으로 안전
4. **테스트 gate**: 기존 `test_signals.py` 6개 + 신규 테스트 전부 통과해야 Phase 진행. 실패 시 해당 Phase revert
