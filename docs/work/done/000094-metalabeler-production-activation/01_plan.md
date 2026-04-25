# [#94] 메타라벨러 프로덕션 활성화 (오케스트레이터 주입 + A/B 등록) — 구현 계획

> 작성: 2026-04-25

---

## 완료 기준 (이슈 본문에서 추출)

- [ ] AC4 실데이터 판정 PASS 확인 (`docs/work/done/000085-meta-labeler-lightgbm/02_implementation.md` 업데이트됨)
- [ ] 최신 MetaLabeler 아티팩트가 `models/momo-btc-v2/latest/` 로 (심볼릭 링크 또는 manifest alias) 연결
- [ ] 오케스트레이터 구성 파일에서 `MomoBtcV2(metalabeler=MetaLabeler.load("models/momo-btc-v2/latest/"), metalabeler_threshold=0.5)` 주입
- [ ] on/off 전략이 별개 strategy_id (예: `momo-btc-v2` vs `momo-btc-v2-meta`) 로 오케스트레이터에 동시 등록되어 A/B 병행 실측
- [ ] 프로덕션 턴온 후 첫 주 Shadow Paper 로그에서 on 경로가 off 경로 대비 Sharpe / MDD 개선 확인 (미달 시 즉시 롤백)

## 개발 체크리스트

- [ ] 테스트 코드 포함 (구성 로더 단위 테스트 + on/off 분리 등록 회귀 테스트)
- [ ] 해당 디렉토리 .ai.md 최신화 (`src/backtest/strategies/`, 신규 `configs/orchestrator/`)
- [ ] 불변식 위반 없음 (`python scripts/check_invariants.py --strict`)

---

## 구현 계획

### 코드베이스 실측 결과

- `src/backtest/strategies/momo_btc_v2.py` — `MomoBtcV2.__init__` 가 이미 keyword-only `metalabeler: Optional["MetaLabeler"] = None`, `metalabeler_threshold: float = 0.5` 를 받고, `on_bar` 에서 `metalabeler is not None` 가드로 bypass 보장. 인프라는 완성, 주입만 비어 있음.
- `src/ml/meta_labeler.py` — `MetaLabeler.load(dir_path: Path)` 클래스 메서드가 `dir_path/model.lgbm` + `dir_path/manifest.json` 을 읽어 booster + `_feature_names` 를 복원. `manifest.json` 스키마: `{trained_at, git_sha, feature_names, config}` (학습 스크립트가 `strategy_id, label_config, cv_score, holdout_accuracy, training_window, positive_rate_train` 추가).
- `src/portfolio/_async_orchestrator.py` — `AsyncStrategyOrchestrator.register_strategy(strategy_id: str, strategy: object)` (라인 61). 동일 ID 두 번 register 시 dict 덮어쓰기 (위험). `register_strategy_returns` 도 따로 필요. 전략은 `on_bar(ctx)` (sync 또는 async) 시그니처를 가져야 `run_bar` 가 호출.
- `src/backtest/strategies/__init__.py`, `protocol.py` — `Strategy` Protocol 시그니처는 `on_bar(bar, history, context)` (sync). `AsyncStrategy` Protocol 은 `on_bar(ctx)` (async). 현재 `MomoBtcV2` 는 sync `on_bar(bar, history, context)` 시그니처라 오케스트레이터의 `run_bar(ctx)` 와 직접 어댑터 필요 — 기존 sync 등록 경로는 `AsyncStrategyOrchestrator` 가 `inspect.iscoroutinefunction` 으로 분기하지만 `ctx` 인자만 넘긴다. 즉 `MomoBtcV2.on_bar(bar, history, context)` 는 그대로 못 붙이고 **얇은 어댑터** 가 필요.
- `models/` 디렉토리 — 워크트리에 **존재하지 않음** (실측: `ls models/` → MISSING). `.gitignore` 11번 줄 `models/` 차단. 실 아티팩트 (`20260424-191615/`) 는 #85 작업 워크트리에만 존재.
- `services/` `configs/` `scripts/` — `services/` 는 `doc_agent/`, `obsidian_mcp/` 만; `configs/` 디렉토리 **없음**; `scripts/` 에는 `bench_metalabeler_btc.py`, `train_metalabeler_btc.py` 학습/벤치 스크립트만. 즉 **오케스트레이터 구성 진입점이 아직 코드베이스에 없음 — 신설해야 함**.
- `tests/test_portfolio_orchestrator_async.py` — `_register(orch, strat)` 헬퍼 패턴, `Signal(action, size, reason)` 반환, `register_strategy(strategy_id, strat)` 호출 검증. on/off 분리 등록 회귀 테스트의 골격.
- `tests/backtest/test_momo_btc_v2_metalabeler.py` — `_StubMetaLabeler.win_probability(X) → np.ndarray` duck-typing 만족하면 됨. 4 개 테스트 모두 PASS.
- `docs/specs/strategies/momo-btc-v2.md` — 사이징/훅 섹션은 있으나 "프로덕션 구성" / on-off A/B 섹션 부재. AC5 항목.
- `docs/work/done/000085-meta-labeler-lightgbm/02_implementation.md` — 이미 PASS 판정 + Sharpe Δ +1.04 기재. AC1 본질은 "경로 갱신" — 이슈 본문은 `docs/work/done/...` 경로를 가리키므로 (이미 done 폴더로 이동), 추가 업데이트는 "본 PR 머지로 #94 활성화 시작" 한 줄 후속링크 정도면 충분.

### Task Flow

#### 1) 모델 아티팩트 승격 + `latest` 별칭 (AC2)

**문제**: Windows 심볼릭 링크는 관리자 권한 또는 Developer Mode 가 있어야 하므로 비결정적. 또한 `.gitignore: models/` 때문에 아티팩트는 git 에 들어가지 않는다 — 즉 "프로덕션 활성화" 는 **로컬·CI 양쪽에서 재현가능한 manifest alias 메커니즘** 이 필요.

**선택**: manifest alias 방식. `models/momo-btc-v2/latest/` 디렉토리를 두고 안에 `pointer.json` 한 파일만 두는 패턴.
```json
{ "active": "20260424-191615", "promoted_at": "2026-04-25T...", "git_sha": "994ea11" }
```
`MetaLabeler.load(...)` 가 `latest/` 를 받았을 때 `pointer.json` 을 읽어 실제 디렉토리로 위임하도록 thin wrapper 추가.

**파일 변경**:
- `src/ml/meta_labeler.py` — `load()` 클래스 메서드 시작부에 `pointer.json` 존재 시 alias 해석 분기 (≤10 lines). `model.lgbm` 미존재 + `pointer.json` 존재 → 동일 디렉토리 부모 하위의 지목된 sub-directory 로 재호출.
- `scripts/promote_metalabeler.py` (신설) — 인자 `--strategy momo-btc-v2 --version 20260424-191615`, `models/momo-btc-v2/latest/pointer.json` 작성. write 대상 1개 파일이라 롤백 단순.
- `models/.gitkeep` 는 추가 안 함 (`.gitignore` 정책 유지). 대신 `models/README.md` 신설로 promote 절차 명시.

**검증**: `tests/ml/test_meta_labeler_alias.py` (신설) — 임시 디렉토리에 (a) 정상 디렉토리 직접 load, (b) alias 디렉토리 + pointer.json 경유 load 두 케이스가 동일 booster 를 복원.

#### 2) 오케스트레이터 구성 진입점 신설 (AC3)

**문제**: `configs/` 디렉토리도 없고 entry-point 스크립트도 없음. 신설 위치 결정 필요. `services/` 는 doc/MCP 전용, `scripts/` 는 일회성 도구 → **신설 디렉토리 `configs/orchestrator/`** 가 가장 자연스럽다 (코드가 아니라 배선 데이터).

**구체 파일**:
- `configs/orchestrator/production.yaml` (신설) — 전략 목록 선언:
  ```yaml
  strategies:
    - id: momo-btc-v2
      class: backtest.strategies.momo_btc_v2.MomoBtcV2
      kwargs: { sizing_mode: full }
    - id: momo-btc-v2-meta
      class: backtest.strategies.momo_btc_v2.MomoBtcV2
      kwargs:
        sizing_mode: full
        metalabeler:
          load_path: models/momo-btc-v2/latest/
        metalabeler_threshold: 0.5
  ```
- `configs/orchestrator/.ai.md` (신설, **불변식 #5 강제**)
- `src/portfolio/config_loader.py` (신설) — `load_orchestrator_from_yaml(path, policy) -> AsyncStrategyOrchestrator`. YAML 파싱 → 클래스 import → kwargs 중 `metalabeler.load_path` 보이면 `MetaLabeler.load(path)` 결과로 치환 → `register_strategy(id, _StrategyAdapter(strategy))` 호출. **주입은 코드에서, LLM 으로부터 kwargs 받지 않음** (불변식 #6).
- `src/portfolio/_strategy_adapter.py` (신설, 또는 `_async_orchestrator.py` 안 helper) — `MomoBtcV2` 의 `on_bar(bar, history, context)` 를 오케스트레이터의 `on_bar(ctx)` 시그니처로 변환하는 어댑터.

**검증**: `tests/portfolio/test_config_loader.py` (신설) — production.yaml fixture 로딩 후 (a) 두 개 strategy 등록 확인, (b) 각 인스턴스의 `_metalabeler` 가 None vs MetaLabeler 인스턴스인지 검증, (c) 동일 인스턴스가 아닌지 (분리된 객체) 확인.

#### 3) on/off 전략 pre-register (AC4)

위 step 2 의 YAML 이 이미 두 항목을 정의 → `config_loader` 가 `register_strategy("momo-btc-v2", off_inst)` 와 `register_strategy("momo-btc-v2-meta", on_inst)` 두 번 호출. **strategy_id 충돌 가드** 도 loader 에 추가 (set 으로 중복 검사 → ValueError).

**리스크 노트**: 두 전략이 동일 신호원으로부터 같은 시점에 buy 를 낸다 → `register_strategy_returns` 시 두 strategy_id 모두에 별도 시계열을 공급해야 portfolio risk 가 분리된다. 본 이슈는 "등록까지" 가 AC 이며 returns 공급은 Shadow Paper 루프 (#80) 의 책임이지만, loader 가 `register_strategy_returns(sid, pd.Series(dtype=float))` 를 빈 시리즈로 init 해두면 (≥2 obs 도달 전엔 report None 반환 정상) 누락 사고 방지.

**검증**: `tests/portfolio/test_orchestrator_ab_registration.py` (신설) — production.yaml 로딩 후 `orch._strategies` 에 두 ID 모두 존재, `orch._strategies["momo-btc-v2"]._metalabeler is None`, `orch._strategies["momo-btc-v2-meta"]._metalabeler is not None`.

#### 4) 롤백 런북 + 단일 커밋 템플릿

**롤백 트리거**: 첫 주 Shadow Paper 로그에서 `momo-btc-v2-meta` Sharpe < `momo-btc-v2` Sharpe (또는 MDD 악화) → 즉시 비활성화.

**단일 커밋 형태**: `configs/orchestrator/production.yaml` 의 `momo-btc-v2-meta` 항목 한 블록만 주석 처리하는 1-파일 변경. 별도 코드/테스트 변경 0. 커밋 메시지 템플릿:
```
revert(orchestrator): metalabeler off — Shadow Paper week-1 degrade

trigger: Sharpe(meta=X.XX) < Sharpe(off=Y.YY) over 7d window
artifact: <path-to-shadow-log>
follow-up: #95 (자동 재학습) 후 재시도
```

**파일 변경**:
- `docs/specs/strategies/momo-btc-v2.md` 의 새 "프로덕션 구성" 섹션에 "롤백 런북" sub-section 추가 (AC5).
- 별도 자동화 스크립트 (`scripts/disable_metalabeler.py`) 는 과잉. YAML 한 줄 주석이 더 명확.

#### 5) Shadow Paper 1주 모니터링 절차 (AC5)

`docs/specs/strategies/momo-btc-v2.md` 에 "프로덕션 구성" 섹션을 만들어 다음 명시:
- on/off 두 strategy_id 가 **동시 활성** 임을 알리고
- Shadow Paper 로그 위치 (#80 출력 디렉토리)
- 모니터링 지표: Sharpe (rolling 7d), MDD (peak-to-trough since promotion), trade_count delta
- 일일 수동 체크 항목 (orchestrator 가 두 ID 모두 살아있나, 어느 한쪽이 quarantine 되지 않았나 — `orchestrator.quarantined_strategies` 확인)
- 1주 종료 시 PASS/FAIL 판정 → FAIL → step 4 단일 커밋 실행

본 이슈는 모니터링 자동화는 미포함 (#80 범위). 본 PR 은 "켜기" 까지.

### 변경 파일 매트릭스

| 파일 | 변경 종류 | AC 매핑 |
|---|---|---|
| `src/ml/meta_labeler.py` | 수정 (alias 분기 ≤10 lines) | AC2 |
| `scripts/promote_metalabeler.py` | 신설 | AC2 |
| `models/README.md` | 신설 | AC2 |
| `configs/orchestrator/production.yaml` | 신설 | AC3, AC4 |
| `configs/orchestrator/.ai.md` | 신설 | 불변식 #5 |
| `src/portfolio/config_loader.py` | 신설 | AC3, AC4 |
| `src/portfolio/_strategy_adapter.py` | 신설 | AC3 |
| `src/portfolio/.ai.md` | 수정 (config_loader 추가 기술) | 불변식 #5 |
| `tests/ml/test_meta_labeler_alias.py` | 신설 | AC2 |
| `tests/portfolio/test_config_loader.py` | 신설 | AC3 |
| `tests/portfolio/test_orchestrator_ab_registration.py` | 신설 | AC4 |
| `docs/specs/strategies/momo-btc-v2.md` | 수정 (프로덕션 구성 + 롤백 런북) | AC5 |
| `docs/work/done/000085-meta-labeler-lightgbm/02_implementation.md` | 수정 (#94 활성화 후속링크 한 줄) | AC1 |

### 검증 / 테스트 전략

- **단위 테스트 (신설 3개)**:
  - `tests/ml/test_meta_labeler_alias.py` — 정상 디렉토리 / pointer.json alias 두 경로 동치, 잘못된 alias 시 FileNotFoundError, alias 가 자기 자신을 가리키는 경우 무한 루프 가드 (1-hop 만 허용)
  - `tests/portfolio/test_config_loader.py` — YAML 파싱 → 두 전략 객체 dependency-injection 검증 + import-string 잘못된 경우 명시적 에러
  - `tests/portfolio/test_orchestrator_ab_registration.py` — register 후 `orch._strategies` dict 와 quarantine 분리 동작
- **통합 시나리오**:
  - 실 `models/momo-btc-v2/latest/` 가 없는 CI 환경에서 loader 가 `metalabeler.load_path` 항목을 만나면 **명확한 에러 메시지** 와 함께 fail (skip 아님 — fail-fast). 단 `production.yaml` 자체는 dev 환경에서 옵션 키로 분기.
  - tests 는 fixture 모델 디렉토리 (작은 toy lgbm booster) 를 `tmp_path` 에 생성 후 로딩
- **회귀 가드**:
  - 기존 `tests/backtest/test_momo_btc_v2_metalabeler.py` 4 케이스 그대로 PASS
  - `python scripts/check_invariants.py --strict` 100 노트 통과
- **명령**:
  - `pytest tests/portfolio/ tests/ml/test_meta_labeler_alias.py tests/backtest/test_momo_btc_v2_metalabeler.py -v`
  - `python scripts/check_invariants.py --strict`

### Guardrails

**Must Have**:
- on/off 두 strategy_id 가 **별개 인스턴스** (deep-copy 또는 두 번 ctor 호출). 공유 시 한쪽 상태 변이가 양쪽에 영향.
- `pointer.json` alias 1-hop 만 허용 (재귀 금지)
- `MetaLabeler.load` 실패 시 fail-fast → 오케스트레이터 부팅 중단 (silently `metalabeler=None` 으로 fallback 금지 — 의도치 않은 bypass 방지)
- `configs/orchestrator/.ai.md` 신설 (불변식 #5)
- 롤백 = 단일 파일 (`production.yaml`) 1-라인 주석 변경

**Must NOT Have**:
- LLM 이 `metalabeler_threshold` 또는 strategy 파라미터를 결정 (불변식 #6 정면 위반). loader 는 정적 YAML 만 읽는다.
- LLM 이 `register_strategy` 호출 (LLM 도구 표면에서 격리 — `_async_orchestrator.py` 의 모듈-레벨 주석 그대로 유지)
- Windows 환경 비호환 심볼릭 링크 (`os.symlink`) 사용 금지
- `.gitignore` 의 `models/` 룰 변경 금지 (5MB+ binary commit 회피)
- `metalabeler=None` silent fallback (load 실패 시 오케스트레이터 종료해야 함)
- 학습 시점 `feature_names` 와 `_extract_metalabeler_features` 출력 컬럼 mismatch silently — loader 또는 첫 `on_bar` 가 명시적으로 검증

### 엣지 케이스 / 주의

- **Windows 심볼릭 링크 금지**: 위 alias 방식 채택 사유. CI (Linux) + 로컬 (Windows 11) 양쪽 결정적 동작.
- **feature 컬럼 순서 / 누락**: `manifest.json::feature_names` 와 strategy 의 `_extract_metalabeler_features` 가 반환하는 DataFrame 컬럼이 일치해야 함. `predict_proba` 가 `X[self._feature_names]` 로 indexing 하므로 누락 시 KeyError. 현재 학습 스크립트는 `["rsi","atr","divergence_magnitude","bars_since_pivot","confidence","close","volume"]` 7개 — 일치 확인됨.
- **빈 returns 시리즈**: `register_strategy_returns(sid, pd.Series(dtype=float))` 로 init 시 `_SyncStrategyOrchestrator.refresh_portfolio_risk` 가 `len(self._returns) < 2` 또는 행 < 2 → None 반환. report=None 일 때 `evaluate` 가 정상 동작하는지 (현재 코드: `Snapshot(portfolio_risk=None)` 통과) 확인.
- **strategy_id 충돌**: dict 덮어쓰기 → loader 에 명시적 set 검사 추가.
- **파일 부재**: dev 환경에서 `models/momo-btc-v2/latest/pointer.json` 없을 때 loader 동작 — fail-fast (CI 와 동일).
- **manifest 호환성**: `MetaLabelerConfig` 에 새 필드 추가될 경우 `MetaLabelerConfig(**manifest.get("config", {}))` 가 TypeError 가능. config 키 화이트리스트 또는 `dataclass` field-name 필터 권장 (이번 이슈 범위 외; 현재 `train_metalabeler_btc.py` 가 추가한 `strategy_id` 등은 manifest 의 최상위 키이지 `config` 안이 아님 → 안전).
- **AC1 경로 표기 불일치**: 이슈 body 는 `docs/work/done/000085-...` 라고 명시 — 본 워크트리에 이미 done 폴더에 존재함을 글롭으로 확인. 추가 작업은 "이미 PASS" 사실 재확인 + #94 후속 링크 한 줄 추가만.

### 의존성 / 차단 요인

- **선행 #85**: 머지 완료 (PR #101). MetaLabeler/triple-barrier/PurgedKFold 모듈 + 학습 스크립트 + AC4 PASS 결과 모두 확보됨.
- **병행 #80 Shadow Paper**: AC5 ("프로덕션 턴온 후 첫 주 Shadow Paper 로그") 는 #80 인프라 의존. 본 이슈는 **켜기 (등록 + alias) 까지** 가 코드 변경 범위. 1주 모니터링 결과 자체는 본 PR 머지 후 별도 후속 작업.
- **선행 #95 (자동 재학습)**: 미존재. 본 PR 은 정적 alias `latest` 만; 월별 갱신은 #95 의 책임.
- **모델 아티팩트 물리 위치**: `.gitignore` 로 git 외부 관리. PR 자체는 alias 메커니즘 + loader 만 포함. 실 아티팩트는 (a) 운영자가 `scripts/promote_metalabeler.py` 를 로컬·CI 에서 실행 (b) 또는 별도 모델 레지스트리 도입 (#95 후속). 본 이슈는 alias 인프라까지.
- **Async 어댑터**: `MomoBtcV2.on_bar(bar, history, context)` 와 오케스트레이터의 `on_bar(ctx)` 시그니처 차이 → 본 PR 의 `_strategy_adapter.py` 가 작은 brige 역할. 기존 다른 전략 (`breakout_donchian`, `meanrev_pairs`, `momo_vol_filtered`) 의 등록 경로도 같은 어댑터로 통일 가능하나, 본 이슈 범위 외 — 본 PR 은 momo-btc-v2 전용 어댑터만.
