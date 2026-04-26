---
id: 01_plan
type: work-plan
issue: 95
status: ready
---

# 01_plan — 메타라벨러 월별 자동 재학습 + 드리프트 감지 파이프라인

> 작성: 2026-04-25 · ralplan (Critic APPROVE, 3 fix 반영)

## AC 체크리스트 (완료 기준)

- [ ] AC1: 스케줄러 (GitHub Actions cron) 가 매월 1일 자동 실행 → **Step 5**
- [ ] AC2: 신규 아티팩트 저장 경로 — `models/<strategy_id>/<YYYYMMDD>/` 디렉토리, 이전 아티팩트 보존 → **Step 3**
- [ ] AC3: CV 스코어 비교 (이전 vs 최신) — drift 기준 (accuracy 5%p OR Sharpe 0.3) 초과 시 Slack/Telegram 알림 → **Step 2 + Step 3**
- [ ] AC4: 프로덕션 `latest/` 자동 업데이트 (메타파일 방식) — 정책 문서화 → **Step 4**
- [ ] AC5: 실패 로그 (data fetch / LightGBM 훈련 실패) 는 재실행 대기 표기 → **Step 3 + Step 5**

## 개발 체크리스트

- [ ] 테스트 코드 (drift detector 단위 + alerts 단위 + retrain e2e dry-run)
- [ ] `.ai.md` 최신화 (`src/ml/`, `src/observability/`, `.github/workflows/`, `scripts/`)
- [ ] 불변식 위반 없음 (`scripts/check_invariants.py --strict`)

---

## 구현 계획

### Step 1 — 알림 어댑터 (선행)

- 파일: `src/observability/alerts.py` 신설.
- API: `notify(level: 'info'|'warn'|'critical', title: str, body: str, fields: dict | None = None) -> None`.
- 채널 분기 (env 기반):
  - `SLACK_WEBHOOK_URL` 있으면 Slack incoming webhook POST.
  - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` 있으면 Telegram Bot HTTP API POST.
  - 둘 다 없으면 stdout 로깅만 (CI fork PR / 로컬 친화).
- 외부 호출은 `requests.post(..., timeout=10)`. 실패 시 stderr 경고 + 예외 swallow (알림 실패가 retrain 을 막지 않음 — fail-soft).
- 테스트: `tests/observability/test_alerts.py` — `requests.post` monkeypatch 로 Slack payload 형식, Telegram payload 형식, 무환경 분기, 네트워크 실패 swallow 검증.

### Step 2 — Drift 감지 모듈

- 파일: `src/ml/drift_detector.py` 신설.
- 입력: 신규 manifest path + 이전 manifest path (None 허용 — 첫 학습 시).
- 출력: dataclass `DriftReport(triggered: bool, reason: str, accuracy_delta: float | None, sharpe_delta: float | None)`.
- 비교 로직:
  - `accuracy_delta = prev.cv_score.mean_accuracy − new.cv_score.mean_accuracy` (양수 = 성능 하락).
  - `sharpe_delta = prev_bench.on.sharpe − new_bench.on.sharpe` (벤치 결과가 둘 다 있을 때만; 없으면 None).
  - threshold: `accuracy_delta ≥ 0.05` OR `sharpe_delta ≥ 0.3` → `triggered=True`.
- 첫 학습 (prev=None) 은 `DriftReport(triggered=False, reason='first-run')` 반환.
- 테스트: `tests/ml/test_drift_detector.py` — fixture manifest 로 시나리오 7건 (acc 5%p ↑/↓/=, sharpe 0.3 ↑/↓/=, 첫 학습=None, 결측 필드 graceful).
- **PSI 미채택 근거** (Guardrails 의 일부): PSI 는 피처 분포 트래킹 인프라 필요 — v1 에서는 manifest 에 이미 있는 `cv_score.mean_accuracy` + bench `sharpe` delta 만 사용. PSI 는 후속 이슈 (out of scope 항목 참조).

### Step 3 — Retrain 오케스트레이터 스크립트

- 파일 신설: `scripts/retrain_metalabeler.py` (얇은 CLI), `src/ml/retrain_pipeline.py` (재사용 가능 함수).
- 기존 `scripts/train_metalabeler_btc.py` 의 핵심 함수를 `src/ml/retrain_pipeline.py` 로 추출:
  - 이동: `load_ohlcv_from_lake`, `build_events_and_features`, `label_events`, `run_cv` → `src/ml/retrain_pipeline.py` 의 공개 API.
  - 추가 신규: `train_and_save(strategy_id, ohlcv, output_dir, **hparams) -> SavedArtifact` (현 `train_metalabeler_btc.main` 의 269~326 라인 save/manifest 블록 함수화).
  - 보존: `train_metalabeler_btc.py` 는 thin CLI 로 남김 (`from ml.retrain_pipeline import ...` import 후 argparse + main entrypoint). 기존 사용처 호환 유지.
- `retrain_metalabeler.py` 책임: 단일 전략 (default `momo-btc-v2`) 매월 재학습 오케스트레이션.
  1. 인자: `--strategy-id`, `--lake-dir`, `--symbol`, `--interval`, `--prev-manifest` (선택), `--bench` (flag, 선택).
  2. `retrain_pipeline.load_ohlcv_from_lake(...)` → 데이터 로드.
  3. `build_events_and_features` → `label_events` → `run_cv` → `train_and_save(output_dir=models/<strategy>/<YYYYMMDD>)` (시간초 제거; 이미 존재 시 `<YYYYMMDD-HHMMSS>` 폴백).
  4. (`--bench` 일 때) `bench_metalabeler_btc.py` subprocess 호출 → on/off Sharpe 캡처 → JSON 으로 새 artifact 디렉토리에 저장 (`bench_result.json`).
  5. `drift_detector.compare(prev_manifest, new_manifest, bench_path)` → DriftReport.
  6. drift triggered → `alerts.notify('warn', f'metalabeler drift: {strategy_id}', body=report.reason, fields={...})`.
  7. 결과 리포트: `docs/work/active/000095-metalabeler-monthly-retrain/02_retrain_log/<YYYYMMDD>.md` 작성 — 표 (CV/Sharpe before vs after), drift verdict, 실패 로그 섹션, "retry 필요" 표기.
- 실패 처리 (AC5): 각 단계 try/except → traceback 을 02_retrain_log 의 "실행 환경 실패 로그" 섹션에 기록 (#85 패턴 그대로). exit code: `0=ok`, `2=drift_detected`, `1=fatal_failure`.

### Step 4 — Latest 메타파일 (AC4 — 자동 업데이트 + 메타파일 채택)

- **선택 근거 (문서화 필수)**: 자동 심볼릭 링크는 Windows worktree 비호환·감사성 약함 → JSON 메타파일이 cross-platform + git-trackable (manifest 자체는 gitignored 모델 디렉토리 밖에 둠).
- 파일 위치: `models/<strategy_id>/latest.json` (모델 디렉토리 외부, gitignore 영향 없음).
- 형식:
  ```json
  {
    "version": "20260501",
    "trained_at": "2026-05-01T00:00:00Z",
    "git_sha": "abc1234",
    "cv_mean_accuracy": 0.512,
    "drift_triggered": false,
    "approved": true
  }
  ```
- 갱신 정책: `drift_triggered=False` 일 때만 자동 갱신. drift 면 latest 보존 + 알림만 (수동 검토 후 다음 월 재학습이 정상 통과해야 갱신).
- 첫 학습은 자동 등록 (prev 없음 → drift skip).
- 후속 production swap (live 어댑터가 latest.json 을 읽어 모델 로드 전환) 은 **별도 이슈 — out of scope**.
- 문서화: `docs/specs/ml/meta-labeling.md` 에 "월별 재학습 + latest 갱신 정책" 섹션 신설.

### Step 5 — GitHub Actions 워크플로

- 파일: `.github/workflows/metalabeler-retrain.yml` 신설.
- 트리거:
  ```yaml
  on:
    schedule:
      - cron: '0 0 1 * *'   # 매월 1일 00:00 UTC
    workflow_dispatch:
      inputs:
        strategy_id: { default: 'momo-btc-v2' }
  ```
- runner: `ubuntu-latest` · Python 3.11 · `pip install -e ".[dev]"`.
- 단계:
  1. `actions/checkout@v4` + `actions/setup-python@v5`.
  2. **데이터 fetch**: `python scripts/fetch_candles.py --symbol BTCUSDT --interval 15m --months 13` (공개 API, secret 불필요). 출력: `lake/ohlcv/...` parquet.
  3. **이전 manifest 다운로드**: `actions/download-artifact@v4` (`name: metalabeler-${{ inputs.strategy_id || 'momo-btc-v2' }}-prev`, `continue-on-error: true` — 첫 실행은 없음).
  4. **재학습 실행**: `python scripts/retrain_metalabeler.py --strategy-id momo-btc-v2 --prev-manifest <downloaded-dir>/manifest.json --bench`.
  5. **신규 아티팩트 업로드**: `actions/upload-artifact@v4` — `path: models/momo-btc-v2/<YYYYMMDD>/` (전체 디렉토리), `retention-days: 90`, name: `metalabeler-momo-btc-v2-<YYYYMMDD>`.
  6. **`prev` 포인터 갱신**: 같은 artifact 를 `metalabeler-momo-btc-v2-prev` 이름으로 한 번 더 업로드 (다음 cron 이 download 할 대상; drift 무관 — 항상 최신으로 갱신해 다음 비교 baseline 으로 사용).
  7. **02_retrain_log 업로드**: `02_retrain_log/<YYYYMMDD>.md` 도 artifact 로 업로드 (감사성).
  8. **종료**: 스크립트 exit code 가 1 이면 step fail → workflow fail (알림은 스크립트 안에서 이미 발송). exit 2 (drift) 는 워크플로 success 유지 (알림으로 충분).
- secrets: `SLACK_WEBHOOK_URL`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` — Repository Secrets 등록 (없으면 stdout 폴백, fork PR 호환).
- AC5 보강: workflow step 별 `continue-on-error: false` (fetch / retrain 실패 시 즉시 fail), 그러나 스크립트 내부에서 02_retrain_log 작성 후 exit → 후속 monitoring 이 retry 가능.

### Step 6 — 문서·.ai.md 갱신

- `src/ml/.ai.md` — `drift_detector.py`, `retrain_pipeline.py` 라인 추가.
- `src/observability/.ai.md` — 없으면 신설, `alerts.py` 추가.
- `.github/workflows/.ai.md` — 없으면 신설, workflow 카탈로그.
- `scripts/.ai.md` — 없으면 신설, `retrain_metalabeler.py` (+ 기존 train/bench) 등재.
- `docs/specs/ml/meta-labeling.md` — "월별 재학습" + "latest.json 정책" 섹션 추가.

### Step 7 — 테스트 + 불변식

- 단위: `pytest tests/ml/test_drift_detector.py tests/observability/test_alerts.py -q`.
- e2e dry-run: `python scripts/retrain_metalabeler.py --strategy-id momo-btc-v2 --lake-dir <synthetic>` 로 1 사이클 (모델 저장 → drift first-run skip → latest.json 생성).
- workflow yaml 검증: `actionlint` (있으면) 또는 `python -c "import yaml; yaml.safe_load(open('.github/workflows/metalabeler-retrain.yml'))"`.
- 불변식: `python scripts/check_invariants.py --strict` 100% 통과.

---

## 실행 순서 (의존성)

```
Step 1 (alerts)  ─┐
Step 2 (drift)   ─┼─→ Step 3 (retrain script + pipeline 추출)
                  │       ↓
                  │   Step 4 (latest.json 갱신 — Step 3 결과 사용)
                  │       ↓
                  │   Step 5 (workflow — Step 1~4 모두 호출)
                  │       ↓
                  └─→ Step 6 (.ai.md / specs 문서)
                          ↓
                      Step 7 (테스트 + 불변식 검증)
```

---

## Guardrails

### Must Have
- `deterministic=True`, `force_col_wise=True`, `random_state=42` (#85 불변식 유지).
- 알림 실패가 retrain 본 파이프라인을 막지 않음 (fail-soft, swallow + stderr 경고).
- 첫 학습 (prev_manifest 없음) 은 drift skip + latest 자동 등록.
- 학습 하이퍼파라미터 (`costs_bps`, `tp_sigma`, `sl_sigma`, `holding_bars`) 는 manifest 에 그대로 보존 — 재현성.
- 데이터 fetch 실패 시 02_retrain_log 의 "실행 환경 실패 로그" 섹션에 traceback + "retry 필요" 표기 (#85 패턴 일치).

### Must NOT Have
- LLM 위임 (CLAUDE.md 불변식 #6) — drift 판정·승격 결정은 순수 수치 비교 + 환경변수 기반.
- `models/<strategy>/<YYYYMMDD>/` 를 git 에 커밋 (gitignored, GHA artifact 만 사용).
- 자동 production swap (live 어댑터의 모델 경로 자동 갱신) — out of scope.
- 다중 전략 동시 재학습 (단일 `--strategy-id` 만 지원, GH Actions matrix 사용 X).
- 5MB 초과 파일 git 커밋 (모델 아티팩트는 GHA artifact 전용).
- 알림 메시지에 secret/토큰 반사 (payload 빌더에서 env value 직접 출력 금지).

---

## Out of Scope (후속 이슈)

- **자동 production 승격** — 본 이슈는 "최신 정상 학습본 표시 + 알림" 까지. live 모델 swap 은 별도.
- **온라인 학습** (실시간 파라미터 업데이트) — 별도 연구 과제.
- **다중 전략 동시 재학습** (전략별 cron 매트릭스) — 본 이슈는 단일 전략 검증 후 후속에서 일반화.
- **PSI (Population Stability Index)** — feature 분포 추적 인프라 필요. v1 은 cv_accuracy + sharpe delta 로 충분.
- **A/B shadow paper 자동화** (#80 후속) — drift 발생 시 shadow 모드 자동 전환은 별도.

---

## 작업 내역

### 2026-04-25
**현황**: 0/5 AC 완료 (구현 대기 — plan ready)
**미완료 항목**: AC1, AC2, AC3, AC4, AC5 전부
**변경 파일**: 0개 (plan 작성만)
**다음 단계**: Step 1 (`src/observability/alerts.py`) 부터 TDD 로 시작.
