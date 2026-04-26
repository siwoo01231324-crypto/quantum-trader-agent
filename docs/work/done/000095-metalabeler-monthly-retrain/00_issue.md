# feat: 메타라벨러 월별 자동 재학습 + 드리프트 감지 파이프라인

## 사용자 관점 목표
MetaLabeler 모델을 **매달 자동으로 재학습**하여 시장 컨셉 드리프트에 대응. 사람이 매월 수동 실행하는 대신, 스케줄러가 최신 데이터로 새 아티팩트를 생성하고 성능 drift 를 감지해 알린다.

## 배경
#85 구현은 **배치 오프라인 학습** — 학습하면 그 모델을 계속 사용. 시장은 비정상적 (non-stationary) 이라 장기적으로 성능이 열화될 수 있음. López de Prado AFML Ch.7/Ch.9 및 실무 퀀트 다수가 **주기적 재학습 (월/주 단위 배치)** 권장.

온라인 학습 (매 거래마다 업데이트) 은 감사성·안정성 문제로 배제 — 의도적으로 배치 재학습만 다룬다.

## 완료 기준
- [x] 스케줄러 (GitHub Actions cron 또는 별도 러너) 가 매월 1일 자동 실행
- [x] 신규 아티팩트 저장 경로: `models/<strategy_id>/<YYYYMMDD>/` 디렉토리, 이전 아티팩트 보존
- [x] CV 스코어 비교 — 이전 fold vs 최신 fold. **drift 기준** (예: accuracy 하락 5%p 이상 또는 Sharpe 시뮬레이션 하락 0.3 이상) 초과 시 Slack/Telegram 알림
- [x] 프로덕션 `latest/` 심볼릭 링크 자동 업데이트 **또는 수동 승인 게이트** (둘 중 선택 문서화)
- [x] 실패 로그 (데이터 fetch 실패, LightGBM 훈련 실패) 는 재실행 대기 상태로 표기 (#85 `02_implementation.md` 식)

## 구현 플랜
1. `scripts/retrain_metalabeler.py` (또는 `bench_metalabeler_btc.py` 확장) 신설 — 단일 전략 한정, 데이터 fetch → 훈련 → 아티팩트 저장 → drift 리포트
2. GitHub Actions workflow `.github/workflows/metalabeler-retrain.yml` — cron, 시크릿 주입, 아티팩트 업로드
3. 드리프트 감지 로직: `src/ml/drift_detector.py` 신설 — PSI(Population Stability Index) 또는 간단한 accuracy delta
4. 알림 연동: 기존 Slack/Telegram 훅 재사용 (`src/observability/` 참조)
5. 모델 버전 로테이션: `models/<strategy>/latest/` 는 최신 승인된 것으로 업데이트, 구버전은 90일 보존 후 아카이브

## 후속 (out of scope)
- 재학습 자동 **프로덕션 승격** (본 이슈는 승인 게이트 + 알림까지, 실제 production swap 은 별도 이슈)
- 온라인 학습 (실시간 모델 업데이트) — 별도 연구 과제
- 다중 전략 동시 재학습 (스테이징)

## 개발 체크리스트
- [x] 테스트 코드 포함 (드리프트 감지 단위 테스트 + 워크플로우 dry-run)
- [x] 해당 디렉토리 .ai.md 최신화 (`src/ml/`, `.github/workflows/`, `src/observability/`, `scripts/`)
- [x] 불변식 위반 없음 (`check_invariants.py --strict`)

## 작업 내역

### 2026-04-25 ~ 2026-04-26

**플랜 (ralplan, Critic APPROVE)**
- `01_plan.md` 작성, 7 step + Guardrails + Out of scope 명시.

**구현 (`/team 3`, 7 task 병렬, 3 worker)**
- `src/observability/alerts.py` (신설) — `notify(level, title, body, fields)`. Slack/Telegram/stdout 3분기, fail-soft (예외 swallow).
- `src/ml/drift_detector.py` (신설) — `DriftReport` + `compare()`. Threshold 상수화 (`ACCURACY_DRIFT_THRESHOLD=0.05`, `SHARPE_DRIFT_THRESHOLD=0.3`).
- `src/ml/retrain_pipeline.py` (신설) — `train_metalabeler_btc.py` 의 함수 4개 추출 + `train_and_save` 신설.
- `scripts/retrain_metalabeler.py` (신설) — 오케스트레이터 (lake load → train → bench subprocess → drift → alerts → latest.json → 02_retrain_log). `--synthetic` 플래그로 e2e 가능.
- `.github/workflows/metalabeler-retrain.yml` (신설) — cron `0 0 1 * *` + workflow_dispatch, ubuntu-latest, GHA artifacts 90d 보존.
- `scripts/train_metalabeler_btc.py` (수정) — thin CLI 로 변환, `retrain_pipeline` import.
- `.ai.md` 4건 + `docs/specs/ml/meta-labeling.md` 갱신 (월별 재학습 + latest.json 정책 섹션).

**Code review (3 reviewer 병렬)**
- 발견된 CRITICAL 2건 / HIGH 1건 수정:
  - `bench_result` dead variable → bench stdout 의 "ON Sharpe" 정규식 파싱으로 결과 dict 채움.
  - exit code 로직 → `post_train_failure` flag 도입, Step 3/4/6 실패 시 set, exit 1 보장 (AC gate WARN 만 있을 때는 exit 0).
  - drift threshold magic number → 상수화 + 함수 인자로 override 가능.

**검증**
- `pytest tests/observability/test_alerts.py tests/ml/test_drift_detector.py tests/ml/` → 38 passed.
- e2e dry-run (`--synthetic`) → manifest + cv_report + latest.json 생성, exit 0.
- `python scripts/check_invariants.py --strict` → 110 노트 통과.
- workflow YAML 파싱 OK.

**AC 결과**: 5/5 충족.

**Cadence 변경 (사용자 요청, finish-issue 단계)**: 초안 "매월 1일" → 최종 "매주 월요일 00:00 UTC" (cron `0 0 * * 1`). 폴더/브랜치명 (`monthly-retrain`) 은 immutable 하므로 그대로 유지. 워크플로 + spec + .ai.md 는 weekly 로 갱신. drift threshold (5%p / 0.3) 는 동일 (관찰 후 후속 튜닝).
