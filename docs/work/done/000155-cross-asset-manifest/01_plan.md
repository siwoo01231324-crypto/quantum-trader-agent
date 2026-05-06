---
type: work-plan
id: 01_plan
---

# 01_plan — #155 cross_asset_compare manifest 정합성

## 사용자 관점 목표
`scripts/cross_asset_compare.py` 가 KIS 측 manifest 를 자동 로드하지 못해 placeholder 로 폴백되는 문제 fix. bench 결과 → `02_implementation.md` 자동 갱신 경로 복원.

## 진단 (현행 분석 결과)

| 위치 | 현상 |
|------|------|
| `scripts/bench_metalabeler_kis.py:206` | `tempfile.TemporaryDirectory()` 안에서 `run_kis_pipeline_pooled` 호출 → manifest 가 종료 시 삭제됨 |
| `scripts/bench_metalabeler_kis.py` (single-symbol main) | manifest 저장 로직 자체가 없음 (직접 train 만 함) |
| `src/ml/pipelines/kis_cross_validation.py:409-411` | KIS pooled manifest 의 `training_window` 에 `start`/`end` 없음 (BTC `train_and_save:368-372` 에는 있음) |
| `scripts/cross_asset_compare.py:149` | default `--kis-model-dir` = `models/momo-kis-v1`, but actual strategy_id = `momo-kis-v1-pooled` (디렉토리 이름 불일치) |
| `tests/ml/` | `test_cross_asset_compare.py` 단위 테스트는 있으나 bench → cross_asset_compare e2e 통합 테스트 부재 |

## AC 체크리스트
- [x] **옵션 A 채택**: bench 가 `models/momo-kis-v1-pooled/<UTC timestamp>/manifest.json` 형식으로 영속 저장 (`bench_metalabeler_kis.py:--manifest-dir`)
- [x] 통합 테스트 `tests/ml/test_cross_asset_compare_e2e.py`: bench → cross_asset_compare → `02_implementation.md` 5섹션 자동 갱신 검증 (3/3 통과)
- [x] 두 자산군 (BTC + KRX) 동시 manifest 로드 시 비교 테이블 정상 산출 (`TestCrossAssetCompareE2E::test_loads_both_manifests_and_renders_full_report`)
- [x] manifest 부재 시 graceful "보류 (인프라)" 처리는 유지 (`TestGracefulFallback::test_missing_kis_manifest_uses_pending_placeholder`)
- [x] #97 머지 확인 (2026-05-03 COMPLETED)
- [x] 회귀 — `tests/ml/` 124/124 통과

## 구현 계획 (옵션 A)

| # | 변경 | 파일 |
|---|------|------|
| 1 | `--manifest-dir PATH` 옵션 추가. default = `models/momo-kis-v1-pooled/<UTC ts>/`. tempfile 대신 영속 디렉토리 사용 | `scripts/bench_metalabeler_kis.py` |
| 2 | single-symbol 모드 main() 에도 manifest.json 영속 저장 (pooled 와 동일 schema) | 동일 |
| 3 | KIS pooled manifest `training_window` 에 `start`/`end` 추가 (BTC 와 동일하게) | `src/ml/pipelines/kis_cross_validation.py` |
| 4 | `cross_asset_compare.py` default `--kis-model-dir` 을 `models/momo-kis-v1-pooled` 로 변경 | `scripts/cross_asset_compare.py` |
| 5 | 통합 테스트 신규 작성 — synthetic OHLCV → bench → manifest 검증 → cross_asset_compare → 02_implementation.md 5섹션 검증 + manifest 부재 시 graceful fallback 회귀 테스트 | `tests/ml/test_cross_asset_compare_e2e.py` (신규) |

## TDD 순서
1. **Red**: `tests/ml/test_cross_asset_compare_e2e.py` 작성 — manifest 영속 저장 + 5섹션 자동 갱신 + manifest 부재 graceful fallback
2. **Green**: 변경 #1 ~ #4 적용
3. **회귀 검증**: 기존 `tests/ml/test_cross_asset_compare.py` + `tests/ml/test_n_eff_correction.py` 통과 유지

## manifest schema (BTC 와 KIS pooled 통합 후)
```json
{
  "strategy_id": "momo-kis-v1-pooled",
  "trained_at": "2026-05-06T12:34:56Z",         // NEW (BTC 패턴)
  "git_sha": "...",                              // NEW (BTC 패턴, optional)
  "n_symbols": 30,                               // KIS pooled 전용
  "symbols": [...],                              // KIS pooled 전용
  "rho_avg": 0.13,                               // KIS pooled 전용
  "n_eff": 8.42,                                 // KIS pooled 전용
  "interval": "1m",
  "holding_bars": 78,
  "costs_bps": 26.0,
  "use_time_block_cv": true,                     // KIS pooled 전용
  "cv_score": {
    "mean_accuracy": 0.62,
    "std_accuracy": 0.04,
    "n_folds": 5
  },
  "label_config": {
    "tp_sigma": 2.0,
    "sl_sigma": 1.5,
    "holding_bars": 78,
    "costs_bps": 26.0
  },
  "training_window": {
    "start": "2026-03-01 09:00:00+00:00",        // NEW (현재 KIS pooled 에 없음)
    "end":   "2026-04-30 15:30:00+00:00",        // NEW
    "n_events": 1234
  },
  "positive_rate_train": 0.41
}
```

## 영향 범위
- `scripts/bench_metalabeler_kis.py`
- `scripts/cross_asset_compare.py`
- `src/ml/pipelines/kis_cross_validation.py`
- `tests/ml/test_cross_asset_compare_e2e.py` (신규)

## 리스크
- KIS pooled 에서 `events_concat.index[0]` 이 multi-symbol 인 경우 timestamp 의 의미가 단순하지 않음 → start/end 는 **train split 전체** 의 min/max ts 로 정의
- bench 가 manifest 디렉토리를 `mkdir -p` 로 생성. 로컬 디스크 쓰기 권한 가정 (CI 도 동일)
- single-symbol 모드 manifest 는 `n_symbols=1`, `rho_avg=0.0`, `n_eff=1.0` 으로 일관 처리

## 비목표
- bench 의 메트릭 산출 로직 변경 (수익률 모델은 그대로)
- BTC 측 (`bench_metalabeler_btc.py`) 은 이미 `--model-path` 인자로 manifest 로딩 가능하므로 변경 없음
- `cross_asset_compare.py` 의 stdin/--input 옵션 (옵션 B) 추가 — 옵션 A 로 해결되므로 보류
