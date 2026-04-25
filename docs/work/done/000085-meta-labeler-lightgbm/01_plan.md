# [#85] 메타라벨링 레이어 (LightGBM 2차 필터 + purged CV + walk-forward) — 구현 계획

> 작성: 2026-04-24 · 실측 기반 (Explore subagent 결과 반영)
> 본 문서는 `/plan 85` 로 확장된 **구체 구현 계획** 이다. `/remind-issue` AC 체크 기준.

---

## 완료 기준 (AC — 이슈 body §"완료 기준")

- [ ] `src/ml/` 모듈 4개 파일 구현 + 단위 테스트 (triple-barrier 라벨링 · purged CV 정확도 · walk-forward 경로)
- [ ] `momo-btc-v2` 에 메타라벨러 래핑 옵션 통합, **기존 bypass 경로 회귀 없음** (기존 테스트 그대로 pass)
- [ ] Purged K-fold + embargo 로 CV 스코어 JSON 리포트
- [ ] 메타라벨러 on/off Sharpe 비교 — **on 경로가 Sharpe ≥ off + 0.2 또는 MDD 10%p 이상 개선** (아니면 해당 전략 disable 유지)
- [ ] `docs/specs/ml/meta-labeling.md` 스펙 + `docs/background/` 메타라벨링 이론 노트 1개
- [ ] `src/ml/.ai.md` 신규 생성, `src/backtest/strategies/.ai.md` 업데이트
- [ ] 테스트 코드 포함
- [ ] `docs/specs/ml/.ai.md` 신규 디렉토리 생성
- [ ] 불변식 위반 없음 (LLM 미개입 · 룩어헤드 가드 · 프론트매터 스키마)

### 불변식 게이트
- [ ] LLM 금지 (CLAUDE.md #6) — LightGBM 결정적 학습만, `take_probability → win_probability` 는 `predict_proba` 순수 매핑
- [ ] 룩어헤드 금지 — #71 가드 재사용 + CV 는 purged + embargo
- [ ] 훈련 라벨에 거래비용(세금·수수료·슬리피지) 반영

---

## 실측 레포 상태 요약 (2026-04-24)

| 항목 | 상태 | 근거 |
|------|------|------|
| `src/ml/` | ❌ 없음 → 신규 | `ls src/` |
| `src/backtest/strategies/momo_btc_v2.py` on_bar | ✅ 라인 151 직전이 Signal emit 지점 | Explore 리포트 §2 |
| `Signal` 인터페이스 (`win_probability`/`confidence`/`expected_return`) | ✅ kw-only Optional 필드 존재 | `src/backtest/protocol.py:22-30` |
| SP-3 0차 팩터 IC 게이트 코드 | ❌ 미구현 (문서만) — 본 이슈와 무관, 의존하지 않음 | `src/signals/` 탐색 |
| `lightgbm` 의존성 | ❌ 미설치 → 추가 필요 | `pyproject.toml` L5-18 |
| `docs/specs/ml/` | ❌ 없음 → 신규 | `ls docs/specs/` |
| note-schemas type 13종 중 `ml-model` 타입 | ❌ 없음 → `spec-architecture` 재사용 | `grep ^type docs/schemas/note-schemas.md` |
| `tests/backtest/test_momo_btc_v2.py` | ✅ 171줄, `_make_ohlcv + run_backtest` 패턴 | Explore §3 |
| #87 선반영 항목 | P1/P3/P4/P5a/P5b/P6/P7 구현됨 — 메타라벨러 범위와 **겹치지 않음** | `git log --grep "#87"` |

---

## 구현 계획

### Part A — `src/ml/` 신규 모듈 (AC1, AC8, 불변식 게이트)

#### A1. 모듈 스켈레톤 + 의존성 추가
**파일**:
- `src/ml/__init__.py` (신규) — `MetaLabeler`, `triple_barrier_label`, `PurgedKFold`, `WalkForwardSplitter` public export
- `src/ml/.ai.md` (신규) — 모듈 목적·구조·역할·호출 경로·불변식·Gotcha

**의존성**:
- `pyproject.toml` `[project.dependencies]` 에 `"lightgbm>=3.3,<5"` 추가 (main deps — 실제 런타임에서 호출하므로)
- `requirements.txt` 가 있으면 동기화

**검증**: `pip install -e .` 후 `python -c "import lightgbm; import src.ml"` 성공

---

#### A2. `src/ml/labeling.py` — Triple-barrier labeling
**기능**: 진입 시점 T 기준, (+tp, −sl, 타임컷 T+H) 3중 배리어 중 먼저 닿는 배리어로 이진 라벨 {0, 1} 생성. López de Prado Ch.3.

**공개 API**:
```python
def triple_barrier_label(
    prices: pd.Series,          # close price, index=datetime
    events: pd.DataFrame,       # index=entry_ts, cols: [side:int{+1,-1}, t1:datetime]
    tp: pd.Series | float,      # 익절 폭 (σ·k 또는 고정)
    sl: pd.Series | float,      # 손절 폭
    costs_bps: float = 0.0,     # 거래비용 (bps) — 라벨 전 수익률에서 차감
) -> pd.DataFrame:
    """
    Returns: cols=[label:int{0,1}, ret:float, barrier:str{'tp','sl','t1'}, t_touch:datetime]
    label=1 iff 비용반영 수익률 > 0 AND 익절/타임컷으로 종료
    """
```

**불변식**:
- `t_touch > entry_ts` 엄격 부등식 (bar t 에서 관측한 정보로 bar t+1 배리어 판정 금지)
- `prices` 가 events 구간 전체 커버하지 않으면 `ValueError`

**검증**: 단위 테스트 (아래 C1)

---

#### A3. `src/ml/cv.py` — Purged K-fold + embargo
**기능**: 시계열 리키지 방지 CV. López de Prado Ch.7.

**공개 API**:
```python
class PurgedKFold:
    def __init__(self, n_splits: int = 5, embargo_frac: float = 0.01): ...
    def split(
        self,
        X: pd.DataFrame,       # index=datetime
        t1: pd.Series,         # 각 샘플의 라벨 확정 시점 (triple_barrier 의 t_touch)
    ) -> Iterator[tuple[np.ndarray, np.ndarray]]:
        """purge: test fold 시점과 겹치는 train 샘플 제거.
           embargo: test fold 직후 `embargo_frac * N` 구간을 train 에서 배제."""
```

**불변식**:
- train index ∩ test index = ∅ (purge)
- train 샘플의 `t1` 이 test fold 의 any sample time range 와 겹치면 drop
- `n_splits < 2` 또는 `embargo_frac < 0` 시 `ValueError`

---

#### A4. `src/ml/meta_labeler.py` — LightGBM 2차 필터
**기능**: 기본 전략 신호(`side`)를 입력받아 "이 트레이드를 잡을지" 이진분류. `take_probability` 를 `win_probability` 로 매핑.

**공개 API**:
```python
@dataclass
class MetaLabelerConfig:
    num_boost_round: int = 500
    early_stopping_rounds: int = 50
    learning_rate: float = 0.05
    num_leaves: int = 31
    min_data_in_leaf: int = 50
    feature_fraction: float = 0.8
    bagging_fraction: float = 0.8
    bagging_freq: int = 5
    lambda_l2: float = 0.1
    random_state: int = 42   # 결정적 학습 (CLAUDE.md #6 불변식)

class MetaLabeler:
    def __init__(self, config: MetaLabelerConfig = MetaLabelerConfig()): ...
    def fit(
        self,
        X_train: pd.DataFrame,   # 피처 (#71 레지스트리 값 + 신호 메타)
        y_train: pd.Series,      # triple_barrier label
        X_val: pd.DataFrame | None = None,
        y_val: pd.Series | None = None,
    ) -> "MetaLabeler": ...
    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:  # shape (N, 2)
    def win_probability(self, X: pd.DataFrame) -> np.ndarray:  # = predict_proba[:, 1]
    def feature_importance(self, method: str = "permutation") -> pd.Series: ...
    def save(self, dir_path: Path) -> Path:   # model.lgbm + manifest.json 저장
    @classmethod
    def load(cls, dir_path: Path) -> "MetaLabeler": ...
```

**manifest.json 필수 필드**:
```json
{
  "strategy_id": "momo-btc-v2",
  "trained_at": "2026-04-24T12:34:56Z",
  "git_sha": "994ea11...",
  "feature_names": ["rsi_14", "atr_14", "divergence_magnitude", ...],
  "label_config": {"tp_sigma_k": 2.0, "sl_sigma_k": 1.5, "holding_bars": 24, "costs_bps": 4.0},
  "cv_score": {"mean_accuracy": 0.567, "std": 0.021, "n_folds": 5, "embargo_frac": 0.01},
  "training_window": {"start": "2025-04-01", "end": "2026-04-01"}
}
```

**불변식**:
- LightGBM `deterministic=True`, `force_col_wise=True` 설정으로 재현성 강제
- `random_state` · `seed` 모두 고정
- `predict_proba` 결과는 그대로 `win_probability` 매핑. 후처리(캘리브레이션)는 별도 이슈

---

#### A5. `src/ml/walkforward.py` — Expanding / rolling walk-forward
**기능**: 학습 윈도를 시간에 따라 전진시키며 주기적 재학습 시뮬레이션.

**공개 API**:
```python
@dataclass
class WalkForwardConfig:
    mode: Literal["expanding", "rolling"] = "expanding"
    train_window: pd.Timedelta | int       # bar count or timedelta
    test_window: pd.Timedelta | int
    step: pd.Timedelta | int               # 리트레인 간격 (월/주/일)
    min_train_samples: int = 500

class WalkForwardSplitter:
    def __init__(self, config: WalkForwardConfig): ...
    def split(self, index: pd.DatetimeIndex) -> Iterator[tuple[np.ndarray, np.ndarray]]: ...
```

**검증**: 단위 테스트로 expanding vs rolling 동작 차이, step 간격 준수, min_train_samples 하한 확인

---

#### A6. 모델 아티팩트 레이아웃
**디렉토리**: `models/<strategy_id>/<YYYYMMDD-HHMMSS>/`
- `model.lgbm` — LightGBM booster (gzip)
- `manifest.json` — 위 A4 스키마
- `cv_report.json` — purged K-fold fold-by-fold 결과
- `feature_importance.json` — permutation importance

**`.gitignore`**: `models/` 전체 무시. 실 아티팩트는 CI 혹은 별도 저장소. (본 PR 에는 디렉토리 + `.gitignore` 엔트리만)

---

### Part B — 전략 통합 (`momo-btc-v2`) (AC2)

#### B1. `src/backtest/strategies/momo_btc_v2.py` 래핑
**훅 포인트**: on_bar 라인 151 직전 (Signal 생성 직전)

**변경 방식** (기존 bypass 기본값 보장):
```python
def __init__(self, ..., metalabeler: Optional[MetaLabeler] = None, metalabeler_threshold: float = 0.5):
    ...
    self._metalabeler = metalabeler
    self._metalabeler_threshold = metalabeler_threshold

def on_bar(self, bar, history, context) -> Signal:
    # ... 기존 로직 (divergence·confidence 계산) ...

    # --- 메타라벨러 훅 (bypass default) ---
    if self._metalabeler is not None:
        feat = self._extract_metalabeler_features(bar, history, context)
        p_take = float(self._metalabeler.win_probability(feat)[0])
        if p_take < self._metalabeler_threshold:
            return Signal(action="hold", size=0.0, reason="metalabeler_reject", win_probability=p_take)
        signal.win_probability = p_take
    # -----------------------------------

    return signal
```

**핵심**:
- `metalabeler=None` 이 기본값 → 기존 테스트 시그니처 변경 없음, 회귀 없음
- `metalabeler_threshold` 는 opt-in 파라미터
- `win_probability` 는 LightGBM 출력을 **그대로** 주입 (사람 손 개입 금지)

#### B2. `context` 인터페이스 유지
기존 `context: dict` 에 새 키 추가 없음. 메타라벨러는 전략 객체 내부에 보유. 이유: orchestrator 가 context 를 다룰 필요 없이 전략 생성 시 주입 가능.

#### B3. `docs/specs/strategies/momo-btc-v2.md` 업데이트
- `## 메타라벨링 (선택)` 섹션 추가 — 사용법·기대 효과·비활성 시 기본값

---

### Part C — 테스트 (AC7)

#### C1. 단위 테스트 (신규)
| 파일 | 대상 | 케이스 |
|------|------|--------|
| `tests/ml/test_labeling.py` | triple_barrier_label | tp/sl/t1 각 배리어 먼저 터치 / 비용 반영 / 룩어헤드 (t_touch > entry_ts) / prices 누락 시 ValueError |
| `tests/ml/test_cv.py` | PurgedKFold | purge 동작 (overlap drop) / embargo 동작 / train·test 비교 / n_splits <2 시 ValueError |
| `tests/ml/test_meta_labeler.py` | MetaLabeler | fit→predict_proba 형태 / save·load round-trip / 결정적 재현성 (seed 동일 시 동일 결과) / feature_importance 반환 |
| `tests/ml/test_walkforward.py` | WalkForwardSplitter | expanding vs rolling fold 수 / step 간격 / min_train_samples 준수 |

각 테스트 파일에 `tests/ml/__init__.py` 생성.

#### C2. 통합 — momo-btc-v2 회귀 방지
| 파일 | 케이스 |
|------|--------|
| `tests/backtest/test_momo_btc_v2.py` (기존) | 그대로 pass 해야 함 (metalabeler=None 기본값) |
| `tests/backtest/test_momo_btc_v2_metalabeler.py` (신규) | metalabeler 주입 시 임계값 이상/이하 동작 / `metalabeler_reject` reason / win_probability 유지 |

#### C3. on/off 비교 백테스트 (검증 증거용, AC4)
**스크립트**: `scripts/bench_metalabeler_btc.py` (신규)
- BTC 15m 1년+ 실데이터 (fixtures 또는 별도 다운로드)
- on: MetaLabeler 주입, off: bypass
- 출력: `docs/work/active/000085-meta-labeler-lightgbm/02_implementation.md` 에 표 기록
  - 컬럼: Sharpe, Sortino, MDD, 승률, 평균 보유바, 거래수, turnover
- `@pytest.mark.slow` 마커. CI 기본 제외.

**통과 기준 (AC4)**: on Sharpe ≥ off Sharpe + 0.2 **또는** on MDD ≤ off MDD − 10%p. 미달 시 해당 전략에 대해 disable 유지하고 원인 분석을 02_implementation.md 에 기록.

---

### Part D — 문서 (AC5, AC6, AC8)

#### D1. `src/ml/.ai.md` (신규)
구조:
- 목적: "2차 메타라벨링 필터 — 규칙 기반 전략 신호의 false positive 쳐내기"
- 파일별 책임 (labeling/cv/meta_labeler/walkforward)
- 외부 의존성 (lightgbm, scikit-learn, numpy, pandas)
- 호출 경로: 전략 `on_bar` 에서 `MetaLabeler.win_probability()` 호출
- 볼트 사용 여부: **사용 안 함** (이유: 순수 수치 계산 모듈, Obsidian MCP 불필요) — CLAUDE.md "새 에이전트 추가 시 볼트 연결 필수" 의 예외 근거 기록
- Gotcha: purged CV timestamp 경계 / 비용 반영 라벨 / deterministic seed

#### D2. `src/backtest/strategies/.ai.md` (업데이트)
- "메타라벨링 훅 추가됨 (#85)" 섹션 추가
- `metalabeler=None` 기본값 불변식 명시 (전략 시그니처 확장 시 default 유지 규칙)

#### D3. `docs/specs/ml/.ai.md` (신규)
- 목적: "ML 모델 스펙 디렉토리"
- 파일 리스트
- 프론트매터 규칙 (type=spec-architecture 재사용, 이유: note-schemas 에 `ml-model` 타입 없음)

#### D4. `docs/specs/ml/meta-labeling.md` (신규)
프론트매터:
```yaml
---
type: spec-architecture
id: meta-labeling
title: 메타라벨링 레이어 (LightGBM 2차 필터)
owner: siwoo01231324-crypto
created: 2026-04-24
---
```
본문 섹션:
1. 목적 · 배경 · 왜 메타라벨링인가
2. 인터페이스 (`MetaLabeler` API · `triple_barrier_label` · `PurgedKFold`)
3. 학습 파이프라인 다이어그램 (Mermaid)
4. 피처 카탈로그 링크 ([[13-feature-alpha-catalog]])
5. CV 프로토콜 ([[22-validation-protocol]])
6. 아티팩트 레이아웃 (`models/<strategy_id>/<ts>/`)
7. 통합 가이드 (전략 측 훅 예시)
8. 실패 모드 · 롤백 기준

#### D5. `docs/background/` 메타라벨링 이론 노트 (신규)
파일: `docs/background/35-meta-labeling-lopez-de-prado.md` (다음 번호 사용)
프론트매터 `type: research`
내용: López de Prado Ch.3 요약 (triple-barrier, meta-labeling, AFML §7 purged CV), 본 레포 적용 맥락, 참고 문헌(출처 명시 — CLAUDE.md §"조사·리서치 규칙")

#### D6. 프론트매터 스키마 확장 여부
- 현 13종 type 중 `spec-architecture` 로 충분 → 추가 없음
- 만약 `ml-model` 타입 추가 시 `docs/schemas/note-schemas.md` + `scripts/check_invariants.py` + 온톨로지 동시 확장 필요 → **본 PR 에서는 하지 않음** (별도 이슈)

---

## Task Flow (단계별 실행 순서)

| # | 단계 | 소스 | 검증 | 의존 |
|---|------|------|------|------|
| 1 | `pyproject.toml` lightgbm 추가 + `pip install -e .` | `pyproject.toml` | `import lightgbm` | — |
| 2 | `src/ml/` 디렉토리 + `__init__.py` + `.ai.md` 스켈레톤 | `src/ml/*` | `python -c "import src.ml"` | 1 |
| 3 | `labeling.py` 구현 + `tests/ml/test_labeling.py` | `src/ml/labeling.py` | `pytest tests/ml/test_labeling.py` | 2 |
| 4 | `cv.py` 구현 + `tests/ml/test_cv.py` | `src/ml/cv.py` | `pytest tests/ml/test_cv.py` | 2 |
| 5 | `meta_labeler.py` 구현 + `tests/ml/test_meta_labeler.py` | `src/ml/meta_labeler.py` | `pytest tests/ml/test_meta_labeler.py` | 3, 4 |
| 6 | `walkforward.py` 구현 + `tests/ml/test_walkforward.py` | `src/ml/walkforward.py` | `pytest tests/ml/test_walkforward.py` | 4 |
| 7 | `momo_btc_v2.py` 훅 + `tests/backtest/test_momo_btc_v2_metalabeler.py` + 기존 테스트 회귀 | `src/backtest/strategies/momo_btc_v2.py` | 기존 test 전부 pass + 신규 test pass | 5 |
| 8 | `scripts/bench_metalabeler_btc.py` on/off 비교 | `scripts/*`, `02_implementation.md` | AC4 Sharpe/MDD 기준 통과 | 7 |
| 9 | 문서: `src/ml/.ai.md`, `src/backtest/strategies/.ai.md`, `docs/specs/ml/{.ai.md,meta-labeling.md}`, `docs/background/35-*.md` | 위 파일들 | `python scripts/check_invariants.py --strict` 통과 | 8 |
| 10 | 최종 검증: `pytest`, `scripts/check_invariants.py --strict`, `02_implementation.md` 리포트 완성 | 모든 변경 | CI green | 1-9 |

---

## Guardrails

### Must Have
- [ ] `metalabeler=None` 기본값 — 기존 `momo-btc-v2` 호출 경로 시그니처 변경 없음
- [ ] `random_state=42` + `deterministic=True` — 재현성 강제
- [ ] 훈련 라벨에 `costs_bps` 반영 (세금·수수료·슬리피지)
- [ ] purged CV 시간 경계 = `t_touch > entry_ts` 엄격
- [ ] `predict_proba` → `win_probability` 직접 매핑 (사람 손 개입 0)
- [ ] `manifest.json` 에 git SHA · feature_names · CV 스코어 기록 (재현성)
- [ ] `models/` gitignore
- [ ] `docs/specs/ml/*.md` 프론트매터 `type: spec-architecture` (스키마 위반 방지)
- [ ] 테스트 파일은 `@pytest.mark.slow` 또는 `@pytest.mark.integration` 마커로 기본 실행 제외 (백테스트성 장시간 작업)

### Must NOT Have
- [ ] LLM 호출 (CLAUDE.md 불변식 #6)
- [ ] 미래 정보 참조 (라벨 확정 시점과 피처 관측 시점 일치 또는 역전 금지)
- [ ] `win_probability` 수동 설정 코드
- [ ] 비용 미반영 라벨 학습 (train-live Sharpe 괴리 원인)
- [ ] `pyproject.toml` 의 기존 dep 버전 변경 (scipy/scikit-learn 등)
- [ ] 기존 `tests/backtest/test_momo_btc_v2.py` 수정 (회귀 방지 위해 그대로 통과해야 함)
- [ ] 모델 아티팩트 git 커밋 (`.lgbm`, `.json` 실 아티팩트)
- [ ] Obsidian MCP 서버 직접 호출 (본 모듈은 볼트 무관)

---

## 테스트 매트릭스

| 카테고리 | 파일 | 케이스 수(예상) | 마커 | CI 기본 실행 |
|---------|------|----------------|------|-------------|
| Unit — labeling | `tests/ml/test_labeling.py` | 8 | — | ✅ |
| Unit — CV | `tests/ml/test_cv.py` | 6 | — | ✅ |
| Unit — meta_labeler | `tests/ml/test_meta_labeler.py` | 7 | — | ✅ |
| Unit — walkforward | `tests/ml/test_walkforward.py` | 5 | — | ✅ |
| Regression — momo-btc-v2 | `tests/backtest/test_momo_btc_v2.py` (기존) | (변경 없음) | — | ✅ |
| Integration — metalabeler 훅 | `tests/backtest/test_momo_btc_v2_metalabeler.py` | 4 | — | ✅ |
| Benchmark — on/off | `scripts/bench_metalabeler_btc.py` | 1 | `slow` | ❌ |

---

## 리스크 완화 전략

| 리스크 | 완화 |
|--------|------|
| CV accuracy < 0.55 | 전략에 MetaLabeler 배치 **금지**. 02_implementation.md 에 원인 분석 (피처 부족 / 라벨 노이즈 / 데이터 양) 기록 |
| on/off Sharpe 기준 미달 | momo-btc-v2 에 대해 disable 유지. 포기 기준 준수 (AC4). 원인 분석 후 별도 후속 이슈 |
| LightGBM 재현성 깨짐 (GPU 없음·MT 차이) | `force_col_wise=True` + `num_threads=1` fallback 테스트 추가 |
| 프론트매터 스키마 위반 (CI 차단) | D1-D6 작성 후 `python scripts/check_invariants.py --strict` 로컬 실행 선행 |
| `lightgbm` Windows 설치 실패 | pre-built wheel 존재 확인 (3.3~4.x python 3.11). 실패 시 optional-deps 로 이동 |
| 기존 테스트 회귀 | B1 구현 직후 `pytest tests/backtest/test_momo_btc_v2.py` 먼저 단독 실행 |
| 모델 아티팩트 커밋 사고 | `.gitignore` 에 `models/**` 선언 + pre-commit 훅(가능 시) |
| 특허 회피 (메타라벨링 자체는 공개 기법) | 별도 청구항 충돌 우려 없음. 다만 #84 아티팩트 대조 1회 재확인 |

---

## 마일스톤 (체크포인트)

| 마일스톤 | 산출물 | 기준 |
|----------|--------|------|
| **M1 — 인프라** | Task 1-2 완료 | `pip install -e .` 성공, `src/ml/` import 가능 |
| **M2 — 학습 파이프라인** | Task 3-6 완료 | `tests/ml/` 전 테스트 pass, purged CV accuracy ≥ 0.55 (소형 합성 데이터 기준으로 단위 테스트 통과) |
| **M3 — 전략 통합** | Task 7 완료 | 기존 `test_momo_btc_v2.py` 무변경 pass + 신규 metalabeler 테스트 pass |
| **M4 — 벤치마크** | Task 8 완료 | BTC 15m 1년 on/off 리포트 `02_implementation.md` 기록. AC4 기준 판정 (on 채택 or disable 기록) |
| **M5 — 문서·CI** | Task 9-10 완료 | 불변식 체커 green, 모든 `.ai.md`/spec/background 완비, PR 준비 |

---

## 의존성 · 리스크 메모 — 실측 상태 (2026-04-24 `gh issue view` 기준)

### CLOSED · Done
| 이슈 | 역할 | 본 이슈 연결점 |
|------|------|---------------|
| #67 | 마켓 데이터 + Zipline + momo-btc-v2 | 학습/검증 데이터 소스 |
| #68 | 브로커 커넥터 | 백테스트 단계 독립 |
| #69 | 포지션 사이징 | **`win_probability` 하류 소비자** |
| #70 | 포트폴리오 리스크 | 메타라벨러 on/off 수익률 평가 |
| **#71** | 알파 팩터 파이프라인 | **하드 블로커 — 피처 소스, 룩어헤드 가드 재사용** |
| #74 | 밸류에이션 | 무관 |
| **#76** | Signal 인터페이스 확장 | **하드 블로커 — `win_probability` 출력 슬롯** |
| #78 | 멀티 전략 async 오케스트레이터 | 라이브 경로(#80)에서 활용 |
| #81 | 팩터 점증 계산 + momo-btc-v2 훅 | 기존 momo-btc-v2 리팩토링 반영됨 |
| #84 | 특허 리서치 | 아티팩트: `docs/background/33-patents-factor-models.md`, `docs/background/34-patents-execution-algos.md` |
| #87 | 특허 차용 리팩토링 일괄 | P1/P3/P4/P5a/P5b/P6/P7 구현됨. 메타라벨러 범위와 **겹치지 않음** (확인 완료) |

### OPEN · In Progress
- **#73** 브로커 어댑터 async — 전이 의존. 백테스트 단계는 독립. 라이브 경로(#80) 통해서만 요구
- **#79** 전략 카탈로그 확장 — 소프트 권장. 이슈 body 에 "첫 스파이크는 `momo-btc-v2` 단일로 시작 가능" 명시
  - **병렬 착수 가능** — 디렉토리 분리 (`#79 → src/backtest/strategies/*.py` vs `#85 → src/ml/`) 덕분에 충돌점은 `src/backtest/strategies/.ai.md` 1곳뿐
  - **인터페이스 churn 리스크**: `Signal.win_probability`·sizer 경로 공유

### OPEN · Ready
- **#80** Shadow Paper — 본 이슈 머지 후 on/off 모두 pre-register

### 롤아웃 순서
1. **현재 (본 이슈)** → `momo-btc-v2` 단일 스파이크 착수
2. **#79 머지 후** → 3전략 카탈로그 전체로 확장 (본 이슈 머지 후 별도 PR)
3. **#80 Shadow Paper 진입 전** → 메타라벨러 on/off 모두 pre-registered (A/B)
4. **Phase 3 (Live Pilot) 이후** → 재학습 자동화 (초기는 수동 월별)

### 특허 리서치 조율 (#84 / #87 / #81 모두 CLOSED)
- 아티팩트: `docs/background/33,34`
- #87 선반영 7항목 (P1/P3/P4/P5a/P5b/P6/P7) — 본 이슈 범위와 **겹치지 않음** (실측 확인)
- 파이프라인 순서 개념 유지: (0차 팩터 게이트 — 향후) → (전략 신호) → (2차 메타라벨러 — 본 이슈)
- purged CV 시간 경계 = 팩터 룩어헤드 체크와 동일 timestamp boundary
- 메트릭 네이밍 분리: `ic` (팩터 품질) vs `meta_precision` (2차 메타라벨링)

---

## 다음 액션

1. `pyproject.toml` lightgbm 의존성 추가 → `pip install -e .` (Task 1)
2. `src/ml/` 스켈레톤 + `.ai.md` (Task 2)
3. Task 3-10 순차 진행 — 각 단계마다 `pytest` 로컬 pass 후 다음 단계
4. PR 생성 전 `python scripts/check_invariants.py --strict` + `pytest` 풀런 확인 + `/fi 85`
