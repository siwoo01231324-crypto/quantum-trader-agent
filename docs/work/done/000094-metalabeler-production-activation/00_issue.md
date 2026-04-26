# feat: 메타라벨러 프로덕션 활성화 (오케스트레이터 주입 + A/B 등록)

## 사용자 관점 목표
#85 에서 만든 MetaLabeler 인프라를 실제로 **켜서** 오케스트레이터 · 실행 루프가 2차 필터 결정에 따라 움직이게 한다. 현재는 `metalabeler=None` 기본값이라 인프라만 있고 동작 안 함.

## 배경
#85 는 **건물은 완성 / 전원 OFF** 상태다:
- MetaLabeler / triple-barrier / PurgedKFold / WalkForward 모듈 완비 (`src/ml/`)
- `momo-btc-v2` 에 opt-in 훅 존재 (`metalabeler=None` 기본, bypass)
- 하지만 실제로 켜려면:
  1. 훈련된 모델 아티팩트 필요 (`models/<strategy>/<ts>/model.lgbm + manifest.json`)
  2. 오케스트레이터 구성에서 `MomoBtcV2(metalabeler=MetaLabeler.load(...))` 주입
  3. on / off 구성을 **둘 다 pre-registered** 상태로 #80 Shadow Paper 에 진입 (A/B 실측)

AC4 INCONCLUSIVE 상태에서 프로덕션 턴온은 금지. 본 이슈는 **AC4 실데이터 판정이 PASS 된 이후** 착수.

## 완료 기준
- [x] AC4 실데이터 판정 PASS 확인 (`docs/work/done/000085-meta-labeler-lightgbm/02_implementation.md` ✅ PASS, Sharpe Δ +1.04)
- [x] 최신 MetaLabeler 아티팩트가 `models/momo-btc-v2/latest/` 로 (manifest alias) 연결 — `MetaLabeler.load` 1-hop alias 분기 + `scripts/promote_metalabeler.py` + `models/README.md` 신설 (실제 promote 는 운영자가 deploy 시점에 실행)
- [x] 오케스트레이터 구성 파일에서 `MomoBtcV2(metalabeler=MetaLabeler.load("models/momo-btc-v2/latest/"), metalabeler_threshold=0.5)` 주입 — `configs/orchestrator/production.yaml` + `src/portfolio/config_loader.py`
- [x] on/off 전략이 **별개 strategy_id** (`momo-btc-v2` vs `momo-btc-v2-meta`) 로 오케스트레이터에 동시 등록되어 A/B 병행 실측 — `tests/portfolio/test_orchestrator_ab_registration.py` 5 케이스 PASS
- [ ] 프로덕션 턴온 후 첫 주 Shadow Paper 로그에서 on 경로가 off 경로 대비 Sharpe / MDD 개선 확인 (미달 시 즉시 롤백) — **post-merge 운영 검증 (#80 Shadow Paper 의존)**, 롤백 런북은 `docs/specs/strategies/momo-btc-v2.md` 에 명시

## 구현 플랜
1. AC4 실데이터 판정 완료 후 모델 아티팩트 `models/momo-btc-v2/<ts>/` 승격
2. `services/` 또는 `configs/` 하위의 오케스트레이터 구성 YAML/JSON 에서 전략 주입 포인트 찾기 (없으면 신설)
3. on/off 2-전략 pre-register 배선
4. 롤백 런북: AC4 재평가에서 on-degrade 시 `metalabeler=None` 로 되돌리는 단일 커밋 준비
5. `docs/specs/strategies/momo-btc-v2.md` 업데이트 — "프로덕션 구성" 섹션에 on/off 설명

## 개발 체크리스트
- [x] 테스트 코드 포함 — 17 개 신규 테스트 PASS (alias 4 + config_loader 4 + A/B registration 5 + 회귀 4)
- [x] 해당 디렉토리 .ai.md 최신화 (`src/backtest/strategies/.ai.md`, `src/portfolio/.ai.md`, `configs/orchestrator/.ai.md` 신설)
- [x] 불변식 위반 없음 — `python scripts/check_invariants.py --strict` 통과 (109 노트 검증)

## 작업 내역

### 2026-04-25 (구현 완료)

**현황**: 4/5 AC 완료 (AC5 는 post-merge 운영 검증), 3/3 개발 체크리스트 완료

**완료된 항목**:
- AC1 (#85 AC4 실데이터 PASS 확인 — Sharpe Δ +1.04, MDD 개선 9.26%p)
- AC2 (latest/ alias 메커니즘) — `MetaLabeler.load` 1-hop pointer.json 분기 + `scripts/promote_metalabeler.py` + `models/README.md`
- AC3 (오케스트레이터 주입) — `configs/orchestrator/production.yaml` + `src/portfolio/config_loader.py` + `_strategy_adapter.py`
- AC4 (on/off A/B 등록) — `tests/portfolio/test_orchestrator_ab_registration.py` 5 케이스 PASS, 별개 인스턴스 + 충돌 가드 검증

**미완료 항목**:
- AC5 (Shadow Paper 1주 on/off Sharpe·MDD 비교) — PR 머지 후속 운영 검증, `docs/specs/strategies/momo-btc-v2.md` 에 모니터링 절차 + 롤백 런북 명시

**구현 (Team `metalabeler-prod-94`, 3 worker 병렬)**:
- worker-ml: `src/ml/meta_labeler.py` alias 분기, `scripts/promote_metalabeler.py`, `models/README.md`, `tests/ml/test_meta_labeler_alias.py`
- worker-orch: `configs/orchestrator/{production.yaml, .ai.md}`, `src/portfolio/{config_loader.py, _strategy_adapter.py, .ai.md}`, `tests/portfolio/{test_config_loader.py, test_orchestrator_ab_registration.py}`
- worker-docs: `docs/specs/strategies/momo-btc-v2.md` 프로덕션 구성/롤백 런북 섹션, `docs/work/done/000085-meta-labeler-lightgbm/02_implementation.md` 후속링크, `src/backtest/strategies/.ai.md`

**검증 결과**:
- `pytest tests/ml/test_meta_labeler_alias.py tests/portfolio/ tests/backtest/test_momo_btc_v2_metalabeler.py -v` → **17 passed in 2.90s**
- `python scripts/check_invariants.py --strict` → **109 노트 통과**

**변경 파일** (13개, 플랜 매트릭스 일치):
- 신설: `configs/orchestrator/{production.yaml, .ai.md}`, `src/portfolio/{config_loader.py, _strategy_adapter.py}`, `scripts/promote_metalabeler.py`, `models/README.md`, `tests/ml/test_meta_labeler_alias.py`, `tests/portfolio/{test_config_loader.py, test_orchestrator_ab_registration.py}`
- 수정: `src/ml/meta_labeler.py`, `src/portfolio/.ai.md`, `src/backtest/strategies/.ai.md`, `docs/specs/strategies/momo-btc-v2.md`, `docs/work/done/000085-meta-labeler-lightgbm/02_implementation.md`

**다음 단계**: `/finish-issue` 또는 `/fi` 로 PR 생성
