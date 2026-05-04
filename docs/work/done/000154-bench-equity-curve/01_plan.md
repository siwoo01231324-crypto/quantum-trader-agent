# 01 Plan — #154 bench_metalabeler_kis.py equity-curve 기반 Sharpe/MDD/DSR

> 작성: 2026-05-04 · 단일 종목 모드 (`bench_metalabeler_kis.py` 기본 경로)에 한정.
> Multi-symbol pooled 모드의 sr/mdd/dsr 보강은 **후속 이슈로 분리** (events 가 외부에 노출되지 않아 별도 리팩토링 필요).

## 현 상태 실측 (2026-05-04, base = master @ 238c206)

이슈 본문은 stale 하다. `bench_metalabeler_kis.py` 는 이미 다음 키를 JSON 으로 출력 중:
- `sr_off`, `sr_on`, `mdd_off`, `mdd_on`, `dsr_off`, `dsr_on`

따라서 본 PR 의 진짜 갭은 6 가지:

| # | 항목 | 현재 | AC 요구 |
|---|------|------|---------|
| A | **ON 정의** | `y == 1` (triple-barrier 양성 라벨) | 메타라벨러 OOF win_probability ≥ threshold |
| B | `periods_per_year` | 단일 모드는 6552 (15m) 고정 | interval=1m 시 98280 자동 |
| C | Sortino | 없음 | `sortino_off` / `sortino_on` |
| D | DSR delta | 없음 | `dsr_delta = dsr_on - dsr_off` |
| E | 자동 판정 | 없음 | `dsr_on ≥ 0.3` + `n_eff ≥ 5` → PASS, 그 외 HOLD |
| F | 단위 테스트 | 없음 | `tests/ml/test_bench_kis_equity.py` |

A 가 가장 큰 변경이며 BTC bench 의 `metalabeler_threshold=0.5` 와 의미적으로 동치 (메타라벨러 score ≥ threshold 만 거래).

## 선행 의존성 (확인됨)
- #97 — MERGED (`a7a9036`, run_kis_pipeline_pooled, scoring.py 의 `deflated_sharpe_ratio`/`max_drawdown`/`annualized_sharpe`)
- BTC bench 의 `_sortino(returns, periods_per_year)` 패턴 (`scripts/bench_metalabeler_btc.py:54`) 직접 차용

## AC 체크리스트
- [x] 출력 JSON 에 추가:
  - `sharpe_off` / `sharpe_on` (alias 추가, `sr_off`/`sr_on` 동시 출력)
  - `mdd_off` / `mdd_on` (기존 유지)
  - `sortino_off` / `sortino_on` (신규)
  - `dsr_off` / `dsr_on` / `dsr_delta` (dsr_delta 신규)
- [x] OFF 경로: 모든 RSI bullish divergence 신호 → triple-barrier returns
- [x] ON 경로: 메타라벨러 win_probability ≥ threshold 만 → triple-barrier returns (label==1 → OOF prob 기반 변경)
- [x] DSR 임계 (≥ 0.3) 기반 자동 판정 출력 (`n_eff < 5` 시 HOLD 강제) — `verdict` 키
- [x] 단위 테스트: `tests/ml/test_bench_kis_equity.py` 17건

## 구현 계획

### 1. `scripts/bench_metalabeler_kis.py` 수정

**1-1. 인자 추가**
- `--metalabeler-threshold`, type=float, default=0.5 — ON 필터 임계치
- `--periods-per-year`, type=int, default=None — None 이면 interval 따라 자동 (1m=98280, 15m=6552)

**1-2. ON 경로 재정의 (line 232~243 교체)**

기존:
```python
positive_idx = y[y == 1].index
tb_returns_on = _triple_barrier_returns(ohlcv, events.loc[positive_idx], labels_df.loc[positive_idx])
```

변경:
```python
# OOF win_probability ≥ threshold 인 events 만 ON
# y_true_all/y_prob_all 은 fold 순서대로 concat 됨; X_tr.index 와 동일 순서
oof_idx = []
oof_prob = []
for fold in cv_result["folds"]:
    if fold.get("skipped"):
        continue
    fold_idx = fold.get("test_idx")  # X_tr.index 부분집합
    if fold_idx is None:
        continue
    oof_idx.extend(fold_idx)
    oof_prob.extend(fold["y_prob"])

oof_series = pd.Series(oof_prob, index=oof_idx, dtype=float)
on_idx = oof_series[oof_series >= args.metalabeler_threshold].index
tb_returns_on = _triple_barrier_returns(
    ohlcv, events.loc[on_idx], labels_df.loc[on_idx]
)
```

> 의존성: `run_cv_extended` 가 `fold["test_idx"]` 를 반환하는지 확인 필요. 미반환 시 fallback: X_tr 인덱스를 fold 순서대로 split (sklearn-like) 으로 재현. 1차 검증은 코드 읽기.

**1-3. Sortino 추가**
```python
def _sortino(returns: pd.Series, periods_per_year: int) -> float:
    if len(returns) == 0:
        return 0.0
    downside = returns[returns < 0]
    if len(downside) == 0 or downside.std(ddof=1) == 0:
        return 0.0
    return float(returns.mean() / downside.std(ddof=1) * np.sqrt(periods_per_year))
```

**1-4. periods_per_year 자동 선택**
```python
if args.periods_per_year is not None:
    periods = args.periods_per_year
elif args.interval == "1m":
    periods = KRX_PERIODS_PER_YEAR_1M  # 98280
else:
    periods = KRX_PERIODS_PER_YEAR  # 6552
```
이후 `annualized_sharpe`, `_sortino`, `summary["periods_per_year"]` 모두 `periods` 사용.

**1-5. DSR delta + 자동 판정**
```python
dsr_delta = dsr_on - dsr_off
n_eff_single = 1  # 단일 종목 모드
if n_eff_single < 5:
    verdict = "HOLD (n_eff<5)"
elif dsr_on >= 0.3:
    verdict = "PASS (dsr_on>=0.3)"
else:
    verdict = "HOLD (dsr_on<0.3)"

summary["sortino_off"] = round(sortino_off, 6)
summary["sortino_on"] = round(sortino_on, 6)
summary["sharpe_off"] = summary["sr_off"]   # alias for AC compat
summary["sharpe_on"] = summary["sr_on"]
summary["dsr_delta"] = round(dsr_delta, 6)
summary["n_eff"] = n_eff_single
summary["verdict"] = verdict
summary["metalabeler_threshold"] = args.metalabeler_threshold
```

**1-6. n_events_on 추가** (디버그용 — ON 필터링 후 남은 이벤트 수)
```python
summary["n_events_on"] = len(on_idx)
summary["n_events_off"] = len(events.loc[common])
```

### 2. `tests/ml/test_bench_kis_equity.py` 신규

테스트 케이스 (각 단위):

| 테스트 | 검증 |
|--------|------|
| `test_sortino_basic` | 합성 returns 시리즈 (양수·음수 섞임) → 양의 Sortino |
| `test_sortino_no_downside` | 모든 returns ≥ 0 → 0.0 반환 |
| `test_sortino_empty` | 빈 시리즈 → 0.0 |
| `test_periods_per_year_auto_1m` | argparse → interval=1m → 98280 |
| `test_periods_per_year_auto_15m` | interval=15m → 6552 |
| `test_periods_per_year_explicit` | --periods-per-year=12345 → 12345 |
| `test_metalabeler_filter_threshold_high` | OOF prob 모두 < threshold → ON 이벤트 0 |
| `test_metalabeler_filter_threshold_low` | OOF prob 모두 ≥ threshold → ON = OFF |
| `test_dsr_delta_sign` | dsr_on > dsr_off → dsr_delta > 0 |
| `test_verdict_hold_low_n_eff` | n_eff_single=1 → HOLD |
| `test_main_synthetic_smoke` | `main(["--symbol","005930","--lake-dir",tmp])` (lake 없음) → exit 0, JSON 에 키 11종 모두 존재 |

샘플 입력은 합성 (small) — main smoke 는 기존 GBM seed=42 fallback 활용.

### 3. 검증 단계
1. `pytest tests/ml/test_bench_kis_equity.py -x` — 각 단위 GREEN
2. `python scripts/bench_metalabeler_kis.py --interval 1m --metalabeler-threshold 0.5 2>&1 | tail -40` — JSON 에 신규 키 11종 출력
3. `python scripts/bench_metalabeler_kis.py --multi-symbol` (회귀) — 기존 동작 영향 없음 확인
4. `pytest tests/ml/ -x` — ml 폴더 회귀
5. `scripts/check_invariants.py --strict` — 통과

## Guardrails

### Must Have
- 기존 `sr_off`/`sr_on` 키 **유지** (alias 로 `sharpe_off`/`sharpe_on` 추가 — 외부 분석 스크립트의 후방 호환)
- `--metalabeler-threshold` 기본값 0.5 (BTC bench 패턴과 일치)
- DSR 자동 판정에서 `n_eff < 5` 가 항상 우선 (dsr 점수 무관 HOLD)
- 회귀: multi-symbol 모드 출력 변화 없음

### Must NOT Have
- KRX 풀 백테스트 엔진 신설 (이슈 명시 비-범위)
- ralph 루프·LLM 의존 작업
- 자동 커밋 (CLAUDE.md 규칙)
- `*.csv`, `*.parquet` 산출물 커밋

## 변경 영향 범위
- `scripts/bench_metalabeler_kis.py` (수정)
- `tests/ml/test_bench_kis_equity.py` (신규)
- `scripts/.ai.md` (신규 인자·키 명시 — 있으면 갱신, 없으면 생략)

## 리스크
- **`run_cv_extended` 가 `test_idx` 미반환** 시 fold→original index 매핑이 어려움. 1차 코드 확인이 필요. 없으면 small refactor 로 추가.
- **OOF probability 가 비어있을 때** (모든 fold skip): ON 경로는 빈 시리즈 → sr_on=0, sortino_on=0, dsr_on 은 deflated_sharpe_ratio 호출 시 0. 명시적 `note` 키로 알림.
- **Multi-symbol 모드 누락**: 이슈 AC 가 명시적으로 multi 를 요구하지 않음. 후속 이슈 분리 (이슈 본문 update 또는 별도 이슈).

---

## 작업 내역

### 2026-05-04 구현 완료
- 신규 헬퍼 (`scripts/bench_metalabeler_kis.py`):
  - `_sortino(returns, periods_per_year)` — 하방 표준편차 기반 Sortino
  - `_resolve_periods_per_year(interval, explicit)` — 1m=98280, 그 외 6552
  - `_compute_verdict(dsr_on, n_eff)` — n_eff<5 우선 게이트, 그 외 dsr_on>=0.3
  - `_oof_filter(oof, threshold)`, `_build_oof_series(cv_result)` — OOF 확률 기반 ON 필터
- ON 경로 재정의: `y == 1` (라벨) → **OOF win_probability ≥ threshold** (메타라벨러 기반)
- 출력 JSON 신규 키: `sharpe_off/on` (alias), `sortino_off/on`, `dsr_delta`, `n_eff`, `verdict`, `metalabeler_threshold`, `n_events_off/on`
- CLI 추가: `--metalabeler-threshold` (default 0.5), `--periods-per-year` (default None=자동)
- `src/ml/retrain_pipeline.py::run_cv_extended` fold dict 에 `test_event_idx` 추가 (한 줄, 후방 호환 유지)
- 신규 테스트: `tests/ml/test_bench_kis_equity.py` 17건 (Sortino, periods 자동, verdict, OOF filter, build_oof_series, main smoke)
- 회귀: `tests/ml/` 121/121 GREEN, invariants 통과
- 실 실행 검증 (synthetic GBM): `periods_per_year=98280` ✓, `verdict="HOLD (n_eff<5)"` ✓, `n_events_off=353 / n_events_on=111` (threshold=0.5 필터링 동작)
- 문서: `scripts/.ai.md` 에 `bench_metalabeler_kis.py` / `bench_metalabeler_btc.py` 라인 추가

### 2026-05-05 Multi-symbol pooled 모드 통합 + 실 검증
사용자 요청으로 본 PR 에 통합:
- `run_kis_pipeline_pooled` — events 에 `symbol` 칼럼, fold 별 OOF y_prob 캡처, `report["_extras"]` 로 raw artifacts 노출
- `_pooled_triple_barrier_returns` 신규 — multi-symbol positional pairing log return
- `_run_multi_symbol_bench` 가 sr/sortino/dsr/dsr_delta/verdict + 11종 키 출력
- `fetch_kis_backfill.py` — `HANTOO_FAKE_*` env fallback (paper 자동) 추가
- 회귀: `tests/ml/` 121/121 + `tests/data_lake/test_fetch_kis_backfill.py` 3/3 GREEN

### 실 검증 결과 (KIS 1m lake, `02_real_validation.md` 참조)
- 11종목 × 21영업일 1m 데이터 백필 (KIS API connect timeout 으로 30종목 시도→11종목 중단)
- 8종목 events 발생 (3종목 RSI bullish divergence 0건)
- 3 thresholds (0.5/0.3/0.2) 모두 verdict=`HOLD (dsr_on<0.3)` — 자동 게이트 정상
- **메타라벨러 효과**: ON Sharpe -60.76 vs OFF -6.46 (9배 악화) — 학습 데이터 부족 (4종목 × 21일)
- OOF prob 분포 [0.195, 0.272] 좁음 — underfit 확정

### 운영 영향 (production.yaml 확인)
- 본 master `production.yaml` 에 `momo-kis-v1` strategy 미등록
- #177 worktree 의 production.yaml 에는 `momo-kis-v1` 등록되어 있으나 **메타라벨러 옵션 미사용** (kwargs 에 metalabeler 미설정)
- → **운영 리스크 0**. 본 PR 의 검증 결과는 #177 작업자가 metalabeler 옵트인 결정 시 사전 경고로 활용
