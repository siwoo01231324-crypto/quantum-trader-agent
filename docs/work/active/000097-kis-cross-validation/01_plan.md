# [#97 v2] 메타라벨러 × KIS 다종목 1분봉 pooled 교차 검증 — 구현 계획

> 작성: 2026-04-26 v3 합의 → 2026-04-27 v5 재합의
> v3 (단일 종목 15분봉, 합성 fallback, Phase A/B 분리): Architect ENDORSE_WITH_REVISIONS + Critic APPROVE
> **v5 (다종목 1분봉 pooled, 합성 제거, 단일 Phase): Architect ENDORSE_WITH_REVISIONS + Critic APPROVE**
> 본 plan 은 v5 합의안. v3 산출물 21파일 + 78 테스트는 보존, v5 는 그 위에 확장.

---

## 완료 기준 (이슈 #97 v2 본문 갱신)

### 데이터 인프라
- [ ] KIS 1분봉 호출 (TR 추가 불필요, `interval="1"` 인자만)
- [ ] KOSPI200 universe 사용 (기존 `src/universe/kospi200.py`)
- [ ] **OHLCV_SCHEMA 불변** — VI/단일가/거래량0 메타는 `src/data_lake/ohlcv_filter.py` sidecar 로 부착 (Critic ADR-097-2)
- [ ] `scripts/fetch_kis_backfill.py` 신규 — N종목 × 30일 × 1분봉 일괄 (60-90분 예상, async 옵션)
- [ ] `cron_fetch_kis_daily.py` 다종목 + 1분봉 확장 (backward compat 유지)

### 전략 / 학습 / CV
- [ ] `momo_kis_v1` `interval_min` 인스턴스화 — 1분봉 모드 시 `holding_bars=78`, `periods_per_year=98280`
- [ ] **`TimeBlockGroupKFold` 신규** (Critic ADR-097-1) — `src/ml/cv.py` 에 추가, 기존 `PurgedKFold` 보존
- [ ] **`run_kis_pipeline_pooled` 별도 함수** — 기존 `run_kis_pipeline` 시그니처 불변 (v3 78 테스트 회귀 게이트)
- [ ] 합성 fallback 제거 (실데이터 없으면 명확 에러 + graceful exit). `_make_synthetic_ohlcv` 는 테스트 fixture 로 존치

### 평가 / 비교
- [ ] BTC vs KRX-pool DSR / PR-AUC / Sharpe 병렬 비교
- [ ] **`CrossAssetReport` 에 `n_symbols`, `n_eff` 필드 추가** — `n_eff = N / (1 + (N-1) × ρ_avg)` 보고 (DSR `n_trials=1` 유지)
- [ ] 통계 판정 (채택/기각/재설계) — 합성 결과 기반 "보류" 금지
- [ ] `02_implementation.md` 실데이터 결과 + n_eff + n_symbols 명시

### 후속 이슈
- [ ] cron 6개월+ 누적 후 전체 KOSPI200 재실행 이슈
- [ ] 결과별: 채택→paper live, 기각→메타라벨러 재설계, 재설계→파라미터/feature 재검토

## 개발 체크리스트
- [ ] 테스트 — v3 78 테스트 PASS 회귀 게이트 + 신규 `test_time_block_group_kfold.py` / `test_run_kis_pipeline_pooled.py` / `test_n_eff_correction.py` / `test_fetch_kis_backfill.py` / `test_ohlcv_filter.py`
- [ ] `.ai.md` 최신화 (`src/universe/`, `src/data_lake/`, `src/ml/`, `src/ml/pipelines/`, `src/ml/reporting/`, `docs/work/active/000097-kis-cross-validation/`)
- [ ] `python scripts/check_invariants.py --strict` exit 0

---

## 구현 계획 (v5)

### RALPLAN-DR Summary

#### Principles
1. **실데이터 우선** — 합성 fallback 제거. KIS backfill 1회로 30일 × N종목 실데이터 즉시 확보. 합성 데이터 결과를 가설 채택/기각 근거로 사용 금지.
2. **Cross-section pooling** — 종목간 이벤트 풀링으로 표본 30~100배 확대. 단, 종목간 상관 보정한 `n_eff` 정직 보고.
3. **인프라 재사용 + 회귀 0** — v3 21파일 + 78 테스트 모두 보존. `run_kis_pipeline` 시그니처 불변, 신규 `run_kis_pipeline_pooled` 별도 추가.
4. **종목축 누수 방지** — `PurgedKFold` 만으론 다종목 pooling 시 시장 충격 누수. `TimeBlockGroupKFold` 별도 splitter 도입.
5. **점진적 확장** — 30종목 1차 → cron 누적 6개월+ 후 전체 KOSPI200 + 시간 다양성 검증은 후속 이슈.

#### Decision Drivers
1. **v3의 ~2 이벤트 문제 해소** — 1분봉 × 다종목 pooling 으로 통계적 유의 표본 확보.
2. **KIS API 30일 제약 회피** — backfill 1회 + 내일부터 cron 누적.
3. **합성 fallback 의 과학적 무의미** — v3 "판정 보류"를 v5 실데이터 판정으로 전환.

#### Selected Option: 1분봉 다종목 pooled (Architect ENDORSE)
- 30종목 × 30일 × 1분봉 ≈ 585,000 bars. 이벤트 수백~수천.
- Pros: 즉시 통계 판정, 합성 제거, 추가 TR 불필요.
- Cons: backfill ~수천 요청 (60-90분), 종목간 상관 → n_eff < N, 30일 단일 레짐 한계.

#### Alternatives Considered (기각)
- **Option B (15분봉 + 다종목만)**: 30종목 × 30일 × 15분봉 ≈ 39,000 bars. 이벤트 밀도 1/15. 풀링 효과 제한.
- **Option C (1분봉 단일 종목)**: 1종목 × 30일 × 1분봉 ≈ 11,700 bars. cross-section 부재로 일반화 불가.
- **Option D (v3 유지 + 후속 이슈 분리)**: 기각 — 인공적 분할, 합성 결과로는 가설 검증 불가.

---

### Guardrails

**Must Have**
- KRX `costs_bps=26.0` (BUY 1.5 + SELL 24.5)
- 1분봉: `holding_bars=78` (≈1.3시간, intraday momentum 반감기), `periods_per_year=98280` (390 × 252)
- 15분봉 모드 (default): `holding_bars=26`, `periods_per_year=6552` 유지 — backward compat
- DSR `n_trials=1` 유지 (deflation 미미 명시), 별도 `n_eff` 메트릭 보고
- `n_eff = pool_size / (1 + (pool_size-1) × ρ_avg)`, ρ_avg 는 학습 시 종목간 일수익률 평균 상관
- `TimeBlockGroupKFold` — 다종목 pooling 시 시간 블록 단위 fold 분할
- `PurgedKFold` 보존 — 단일 종목 path (BTC + 기존 momo_kis_v1 single) 회귀 방지
- v3 78 ml 테스트 + 5 data_lake 테스트 전부 PASS — CI gate
- KIS 토큰 부재 시 graceful exit 0 + 안내 (cron 안전, backfill 안내)
- 종목ID feature 또는 ATR-normalized 정규화 (메타라벨러 학습 시)
- VI/단일가/거래량0 봉 학습에서 제외 (백테스트는 마킹 포함)
- 모든 신규 디렉토리에 `.ai.md`

**Must NOT Have**
- v3 21파일 시그니처 변경 — 회귀 위험
- `OHLCV_SCHEMA` 핵심 필드 변경 — `validate_schema` 호환성
- 합성 fallback 강제 의존 — 실데이터 없으면 명확 에러 + 종료
- VI 포함/제외 두 트랙 비교 — 과잉
- `PurgedKFold` 만으로 다종목 pooling — 종목축 누수
- cherry-pick window — 30일 전체 사용
- LLM 의 주문/리스크 결정 개입
- 모델 아티팩트 git 커밋

---

### Task Flow

```
Group A (Data Infra)
  A1: krx_pool.py (universe wrapper)              ←┐
  A2: ohlcv_filter.py (sidecar VI/거래량0 필터)    ← │ 독립
  A3: fetch_kis_backfill.py (graceful + async)    ← │
  A4: cron_fetch_kis_daily.py 다종목 확장          ← │
  A5: TimeBlockGroupKFold (src/ml/cv.py)           ←┘
        ↓
Group B (Strategy)
  B1: momo_kis_v1 interval_min 인스턴스화          (A1 의존)
        ↓
Group C (Pipeline & Eval)                         (A + B 의존)
  C1: run_kis_pipeline_pooled 신규 함수
  C2: cross_asset_compare n_symbols/n_eff 필드
  C3: bench/train --multi-symbol flag
  C4: 신규 테스트 5종 + v3 78 회귀 게이트
        ↓
Group D (Result & Finalize)                       (C 의존)
  D1: backfill 1회 실행 (60-90분 또는 graceful skip)
  D2: 02_implementation.md 실데이터 재생성
  D3: .ai.md + 후속 이슈 + 불변식
```

**병렬화**: A1~A5 모두 독립. C1~C3 도 독립. 3 worker 권장 분담:
- worker-1: A1, A2, A5 (data infra + CV splitter)
- worker-2: A3, A4, B1 (fetch infra + strategy)
- worker-3: C1, C2, C3, C4 → D2, D3

---

### Group A: Data Infra

#### A1: `src/universe/krx_pool.py` (신규)
- `get_pool_codes(n: int = 30, *, sectors: list[str] | None = None, seed: int = 42) -> list[str]`
- KOSPI200 sector 균등 샘플링, seed 고정 재현성, 005930 항상 포함.
- `tests/universe/test_krx_pool.py` 신규.

**Verification**: `pytest tests/universe/test_krx_pool.py -v` PASS, `len(get_pool_codes(30))==30`, `"005930" in codes`.

#### A2: `src/data_lake/ohlcv_filter.py` (신규, schema 불변)
- `filter_noise_bars(df, *, exclude_vi=True, exclude_single_price=True, exclude_zero_volume=True) -> pd.DataFrame`
- `mark_noise_bars(df) -> pd.DataFrame` — `_noise` 컬럼 부착
- VI/단일가는 KIS 응답 필드 미지원 시 `False` default. 거래량 0 봉은 `volume == 0`.
- 입력 DataFrame 의 OHLCV_SCHEMA 외 컬럼 (sidecar) 으로만 동작 → `validate_schema` 호환성 0 영향.
- `tests/data_lake/test_ohlcv_filter.py` 신규.

**Verification**: `pytest tests/data_lake/test_ohlcv_filter.py -v`, `validate_schema(df)` PASS 유지.

#### A3: `scripts/fetch_kis_backfill.py` (신규)
- argparse: `--n-symbols 30 --interval 1m --lake-dir lake/ --dry-run --seed 42 --sleep-between 0.6 --async-concurrency 1`
- `--async-concurrency > 1` 시 asyncio + bounded semaphore (KIS 토큰당 2 req/s 한도).
- 토큰 부재 시 graceful exit 0 + 합성 fixture 안내 (사용자 환경변수 점검 가이드).
- 진행률: `[3/30] 005930 fetched 11700 bars (30 days)`.
- 예상 시간: 60-90분 (sync) / 30-45분 (async concurrency=2).
- `tests/data_lake/test_fetch_kis_backfill.py` 신규 — mock 기반.

**Verification**: `python scripts/fetch_kis_backfill.py --dry-run --n-symbols 5` exit 0, mock 테스트 PASS.

#### A4: `scripts/cron_fetch_kis_daily.py` 확장
- `--n-pool 30 --interval 1m` 옵션 추가. 기존 `--symbol 005930 --interval 15m` default 유지.
- 다종목 루프, 종목간 sleep, dry-run 출력에 종목 리스트.
- 토큰 부재 graceful exit 0 (기존 패턴 유지).

**Verification**: dry-run 다종목 + 단일 종목 둘 다 exit 0.

#### A5: `src/ml/cv.py::TimeBlockGroupKFold` (신규, ADR-097-1)
- `class TimeBlockGroupKFold:` — 시간 인덱스 기준 fold 분할, 같은 시간 블록의 모든 종목은 동일 fold 배치.
- 시그니처 `split(X, t1) -> Iterator[(train_idx, test_idx)]` — `PurgedKFold` 와 동일 (drop-in 가능).
- 기존 `PurgedKFold` 클래스 **불변** — 단일 종목 path / BTC 회귀 방지.
- `tests/ml/test_time_block_group_kfold.py` 신규:
  - 합성 다종목 데이터 → fold 별로 같은 시간 블록 종목들이 함께 묶이는지 검증
  - train/test 의 시간 범위가 겹치지 않는지 검증
  - `PurgedKFold` 와 `split()` 시그니처 호환 검증

**Verification**: `pytest tests/ml/test_time_block_group_kfold.py -v` PASS.

---

### Group B: Strategy

#### B1: `momo_kis_v1.py` `interval_min` 인스턴스화
- `__init__` 에 `interval_min: int = 15` 추가. `INTERVAL_MIN` 클래스 상수 → 인스턴스 변수.
- `_is_my_bar_boundary` 에서 `self.interval_min` 사용.
- 1분봉 모드 도입: `interval_min=1` 시 `holding_bars=78` (Architect M3), `periods_per_year=98280`.
- 기존 default (`symbol="005930"`, `interval_min=15`) 보존 → 회귀 0.

**Verification**: 기존 `tests/backtest/test_momo_kis_v1.py` PASS, 신규 `interval_min=1` 케이스 추가 테스트.

---

### Group C: Pipeline & Eval

#### C1: `run_kis_pipeline_pooled` 신규 함수
- `src/ml/pipelines/kis_cross_validation.py` 에 신규 함수 추가:
  ```python
  def run_kis_pipeline_pooled(
      symbols: list[str],
      lake_dir: Path,
      output_dir: Path,
      *,
      interval: str = "1m",
      holding_bars: int = 78,
      costs_bps: float = 26.0,
      use_time_block_cv: bool = True,
      ohlcv_filter_config: dict | None = None,
      ...
  ) -> tuple[SavedArtifact, dict]:
  ```
- 종목별 `load_ohlcv_from_lake` → `ohlcv_filter.filter_noise_bars` → events/features 수집 (종목 코드 컬럼 부착) → concat → label → CV (`use_time_block_cv=True` 시 `TimeBlockGroupKFold`) → `train_and_save`.
- 합성 fallback 호출 없음. lake 부재 종목은 skip + warning. 전 종목 부재 시 명확 에러.
- 기존 `run_kis_pipeline` (단일 종목) 시그니처/동작 **불변**.
- 양수 라벨 비율 < 0.2 시 경고. 이벤트 0건 graceful exit code 3.

**Verification**: `pytest tests/ml/test_run_kis_pipeline_pooled.py -v` PASS, 기존 `test_kis_pipeline.py` 2 테스트 PASS (회귀).

#### C2: `cross_asset_compare.py` `n_symbols` / `n_eff` 필드
- `CrossAssetReport` 에 추가:
  - `n_symbols: int = 1`
  - `n_eff: float = 0.0` (effective sample size, 0 = N/A)
  - `rho_avg: float = 0.0` (종목간 평균 상관)
- `judge_hypothesis` — n_events 임계 시 `n_eff` 도 고려.
- `render_markdown` — n_symbols > 1 시 종목 리스트 + n_eff 명시.
- `n_eff` 계산 helper: `compute_effective_n(pool_size: int, rho_avg: float) -> float`.

**Verification**: `pytest tests/ml/test_cross_asset_compare.py -v` 기존 4 시나리오 PASS + 신규 `test_n_eff_correction.py` PASS.

#### C3: `bench_metalabeler_kis.py` / `train_metalabeler_kis.py` 확장
- 두 스크립트에 `--multi-symbol` flag + `--n-symbols 30 --interval 1m` 인자 추가.
- `--multi-symbol` 지정 시 `get_pool_codes(n)` → `run_kis_pipeline_pooled()` 호출.
- 미지정 시 기존 동작 (단일 종목 005930) 유지 — backward compat.
- `bench` 출력 JSON 에 `n_symbols`, `n_eff`, `rho_avg` 포함.

**Verification**: `--help` 출력 + `--multi-symbol --n-symbols 5` (dry-run 가능 시) 동작.

#### C4: 테스트 확장 (5 신규 + v3 78 회귀)
- `tests/ml/test_time_block_group_kfold.py` (A5)
- `tests/ml/test_run_kis_pipeline_pooled.py` (C1)
- `tests/ml/test_n_eff_correction.py` (C2)
- `tests/data_lake/test_fetch_kis_backfill.py` (A3)
- `tests/data_lake/test_ohlcv_filter.py` (A2)
- `tests/universe/test_krx_pool.py` (A1)
- **회귀 게이트**: `pytest tests/ml/ tests/data_lake/ tests/universe/ -v` 전부 PASS.

---

### Group D: Result & Finalize

#### D1: backfill 1회 실행
- `python scripts/fetch_kis_backfill.py --n-symbols 30 --interval 1m`
- 60-90분 예상 (sync) / 30-45분 (async)
- KIS 인증 토큰 부재 시 graceful skip + 사용자 안내. 이 경우 D2 는 "데이터 미적재 → 후속 토큰 설정 + 재실행" 으로 명시.
- 실행 결과 (총 봉 수, 종목별 결측, VI/거래량0 분포) 02_implementation.md 에 기록.

#### D2: `02_implementation.md` 실데이터 재생성
- `python scripts/bench_metalabeler_kis.py --multi-symbol --n-symbols 30 --interval 1m` 실행
- `python scripts/cross_asset_compare.py` 자동 갱신
- 5개 필수 섹션 + n_symbols + n_eff + rho_avg 명시
- 합성 fallback 관련 문구 제거
- 토큰 부재로 데이터 미적재 시 "데이터 적재 보류, 토큰 설정 후 재실행" 명시 (보류 ≠ 가설 보류, 인프라 보류)

#### D3: 마무리
- `.ai.md` 갱신: `src/universe/`, `src/data_lake/`, `src/ml/`, `src/ml/pipelines/`, `src/ml/reporting/`, `docs/work/active/000097-kis-cross-validation/`
- 후속 이슈 생성:
  - "cron 6개월+ 누적 후 전체 KOSPI200 교차 검증 재실행" (시간 다양성)
  - 판정 결과별: 채택 → paper live (#80 연동) / 기각 → 메타라벨러 재설계 / 재설계 → 파라미터 재검토
- 연구 노트: `docs/specs/meta-labeling/kis-cross-validation.md` 갱신 (v5 다종목 풀 결과 반영)
- `python scripts/check_invariants.py --strict` exit 0

---

### 산출물 전체 목록 (v5)

| 파일 | 처리 | 그룹 |
|------|------|------|
| `src/universe/krx_pool.py` | 신규 | A1 |
| `src/data_lake/ohlcv_filter.py` | 신규 (schema 불변) | A2 |
| `scripts/fetch_kis_backfill.py` | 신규 (graceful + async) | A3 |
| `scripts/cron_fetch_kis_daily.py` | 변경 (다종목 + 1분봉) | A4 |
| `src/ml/cv.py::TimeBlockGroupKFold` | 변경 (클래스 추가, PurgedKFold 보존) | A5 |
| `src/backtest/strategies/momo_kis_v1.py` | 변경 (interval_min 인스턴스화) | B1 |
| `src/ml/pipelines/kis_cross_validation.py::run_kis_pipeline_pooled` | 변경 (함수 추가, 기존 보존) | C1 |
| `src/ml/reporting/cross_asset_compare.py` | 변경 (n_symbols, n_eff) | C2 |
| `scripts/{bench,train}_metalabeler_kis.py` | 변경 (--multi-symbol) | C3 |
| `scripts/cross_asset_compare.py` | 변경 (n_symbols 처리) | C2 |
| **신규 테스트 6종** | 신규 | A1/A2/A3/A5/C1/C2 |
| `docs/work/active/000097-kis-cross-validation/02_implementation.md` | 재생성 (실데이터) | D2 |
| `src/universe/.ai.md` / `src/data_lake/.ai.md` / `src/ml/.ai.md` 등 | 변경 | D3 |
| `docs/specs/meta-labeling/kis-cross-validation.md` | 변경 | D3 |
| **v3 산출물 21파일 + 78 테스트** | **불변 (회귀 게이트)** | M1 |
| **`OHLCV_SCHEMA` 핵심 필드** | **불변** | M4 |

---

### 성공 기준 (v5)

1. `python -m pytest tests/ml/ tests/data_lake/ tests/universe/ -v` — **v3 78 + 신규 6+ 테스트** 전부 PASS (회귀 0).
2. `python scripts/check_invariants.py --strict` — exit 0.
3. `02_implementation.md` 실데이터 결과 + n_symbols + n_eff + rho_avg 명시. 합성 결과 기반 판정 없음.
4. `lake/ohlcv/freq=1m/` 에 30종목 데이터 존재 (또는 토큰 부재 시 graceful 안내 기록).
5. DSR 기반 판정 (채택/기각/재설계) — 데이터 적재 시 실질 판정 산출.
6. 후속 이슈 1건+ 생성.
7. `momo_kis_v1` 기존 default 동작 보존 (`symbol="005930"`, `interval_min=15`).
8. `run_kis_pipeline` 시그니처 불변, 신규 `run_kis_pipeline_pooled` 추가.
9. `OHLCV_SCHEMA` 핵심 필드 불변, sidecar 필터 패턴.

---

### ADR (Architecture Decision Records)

#### ADR-097-v5: 1분봉 다종목 pooled 전환
- **Decision**: 1분봉 다종목 pooled cross-validation + 합성 fallback 제거 + backfill 인프라 + sidecar 필터.
- **Drivers**: v3 단일 종목 ~2 이벤트 통계 판정 불가, 합성의 과학적 무의미, KIS 30일 제약 회피.
- **Alternatives**:
  - (A) 1분봉 다종목 pooled **(선택)** — 표본 30~100배, 즉시 판정 가능.
  - (B) 15분봉 + 다종목 — 이벤트 밀도 1/15, 풀링 효과 제한.
  - (C) 1분봉 단일 종목 — cross-section 부재.
  - (D) v3 유지 + 후속 분리 — 인공적 분할.
- **Why Chosen**: 표본 부족 → 판정 불가 문제를 직접 해결. 1분봉 × 다종목 = 30일 윈도우 내 통계적 유의 판정.
- **Consequences**: backfill 60-90분, n_eff < N (코스피 종목간 상관), 30일 단일 레짐 한계 → 후속 이슈.

#### ADR-097-1: TimeBlockGroupKFold (Critic 권고)
- **Context**: 다종목 pooling 시 `PurgedKFold` 시간축 purge 만 → 같은 시각 다른 종목 train/test 혼재 → 시장 충격 누수 → CV 과대추정.
- **Decision**: `TimeBlockGroupKFold` 신규 클래스 추가, 시간 블록 단위 fold 분할. 기존 `PurgedKFold` 보존 (단일 종목 path).
- **Consequences**: 단일 종목 v3 회귀 0, 다종목 CV 신뢰도 확보.

#### ADR-097-2: OHLCV_SCHEMA 불변 + sidecar 필터 (Critic 권고)
- **Context**: VI/거래량0 메타 필요. `validate_schema` 가 extra column 에 에러.
- **Decision**: `ohlcv_filter.py` 에서 DataFrame 동적 부착, `OHLCV_SCHEMA` 불변.
- **Consequences**: 기존 data_lake 코드 영향 0, 필터 단일 책임 분리.

---

### v3 → v5 변경 사유 (v3 plan 보존)

v3 는 단일 종목(005930) 15분봉 + 합성 fallback + Phase A/B 분리 모델. 21파일 작성 + 78/78 tests green + 불변식 통과 완료. Architect ENDORSE_WITH_REVISIONS + Critic APPROVE 합의.

**v5 로 전환한 사유**:
1. 사용자 의견: "Phase B 후속 이슈 분리는 인공적. 어차피 만들 인프라면 같은 이슈에서 진행."
2. 사용자 통찰: "lake 가 비어있던 건 데이터가 없어서가 아니라 fetch 안 해서. KIS API 는 과거 30일 fetch 가능."
3. 사용자 통찰: "분봉 700개는 시간봉 한 달 수준. 1분봉 + 다종목으로 표본 폭증 가능."
4. 코드베이스 실측: KIS 1분봉 이미 지원, KOSPI200 universe 이미 존재, 새 작업량 예상보다 적음.

**v3 자산 처리**: 21파일 + 78 테스트 모두 보존. v5 는 그 위에 확장 (별도 함수, 별도 splitter, sidecar 필터). 회귀 0 보장.

| 항목 | v3 | v5 |
|------|----|----|
| 봉 주기 | 15분봉 단일 | 1분봉 (단일/다종목 모두 지원) |
| 종목 수 | 005930 단일 | KOSPI200 30종목 풀 |
| 표본 규모 | ~2 이벤트 | 30~100배 확보 |
| 합성 fallback | 의존 | 제거 (테스트 fixture 만 존치) |
| Phase 구분 | A→B 분리 | 단일 Phase |
| n_trials / n_eff | 1 / N/A | 1 / pool 보정 |
| holding_bars | 26 (15m) | 78 (1m) / 26 (15m, default) |
| CV splitter | PurgedKFold only | PurgedKFold + TimeBlockGroupKFold |

---

### Open Questions 답변 (Architect 라운드 1)

1. **n_trials 산정**: `n_trials=1` 유지 (DSR 정의 부합). pool size 는 `n_eff` 별도 메트릭으로 보고.
2. **VI/단일가/거래량0**: 제외 only (두 트랙 비교 과잉).
3. **종목축 누수**: `TimeBlockGroupKFold` 필수 (ADR-097-1).
4. **holding_bars**: 1분봉 시 78, 15분봉 시 26 (default).
5. **backfill 시간**: 60-90분 (sync) / 30-45분 (async). 토큰 부재 시 graceful exit 0 + 안내.
6. **v3 회귀**: 78 ml + 5 data_lake 테스트 PASS CI gate. `_make_synthetic_ohlcv` 테스트 fixture 존치.
