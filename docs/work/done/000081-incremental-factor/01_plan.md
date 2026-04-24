---
type: work-done
id: 01_plan
name: "#81 팩터 점증 계산 (incremental factor computation) 구현 플랜"
issue: 81
status: draft
revision: v2
---

# Plan — 팩터 점증 계산 (Incremental Factor Computation) v2

> **요약**: 백테스트 엔진의 O(N^2) 팩터 재계산을 O(N) 선계산+슬라이싱 경로로 교체한다.
> 전략 함수(`compute_rsi` 등)는 수정하지 않고, 엔진이 루프 진입 전 전체 OHLCV 로 한 번만 계산한 뒤 바 루프 내에서 `.iloc[:i+1]` 로 슬라이싱하여 동일 결과를 제공한다.
> `momo_btc_v2` 를 마이그레이션하여 직접 `compute_rsi` 호출을 제거하고 `required_factors` 훅으로 전환한다.
> 70k-bar 단일 RSI < 60s, 5팩터 < 120s 성능 게이트를 통과한다.

---

## 1. RALPLAN-DR

### Principles

1. **팩터 함수 무수정** — `compute_rsi`, `compute_macd` 등 기존 함수의 시그니처와 구현을 변경하지 않는다. 엔진만 수정.
2. **bit-identical 결과** — 선계산+슬라이스 경로의 출력이 기존 full-recompute 경로와 정확히 동일해야 한다 (atol/rtol 허용 없음).
3. **인과성(causality) 보존** — 전 팩터가 인과적(bar i 의 값은 bar 0..i 의 입력에만 의존)이므로 precompute-then-slice 가 수학적으로 동치. 비인과 팩터 진입을 구조적으로 차단.
4. **최소 변경 범위** — 엔진 루프 내부 팩터 계산 경로와 momo_btc_v2 마이그레이션만 수정. 아키텍처 재설계 없음.
5. **성능 게이트 선행** — 성능 테스트가 기존 기능 테스트와 동일 PR 에서 통과해야 머지 가능.

### Decision Drivers

1. **실행 시간** — 70k-bar 백테스트가 현재 외삽치 ~13h → 60s 이내로 단축 필수 (이슈 AC).
2. **정확성** — 기존 경로와 bit-identical. 금융 시뮬레이션이므로 부동소수점 근사 허용 불가.
3. **유지보수** — 팩터 추가 시 엔진 수정 없이 레지스트리 등록만으로 동작. 향후 라이브 엔진 분기 여지.

### Options

**Option A (채택): 루프 전 선계산 + iloc 슬라이싱**

엔진이 `for name in required_factors: precomputed[name] = compute(name, ohlcv[col])` 로 전체 시계열을 한 번만 계산하고, 바 루프 내에서 `precomputed[name].iloc[:i+1]` 로 슬라이싱.

- Pros: (1) 팩터 함수 무수정, (2) O(N) 계산으로 성능 목표 달성, (3) pandas `.iloc[:i+1]` 이 Series/DataFrame 양쪽 동일 구문
- Cons: (1) 메모리 추가 (팩터당 N-길이 Series/DataFrame 1개 — 70k 기준 수 MB, 무시할 수준), (2) 비인과 팩터에 무효 (causal 가드로 차단)

**Option B (탈락): 점증(incremental) 상태 보존형 팩터**

각 팩터를 `IncrementalFactorSpec` protocol 로 감싸 상태를 보존하며 한 바씩 업데이트.

- Invalidation rationale: (1) Principle #1 위반 — 모든 팩터 함수를 상태 보존형으로 재작성해야 함, (2) EWM 계열(`adjust=False`)은 내부 상태 추출이 pandas API 로 불가, (3) 변경 범위가 Option A 의 3배 이상, (4) 배치 백테스트에서는 선계산이 수학적으로 동치이므로 복잡도 대비 이점 없음. 라이브 스트리밍 엔진에서만 의미가 있으며, 그때는 별도 이슈로 `IncrementalFactorSpec` 도입 가능.

---

## 2. 완료 기준

### Part A — 엔진 선계산 경로 + 회귀 테스트 + 성능 게이트

- [ ] `engine.py` 가 `required_factors` 가 있을 때 루프 전 선계산 + 루프 내 슬라이싱으로 동작
- [ ] `FactorSpec.causal: bool = True` 필드 추가, 엔진 precompute 진입 시 assert
- [ ] 전체 바 bit-identical 회귀 테스트 통과 (500-bar, 5팩터, 2500 포인트)
- [ ] 70k-bar 단일 RSI < 60s, 5팩터 < 120s 성능 게이트 통과
- [ ] 기존 테스트 전부 통과 (룩어헤드 가드 포함)

### Part B — momo_btc_v2 마이그레이션 + 문서

- [ ] `momo_btc_v2.on_bar` 에서 `compute_rsi` 직접 호출 제거, `required_factors = ["rsi"]` + `context["factors"]["rsi"]` 사용
- [ ] `detect_divergence` import 유지, `compute_rsi` import 제거
- [ ] 마이그레이션 전후 `on_bar` 결과 동일 확인 (회귀 테스트)
- [ ] `src/backtest/.ai.md` 성능 경고 업데이트, `src/signals/.ai.md` 최신화

---

## 3. Part A Task Flow

### A-1. 엔진 선계산 경로 구현

**변경 파일**: `src/backtest/engine.py` (L42-99)

**구현 내용**:

1. `required_factors` 파싱 직후 (L42-48), 루프 진입 전에 precompute 블록 추가:

```
precomputed_factors: dict[str, pd.Series | pd.DataFrame] = {}
if required_factors:
    from signals.registry import FACTOR_REGISTRY, compute
    assert all(FACTOR_REGISTRY[n].causal for n in required_factors), \
        f"non-causal factors cannot use precompute path: ..."
    for name in required_factors:
        spec = FACTOR_REGISTRY[name]
        kwargs = {col: ohlcv[col] for col in spec.inputs if col in ohlcv.columns}
        precomputed_factors[name] = compute(name, **kwargs, **spec.default_params)
```

2. 루프 내 기존 팩터 계산 블록 (L91-98) 을 슬라이싱으로 교체:

```
if required_factors:
    factors: dict[str, pd.Series | pd.DataFrame] = {}
    for name in required_factors:
        factors[name] = precomputed_factors[name].iloc[:i+1]
    context["factors"] = factors
```

> **구현 메모 (A3)**: `ohlcv.iloc[:i+1]` 은 DataFrame 슬라이스 (view). 엔진은 루프 진입 전 `precomputed_factors[name] = compute(name, ohlcv[col], ...)` 로 Series/DataFrame 을 저장하고, 루프 내에서 `precomputed_factors[name].iloc[:i+1]` 로 슬라이스. pandas 의 `.iloc[:i+1]` 은 Series/DataFrame 양쪽 모두 지원하므로 동일 구문 사용 가능.

**검증**: A-2 회귀 테스트 + 기존 `test_engine_injects_required_factors_into_context` 통과.

### A-1-sub. FactorSpec causal 필드 추가 (A4)

**변경 파일**: `src/signals/registry.py` (L13-17)

**구현 내용**:

`FactorSpec` 에 `causal: bool = True` 필드 추가 (기본값 True):

```python
@dataclass
class FactorSpec:
    name: str
    func: Callable[..., Any]
    inputs: list[str]
    default_params: dict[str, Any] = field(default_factory=dict)
    causal: bool = True
```

- 팩터 함수 자체는 수정하지 않음 (Principle #1 유지)
- 기존 팩터는 전부 기본값 `True` 로 등록되므로 동작 변화 없음
- `register()` 데코레이터의 시그니처에 `causal: bool = True` 키워드 추가 (optional)
- 엔진 precompute 경로는 `assert all(FACTOR_REGISTRY[n].causal for n in required_factors)` 로 진입 가드
- 비인과 팩터가 도입될 때를 위한 구조적 에스케이프 해치

**검증**: `test_all_registered_factors_causal` (test_lookahead_guard.py L67-77) 가 이미 전 레지스트리 스캔. 기존 테스트 통과 확인.

### A-2. bit-identical 전체 바 회귀 테스트 (MF-3)

**변경 파일**: `tests/backtest/test_backtest_factor_integration.py`

**구현 내용**:

기존 `_FactorProbeStrategy` 를 확장하여 **전체 바의 `context["factors"]` 를 누적 캡처**:

```python
class _FactorProbeAllBarsStrategy:
    """Captures context["factors"] at every bar for bit-identical verification."""
    required_factors: ClassVar[list[str]] = ["rsi", "sma", "atr", "macd", "bollinger"]

    def __init__(self) -> None:
        self.all_contexts: list[dict] = []

    def on_init(self, context: dict) -> None:
        pass

    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
        if "factors" in context:
            self.all_contexts.append({
                "bar_idx": len(self.all_contexts),
                "factors": {k: v.copy() for k, v in context["factors"].items()},
            })
        else:
            self.all_contexts.append({"bar_idx": len(self.all_contexts), "factors": {}})
        return Signal(action="hold", size=0.0, reason="probe-all")
```

**테스트 함수** `test_precompute_bit_identical_all_bars`:

- 500-bar 샘플, 5 팩터 (`rsi`, `sma`, `atr`, `macd`, `bollinger`)
- 각 바 `i` 에 대해 `precomputed.iloc[:i+1]` 과 `compute(name, history.iloc[:i+1][col])` 을 bit-identical 비교
- 5 팩터 x 500 바 = 2,500 포인트 검증 (실행 시간 합리적)
- bit-identical 검증 도구: `pd.testing.assert_series_equal(got, expected, check_exact=True, check_names=False)` / `pd.testing.assert_frame_equal(got, expected, check_exact=True, check_names=False)`

**검증**: 테스트 통과 = 선계산+슬라이싱이 full-recompute 와 정확히 동일함을 증명.

### A-3. 성능 게이트

**변경 파일**: `tests/backtest/test_backtest_factor_integration.py` (L109-135 기존 `test_rsi_perf` 수정)

**구현 내용**:

기존 `test_rsi_perf` 의 AC 를 유지하되, 선계산 경로가 적용된 상태에서 통과하는지 확인:

- 70k-bar, `required_factors=["rsi"]`, wall time < 60s
- 추가: 70k-bar, `required_factors=["rsi","sma","atr","macd","bollinger"]`, wall time < 120s
- 실측값은 PR 본문에 기록 필수

> **성능 AC 롤백 경로 (SF-1)**: 70k-bar 5팩터 < 120s / 단일 RSI < 60s 는 하드 게이트. 실측 실패 시 완화 옵션:
> (a) dev 머신 스펙 명시 후 AC 재조정 (PR 리뷰 승인 필요)
> (b) numpy view 기반 슬라이싱으로 pandas 오버헤드 축소
> (c) 팩터 등록 수 제한 (`required_factors` 최대 개수 경고)
> 실측값은 A-3 에서 PR 본문에 기록 필수.

**검증**: `pytest -m slow tests/backtest/test_backtest_factor_integration.py` green.

---

## 4. Part B Task Flow

### B-1. momo_btc_v2 마이그레이션

**변경 파일**: `src/backtest/strategies/momo_btc_v2.py` (L9, L80-87)

**구현 내용**:

1. `required_factors` 클래스 속성 추가:

```python
class MomoBtcV2:
    required_factors: ClassVar[list[str]] = ["rsi"]
    ...
```

2. `on_bar` 에서 `compute_rsi` 직접 호출 제거, context 에서 수신:

```python
def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal:
    ...
    close = history["close"]
    rsi = context["factors"]["rsi"]
    div = detect_divergence(close, rsi, self.LOOKBACK)
    ...
```

3. import 변경:

```python
# 변경 전
from signals.rsi import compute_rsi, detect_divergence

# 변경 후
from signals.rsi import detect_divergence
```

> **명확화: 마이그레이션 후 `on_bar` 동작 (SF-2)**
>
> 1. `rsi = context["factors"]["rsi"]` 로 RSI 시리즈 수신 (엔진이 선계산)
> 2. `from signals.rsi import detect_divergence` -- `compute_rsi` import 만 제거, `detect_divergence` import 유지
> 3. `div = detect_divergence(close, rsi, self.LOOKBACK)` -- `close` 는 `history["close"]`, `rsi` 는 context 에서 가져온 값
> 즉 `compute_rsi` 호출만 없어지고 `detect_divergence` 호출은 그대로. `detect_divergence` 는 "신호 해석기" 로 레지스트리 미등록 유지.

**검증**: B-2 회귀 테스트.

### B-2. 마이그레이션 회귀 테스트

**변경 파일**: `tests/backtest/` 내 기존 momo_btc_v2 테스트 또는 신규

**구현 내용**:

- 마이그레이션 전후 `MomoBtcV2.on_bar` 가 동일한 Signal 을 생성하는지 검증
- sizing 포함 경로 (`half-kelly`, `vol-target`) 모두 포함

> **SF-3**: `_entry_size` 는 `history["close"]` 의 tail 만 보므로 RSI 훅 마이그레이션과 무관. Step B-2 회귀 테스트에서 sizing 포함 경로(half-kelly, vol-target) 모두 검증. R5 는 "verified no impact" 으로 확인됨.

**검증**: `pytest tests/backtest/` green, 마이그레이션 전후 equity curve 동일.

### B-3. 문서 최신화

**변경 파일**:
- `src/backtest/.ai.md` (L19) — 성능 경고 문구 업데이트 (O(N^2) 경고 → 선계산 적용 완료 기술)
- `src/signals/.ai.md` — `FactorSpec.causal` 필드 추가 기술
- `docs/work/active/000081-incremental-factor/01_plan.md` — status: complete 로 변경

**검증**: `.ai.md` 최신화 확인, 프론트매터 불변식 통과.

---

## 5. 리스크 / 완화

| ID | 리스크 | 영향 | 완화 |
|----|--------|------|------|
| R1 | EWM `adjust=True` 사용 시 bit-identical 구조적 깨짐 | 높음 | 현재 `macd.py` L21-24 는 `adjust=False` 확인됨. (a) 신규 EWM 팩터 등록 시 PR 리뷰에서 `adjust=False` 확인, (b) 선택적 CI 가드 -- `grep -r "\.ewm(" src/signals/ \| grep -v "adjust=False"` 가 빈 결과를 내는지 확인하는 스크립트 추가 고려 (Open Question Q4) |
| R2 | pandas `.iloc` 슬라이싱이 copy vs view 에 따라 성능 차이 | 낮음 | pandas `.iloc[:i+1]` 은 view 반환 (CoW 환경에서도 읽기 전용이면 copy 불발). 성능 게이트가 실측 검증 |
| R3 | 500-bar 회귀 테스트가 edge case 누락 | 중간 | warmup 구간(NaN) + 정상 구간 모두 포함. 기존 룩어헤드 가드(150-bar, 전 팩터)가 보완 |
| R4 | Wilder 평활(RSI, ATR)의 Python for 루프가 선계산 1회에서도 느림 | 낮음 | 70k-bar 단일 RSI 선계산 ~0.5s (벤치마크 외삽). 게이트 60s 대비 충분 |
| R5 | `_entry_size` 가 RSI 마이그레이션 영향을 받을 가능성 | 없음 (verified no impact) | `_entry_size` 는 `history["close"]` 의 tail 만 사용. RSI 훅 마이그레이션과 무관. B-2 에서 sizing 포함 경로(half-kelly, vol-target) 모두 검증 |
| R6 | 향후 비인과(non-causal) 팩터 등록 시 precompute-slice 경로가 full-recompute 와 다른 값 생산 | 중간 | (a) `FactorSpec.causal` 필드 (A-1-sub), (b) `tests/signals/test_lookahead_guard.py::test_all_registered_factors_are_causal` 가 이미 전 레지스트리 스캔 -- CI 게이트로 승격 (이미 pytest 에서 실행되므로 추가 작업 불필요) |

---

## 6. 테스트 전략

### 기존 유지 (수정 없음)

- `test_engine_injects_required_factors_into_context` -- 마지막 바 context 검증 (기존 호환성)
- `test_engine_skips_factors_when_empty` -- 팩터 미사용 경로
- `test_engine_factor_length_matches_history` -- 팩터 길이 == history 길이
- `test_engine_rejects_unregistered_factor` -- 미등록 팩터 에러
- `test_all_registered_factors_causal` (test_lookahead_guard.py L67-77) -- 전 레지스트리 인과성 검증

### 신규 추가

- `test_precompute_bit_identical_all_bars` -- 500-bar, 5팩터, 전체 바 bit-identical (A-2)
- `test_rsi_perf` 수정 -- 선계산 경로에서 70k-bar < 60s (A-3)
- `test_5factor_perf` 신규 -- 70k-bar 5팩터 < 120s (A-3)
- `test_momo_btc_v2_migration_regression` -- 마이그레이션 전후 equity curve 동일 (B-2)
- `test_momo_btc_v2_sizing_modes_after_migration` -- half-kelly, vol-target 경로 검증 (B-2)

### bit-identical 검증 도구 (MF-2)

> **bit-identical 정의**: `pd.Series` 는 `pd.testing.assert_series_equal(got, expected, check_exact=True, check_names=False)`; `pd.DataFrame` 는 `pd.testing.assert_frame_equal(got, expected, check_exact=True, check_names=False)`. `atol`/`rtol` 도입 금지. 기존 `check_exact=False, rtol=1e-5` 기본값은 사용하지 않는다.

---

## 7. Guardrails

### Must Have

- 팩터 함수 (`compute_rsi`, `compute_macd` 등) 시그니처 및 구현 변경 금지
- 전체 바 bit-identical 회귀 테스트 통과 필수
- 기존 테스트 전부 green (룩어헤드 가드 포함)
- 성능 게이트: 70k-bar 단일 RSI < 60s, 5팩터 < 120s
- `detect_divergence` 는 레지스트리 미등록 유지 (신호 해석기, 팩터 아님)

### Must NOT Have

- `atol` / `rtol` 근사 비교 도입 금지
- 팩터 함수 내부 수정 금지
- 비인과 팩터의 precompute 경로 진입 허용 금지
- 라이브 스트리밍 엔진 구현 (본 PR 범위 밖)
- `FactorSpec.func` 시그니처 확장 (배치/라이브 분기는 별도 이슈)

### EWM adjust=False 불변식 (A2)

> EWM 계열 팩터(MACD 등)는 반드시 `adjust=False` 사용. `adjust=True` 는 정규화 분모가 길이 의존적이라 precompute-slice 경로에서 bit-identical 깨짐. 현재 `src/signals/macd.py:21-24` 가 `adjust=False` 확인됨. 향후 EWM 신규 팩터 도입 시 리뷰에서 이 불변식 확인.

### 커밋 순서 강제 (MF-4)

> 커밋 순서 강제: Part A 변경(engine.py + registry.py + 회귀 테스트) 을 먼저 커밋하고 Part A CI green 확인 후에만 Part B 커밋 추가. 단일 PR 내에서 `git bisect` 가능성 확보.

---

## 8. 검증 절차

### Part A 검증

```bash
# 1. 기존 테스트 전부 통과
pytest tests/backtest/test_backtest_factor_integration.py -v
pytest tests/signals/test_lookahead_guard.py -v

# 2. 전체 바 bit-identical 회귀
pytest tests/backtest/test_backtest_factor_integration.py::test_precompute_bit_identical_all_bars -v

# 3. 성능 게이트
pytest -m slow tests/backtest/test_backtest_factor_integration.py::test_rsi_perf -v
pytest -m slow tests/backtest/test_backtest_factor_integration.py::test_5factor_perf -v

# 4. 룩어헤드 가드 (기존)
pytest tests/signals/test_lookahead_guard.py::test_all_registered_factors_causal -v
```

### Part B 검증

```bash
# 5. momo_btc_v2 마이그레이션 회귀
pytest tests/backtest/ -k "momo" -v

# 6. 전체 테스트 스위트
pytest tests/ --ignore=tests/services -v

# 7. 불변식 체크
python scripts/check_invariants.py --strict
```

---

## 9. 변경 파일 목록

### 수정

| 파일 | Task | 변경 내용 |
|------|------|-----------|
| `src/backtest/engine.py` | A-1 | 루프 전 precompute + 루프 내 슬라이싱 |
| `src/signals/registry.py` | A-1-sub | `FactorSpec.causal: bool = True` 필드 추가, `register()` 에 `causal` kwarg |
| `tests/backtest/test_backtest_factor_integration.py` | A-2, A-3 | 전체 바 회귀 테스트 + 5팩터 성능 게이트 추가 |
| `src/backtest/strategies/momo_btc_v2.py` | B-1 | `required_factors` 추가, `compute_rsi` 호출 제거 |
| `src/backtest/.ai.md` | B-3 | 성능 경고 업데이트 |
| `src/signals/.ai.md` | B-3 | `FactorSpec.causal` 필드 문서화 |

### 신규

| 파일 | Task | 내용 |
|------|------|------|
| (기존 테스트 파일에 함수 추가) | A-2 | `_FactorProbeAllBarsStrategy`, `test_precompute_bit_identical_all_bars` |
| (기존 테스트 파일에 함수 추가) | A-3 | `test_5factor_perf` |
| (기존 또는 신규 테스트) | B-2 | `test_momo_btc_v2_migration_regression`, `test_momo_btc_v2_sizing_modes_after_migration` |

---

## 10. Open Questions

### Q1. 팩터 DAG (의존성 그래프)

현재 팩터 간 의존성이 없으므로(각 팩터는 OHLCV 열만 입력) DAG 불필요. 향후 "RSI of MACD" 같은 파생 팩터 도입 시 DAG 기반 topological sort 필요. 본 PR 범위 밖.

### Q2. 라이브 스트리밍 엔진 한계 (A7)

본 PR 은 배치 전용 경로만 구현. 라이브 엔진은 `IncrementalFactorSpec` (상태 보존형 protocol) 이 필요할 것. **`FactorSpec.func` 시그니처는 본 PR 에서 확장하지 않음** -- 배치/라이브 분기는 별도 이슈로.

### Q3. FactorSpec.func 시그니처 동결

본 PR 에서 `func: Callable[..., Any]` 를 변경하지 않음. 라이브 엔진 대응 시 `IncrementalFactorSpec` 을 별도 protocol 로 도입하되 기존 `FactorSpec` 하위 호환성 유지.

### Q4. EWM 가드 스크립트

`grep -r "\.ewm(" src/signals/ | grep -v "adjust=False"` 가 빈 결과를 내는지 확인하는 CI 스크립트 추가 여부. 현재 팩터가 7개뿐이므로 PR 리뷰로 충분하나, 팩터 수 증가 시 자동화 고려. 본 PR 에서는 선택적.

---

## 11. Changelog (v1 -> v2)

### Architect Revisions 반영 (7/7)

| ID | 내용 | 반영 위치 |
|----|------|-----------|
| A1 | 전체 바 검증 (마지막 바만 -> 모든 바) | A-2 task 전면 재구성, MF-3 |
| A2 | EWM `adjust=False` 불변식 | Guardrails "EWM adjust=False 불변식" 섹션 |
| A3 | `.iloc[:i+1]` Series/DataFrame 동일 구문 메모 | A-1 task 구현 메모 |
| A4 | `FactorSpec.causal: bool` 필드 | A-1-sub task 신설 |
| A5 | R1 강화 (EWM adjust 리스크) | 리스크 표 R1 |
| A6 | R6 추가 (비인과 팩터 리스크) | 리스크 표 R6 |
| A7 | 라이브 엔진 한계 구체화 | Open Questions Q2 |

### Critic Must-Fix 반영 (4/4)

| ID | 내용 | 반영 위치 |
|----|------|-----------|
| MF-1 | Write 툴로 01_plan.md 덮어쓰기 | 본 파일 자체 |
| MF-2 | bit-identical 검증 도구 명시 | 테스트 전략 "bit-identical 검증 도구" 박스 |
| MF-3 | 모든 바 검증 | A-2 task (`_FactorProbeAllBarsStrategy` + 2500 포인트) |
| MF-4 | Part A->B 커밋 순서 집행 | Guardrails "커밋 순서 강제" 섹션 |

### Critic Should-Fix 반영 (3/3)

| ID | 내용 | 반영 위치 |
|----|------|-----------|
| SF-1 | 성능 AC 롤백 경로 | A-3 task note |
| SF-2 | detect_divergence 처리 명확화 | B-1 task "명확화" 박스 |
| SF-3 | entry_size 영향 없음 문서화 | 리스크 표 R5 "verified no impact" |
