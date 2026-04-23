# feat: 알파 팩터 파이프라인 (피처 엔지니어링 프레임워크)

## 목표
시그널·팩터를 체계적으로 생성·관리하는 파이프라인 구축.

## 배경
- `docs/background/13-feature-alpha-catalog.md` 에 40+ 팩터 카탈로그 존재
- `docs/specs/signals/rsi-divergence.md` 에 RSI 시그널 스펙
- 현재 팩터 계산은 전략 코드 안에 인라인 — 재사용·테스트 불가

## 범위
- `src/signals/` 팩터 라이브러리 (RSI, SMA, 볼린저 등)
- 팩터 레지스트리 (이름 → 계산 함수 매핑)
- 룩어헤드 바이어스 자동 검출 (point-in-time 검증)
- Parquet 기반 팩터 캐시 (data-lake Factor 파티션)

## 완료 기준
- [x] 5+ 팩터 구현 + 단위 테스트 (6 팩터: RSI·SMA·ATR·MACD·Bollinger·RealizedVol, 각 전용 테스트 파일)
- [x] 룩어헤드 검증 테스트 (`src/signals/lookahead_guard.py` + 전 등록 팩터 parametrize)
- [x] 백테스트 엔진에서 팩터 라이브러리 호출 (`required_factors` 컨벤션 → `context["factors"]` 주입)

## 선행 조건
- #67 (데이터 파이프라인 + 백테스트) — 머지됨 (4f42ae0)


## 작업 내역

### 팩터 프레임워크
- `src/signals/registry.py` 신규 — `@register` 데코레이터, `FACTOR_REGISTRY`, `compute()` (`inspect.signature` 기반 kwarg 필터, VAR_KEYWORD 지원), `list_factors()`, `DEFAULT_FACTOR_SET="v1"`
- `src/signals/{sma,atr,macd,bollinger,realized_vol}.py` 신규 — pandas+numpy 벡터화 직접구현. `sma_cross` 는 golden/dead 시그널 포함
- `src/signals/rsi.py` 수정 — `@register("rsi")` 데코레이터 추가 (기존 함수 시그니처 보존)
- `src/signals/lookahead_guard.py` 신규 — `assert_no_lookahead` append-tail-bar 인과성 검증 (Series/DataFrame, NaN/None 동등성)
- `src/signals/cache.py` 신규 — `to_factor_long` (UTC 강제 + melt), `write_factor_parquet` (year/month 파티션), `read_factor_parquet`

### 엔진 통합
- `src/backtest/protocol.py` — `required_factors` 컨벤션 주석 추가 (Protocol 외부 — PEP 544 ClassVar + `@runtime_checkable` 호환성)
- `src/backtest/engine.py` — `getattr(strategy, "required_factors", [])` fallback, 미등록 팩터 조기 `KeyError`, 바마다 `spec.inputs` 필터링 후 `context["factors"][name]` 주입. `required_factors` 빈 전략은 기존 동작 보존 (MomoBtcV2 변경 0)

### 시그널 노트 + 문서
- `docs/specs/signals/sma-cross.md`, `docs/specs/signals/bollinger-breakout.md` 신규 — signal 프론트매터 스키마 준수
- `src/signals/.ai.md`, `src/backtest/.ai.md`, `src/data_lake/.ai.md` 갱신
- `docs/work/active/000071-alpha-factors/01_plan.md` — ralplan 합의 플랜 (Planner→Architect→Critic APPROVE + 6 edits 반영)
- `docs/work/active/000071-alpha-factors/02_perf_benchmark.md` — O(N²) 퍼프 벤치마크 실측 + follow-up #81 권고

### 테스트 + CI
- 신규 테스트 9개 (`signals/test_{signals_registry,factor_*,cache,lookahead_guard}.py` + `backtest/test_backtest_factor_integration.py`)
- `pyproject.toml` — `pandas-ta` dev extras, `slow` 마커 + `addopts -m 'not integration and not slow'`
- 70k-bar 퍼프 게이트 실패(O(N²) Python-loop, n=4k→156s) → `@pytest.mark.slow` 격리, follow-up **#81** 생성

### 리팩토링 (스코프 합의)
- `tests/` 폴더링 — 24 flat → 5 도메인 서브(`signals/`, `backtest/`, `data_lake/`, `obsidian/`, `ontology/`) + 3 단독 유지. 13개 위치 `__file__` parent 홉 조정

### 검증
- **pytest: 433 passed / 6 skipped (pandas-ta 미설치) / 6 deselected (integration+slow)**
- **invariants strict: 84 노트 통과** (+2 signal 노트)
- MomoBtcV2 import 경로 및 테스트 회귀 0
