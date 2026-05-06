---
type: work-done
id: 000155-cross-asset-manifest-00-issue
name: "#155 cross_asset_compare.py manifest 정합성 (#97 후속)"
status: done
---

# fix: cross_asset_compare.py manifest path / format 정합성 (#97 후속)

## 사용자 관점 목표
#97 v5 에서 발견된 `cross_asset_compare.py` 의 manifest 자동 로드 미작동 fix. bench 결과 → 02_implementation.md 자동 갱신 경로 복원.

## 배경
#97 v5 실데이터 검증 시:
> "[warn] BTC manifest not found locally - using pending placeholder for btc-usdt"
> "[warn] KIS manifest not found locally - using pending placeholder for krx-005930"

`bench_metalabeler_kis.py` 가 stdout JSON 출력만 하고 manifest 파일을 lake/models/ 에 저장하지 않음. `cross_asset_compare.py` 는 manifest 파일을 읽으려 함 → 경로 / 형식 불일치 → placeholder 처리.

## 완료 기준
- [ ] **옵션 A**: bench 가 `models/momo-kis-v1-pooled/<timestamp>/manifest.json` 형식으로 저장 (BTC `models/momo-btc-v2/<timestamp>/` 와 동일 패턴) — train_and_save 가 이미 이 패턴 사용 중. bench 가 train 결과 path 받아서 manifest 로딩하면 끝.
- [ ] **옵션 B**: `cross_asset_compare.py` 가 bench JSON stdout 직접 입력 받도록 stdin 또는 --input 인자 추가
- [ ] 둘 중 하나 선택 후 통합 테스트 (`tests/ml/test_cross_asset_compare_e2e.py`):
  - bench 실행 → cross_asset_compare → 02_implementation.md 5 섹션 자동 갱신
- [ ] 두 자산군 (BTC + KRX) 동시 manifest 로드 시 비교 테이블 정상 산출
- [ ] manifest 부재 시 graceful "보류 (인프라)" 처리는 유지

## 의존성
- **#97 머지 필수** ✅ (2026-05-03 COMPLETED)
- **#97 B-3 (bench Sharpe/DSR 출력) 권장** — manifest 에 sharpe_off/on 등 필드 포함 시 cross_asset_compare 가 더 풍부한 비교 가능

## 주의사항
- 옵션 A 가 v3/v5 패턴과 일관 (train_and_save 가 manifest 자동 저장). 옵션 B 는 stdin 의존성 추가로 테스트 복잡.
- 권고: **옵션 A**.

---

## 작업 내역
- 2026-05-06: `/si 155` 로 워크트리 생성, Backlog → Ready 이동, assign 완료. #97 머지 확인 완료.
- 2026-05-06: 옵션 A 구현 완료 (TDD Red→Green).
  - `tests/ml/test_cross_asset_compare_e2e.py` 신규 — schema/e2e/graceful fallback 3 케이스
  - `src/ml/pipelines/kis_cross_validation.py` — pooled manifest 의 `training_window` 에 `start`/`end` 추가
  - `scripts/bench_metalabeler_kis.py` — `--manifest-dir` 옵션 추가, multi-symbol 의 tempfile 제거 (manifest 영속 저장), single-symbol 모드에도 manifest 저장 추가
  - `scripts/cross_asset_compare.py` — default `--kis-model-dir` 을 `models/momo-kis-v1-pooled` 로 변경 (bench default 와 정합)
  - `tests/ml/` 124/124 통과 (3 신규 + 121 회귀 유지)
  - `scripts/.ai.md`, `src/ml/pipelines/.ai.md` 업데이트
- 2026-05-06: simplify 리뷰 (reuse/quality/efficiency 3 에이전트 병렬) 반영.
  - `STRATEGY_ID + "-pooled"` 상수화 — `_resolve_manifest_dir` 의 string literal 제거
  - 코멘트 트림 (3줄 → 1줄, WHAT 설명 제거)
  - test skip 메시지에 symbols + 사유 명시
  - 33/33 회귀 재통과

## 후속 이슈 후보 (#155 PR 범위 외)
- `_write_single_symbol_manifest` ↔ `run_kis_pipeline_pooled` ↔ `train_and_save` 의 manifest dict 빌더 통합 (현재 3곳 near-identical)
- `trained_at` 형식 표준화 — 현재 4개 변형 (`%Y%m%dT%H%M%SZ`, `%Y-%m-%dT%H:%M:%SZ`, `isoformat(timespec="seconds")` + Z 치환, `isoformat()` no-replace) 이 분산
- UTC run_id 헬퍼 (`utc_run_id() -> str`) — 동일 strftime 5곳 사본 (`live_run.py`, `shadow_run.py`, `shadow_run_swing.py`, `installer.py`, `bench_metalabeler_kis.py`)
- `bench_metalabeler_kis.py` 의 `_mdd` / `_equity_mdd` closure 중복 (pre-existing) — 모듈 레벨 단일 함수로 hoist
