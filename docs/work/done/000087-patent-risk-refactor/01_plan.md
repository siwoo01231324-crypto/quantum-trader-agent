# 구현 계획 (확정)

> Issue #87 — chore: 특허 조사(#84) 차용 리팩토링 일괄 — 리스크/유니버스 강화
> 확정일: 2026-04-24

---

## RALPLAN-DR

### Principles (5)

1. **특허 회피** — 청구항 전체 구성요소를 단일 모듈에 구현하지 않는다. 각 모듈 상단에 특허 출처 주석을 학술 참고로 기재한다.
2. **LLM 미개입** — CLAUDE.md 불변식 #6: 주문 실행/리스크 결정을 LLM에 위임하지 않는다. 모든 함수는 순수 수학 함수로 구현한다.
3. **기존 테스트 회귀 0** — `test_portfolio_risk.py`, `test_risk_sizing.py`, `test_risk_dsl.py`, `test_portfolio_orchestrator.py` 기존 assertion 변경 금지. 신규 필드는 Optional + 기본값으로 추가.
4. **pure functions / 외부 I/O 없음** — `stability_grade`, `consensus_kelly`, `user_risk_vol_target`, `historical_cvar_levels`, `equal_risk_contribution_convex`, `hrp_with_clustering` 모두 외부 네트워크/DB/파일 접근 없이 인메모리 입력만 처리.
5. **pyproject 의존성 최소** — cvxpy 배제. scipy는 scikit-learn>=1.4 의 transitive 의존성을 direct 로 승격 (SLSQP + hierarchy). Python 3.14/Windows 호환 검증된 휠만 허용.

### Decision Drivers (Top 3)

| 순위 | Driver | 영향 범위 |
|------|--------|-----------|
| 1 | **Python 3.14 호환** | ERC/HRP 구현 백엔드 선택. cvxpy 빌드 휠 미제공 → scipy SLSQP 강제 | 
| 2 | **특허 회피** | 모든 7개 구현의 설계 경계 결정. 청구항 구성요소 분리 기준 |
| 3 | **기존 포트폴리오 리스크 경로 보존** | PortfolioRiskReport/evaluate() 변경 시 additive-only. frozen model extra=forbid → 신규 필드는 Optional |

### Viable Options

#### (i) ERC 최적화 백엔드

| 옵션 | 장점 | 단점 |
|------|------|------|
| **A. scipy.optimize.minimize(SLSQP)** | Python 3.14 호환, 이미 numpy/scipy 생태계, 설치 안정, Windows 네이티브 휠 | 유일해 수학적 보장 없음 (다만 ERC는 볼록 재정식화 시 유일해), 수렴 실패 시 fallback 필요 |
| **B. cvxpy + SCS/ECOS** | 볼록 문제 유일해 보장, DSL 수준 문제 정의 | Python 3.14 빌드 실패 이력, Windows 휠 불안정, 의존성 트리 대형 (SCS/ECOS C 라이브러리), pyproject 복잡도 증가 |

**결정: 옵션 A (scipy SLSQP)**
- cvxpy 무효화 사유: Python 3.14 Windows에서 SCS/ECOS C 확장 빌드 실패 보고 다수. 프로젝트 제약 "cvxpy 도입 금지" 명시. ERC 볼록 재정식화를 scipy SLSQP로 풀면 실용적으로 유일해 수렴 (초기값 1/N + PSD 보장).

#### (ii) stability_grade 데이터 수집 전략

| 옵션 | 장점 | 단점 |
|------|------|------|
| **A. 순수 함수 (호출자 주입)** | 테스트 용이, 외부 의존성 0, 모듈 경계 명확, 특허 회피 (데이터 수집 경로 미구현) | 호출자가 데이터를 준비해야 함 (CoinGecko 어댑터 별도 이슈) |
| **B. 내장 어댑터** | 원스톱 호출 | 외부 API 의존, 테스트 비결정성, 모듈 경계 침범, rate limit 관리 필요 |

**결정: 옵션 A (순수 함수)**
- 옵션 B 무효화 사유: 프로젝트 제약 "stability_grade 데이터 수집 X — pure function" 명시. CoinGecko 어댑터는 별도 chore 이슈 후보.

---

## 1. 아키텍처 결정

| # | 항목 | 결정 | 근거 |
|---|------|------|------|
| 1 | ERC 백엔드 | scipy SLSQP | Python 3.14 Windows 호환. cvxpy SCS/ECOS 빌드 불안정. 프로젝트 제약 명시 |
| 2 | 모듈 분리: position_sizer | `src/risk/position_sizer.py` 신규 (ERC + HRP 동거) | sizing.py는 Kelly/Vol 단일자산 사이저. position_sizer는 다자산 포트폴리오 가중치 — 관심사 분리 |
| 3 | 모듈 분리: stability_grade | `src/universe/stability_grade.py` + `src/universe/__init__.py` | 리스크와 유니버스는 직교 관심사. universe 패키지 신규 생성 |
| 4 | DSL 확장 방향 | `PerPortfolioRisk`에 `cvar_levels` + `extreme_fear_block` Optional 필드 추가 | extra=forbid 모델이므로 필드 추가 필수. 기본 None → 기존 YAML 파싱 회귀 0 |
| 5 | 테스트 폴더링 보류 | tests/ 루트 flat 유지 | 별도 chore 이슈 스코프. 본 이슈에서 신규 파일 7개 → 기존 6개 + 7개 = 13개. 10개 초과하나 구조 변경은 스코프 밖 |
| 6 | dev_activity optional | `stability_grade.grade(dev_activity=None)` 시 가중치 자동 재분배 (mcap:0.5 / volume:0.5) | dev_activity 데이터 가용성이 프로젝트마다 다름. 누락 시에도 등급 산출 가능해야 함 |
| 7 | extreme_fear default off | `extreme_fear_block` YAML 키 기본 미포함. DSL strict schema에서 Optional[float] = None | 기존 policies/*.yaml 파싱 회귀 0. 활성화는 명시적 YAML 편집으로만 |
| 8 | consensus_kelly default off | `momo_btc_v2.py`에 `use_consensus_kelly: bool = False` 옵션만 추가 | 기존 테스트 회귀 0. 활성화는 의도적 파라미터 변경으로만 |
| 9 | HRP fallback | `k_clusters=None` → 단일 HRP (기존 동작). k_clusters 지정 시에만 2단계 | N<50 docstring 권고. 안전한 기본값 |
| 10 | 리스크 모델 필드 추가 | `Snapshot`에 `fear_greed_proxy: Optional[float] = None`, `PortfolioRiskReport`에 `cvar_levels: Optional[dict] = None` | `compute_portfolio_risk_from_df()`는 returns만 수신 — price 기반 fear_greed_proxy를 여기서 산출 불가. Snapshot은 orchestrator가 조립하므로 price history 접근 가능. Snapshot·PortfolioRiskReport 모두 extra=forbid, Optional + None 기본값 → 기존 생성 코드 변경 불필요 |
| 11 | scipy 의존성 승격 | `pyproject.toml` dependencies에 `"scipy>=1.10"` direct 추가 (scikit-learn>=1.4 transitive 의존성 승격) | ERC(SLSQP) + HRP(hierarchy)에서 직접 import. scipy.optimize.minimize(SLSQP) + scipy.cluster.hierarchy 모두 scipy 1.0+ 에서 사용 가능. scikit-learn transitive floor(>=1.10) 과 일치 |
| 12 | fear_greed_proxy 산출 | `close_latest / rolling_max(252d)` 정규화 | 소셜 크롤 금지. 가격 기반 단순 지표만. 252 = 1거래년 |

---

## 2. 단계별 구현 순서 (TDD Red-Green)

### Phase 1: 기반 인프라 (의존성 0)

**scope**: `src/universe/` 패키지 생성, `pyproject.toml` scipy direct 의존성 승격

| 단계 | 작업 | 파일 |
|------|------|------|
| 1a | `src/universe/__init__.py` 생성 (빈 패키지) | 신규 |
| 1b | `src/universe/.ai.md` 생성 | 신규 |
| 1c | `pyproject.toml`에 `scipy>=1.10` direct 의존성 승격 | 수정 |

**의존성**: 없음. 모든 후속 Phase의 전제.

---

### Phase 2: P1 vol_target R1 파라미터화 (의존성: Phase 1)

| 단계 | 작업 | 파일 |
|------|------|------|
| RED | `test_user_risk_vol_target()` — risk_score=[0.0, 0.5, 1.0] 파라메트릭 | `tests/test_sizing_user_risk.py` (신규) |
| GREEN | `user_risk_vol_target(risk_score, vol_floor=0.05, vol_ceil=0.20) -> float` | `src/risk/sizing.py` (수정) |
| REFACTOR | `__init__.py` export 추가 | `src/risk/__init__.py` (수정) |

**AC 검증**: `user_risk_vol_target(0.0) == 0.05`, `user_risk_vol_target(1.0) == 0.20`, `user_risk_vol_target(0.5) == 0.125`. 기존 `vol_target()` 시그니처/동작 불변.

---

### Phase 3: P5 consensus_kelly (의존성: Phase 1)

| 단계 | 작업 | 파일 |
|------|------|------|
| RED | `test_consensus_kelly_*` — base=0.5, max=0.75, agreement=[0.0, 0.5, 1.0] | `tests/test_consensus_kelly.py` (신규) |
| GREEN | `consensus_kelly(full_kelly, signal_agreement, k_base=0.5, k_max=0.75) -> float` | `src/risk/sizing.py` (수정) |
| GREEN | `momo_btc_v2.py`에 `use_consensus_kelly: bool = False` 옵션 배선 (기본 비활성) | `src/backtest/strategies/momo_btc_v2.py` (수정) |
| REFACTOR | `__init__.py` export 추가 | `src/risk/__init__.py` (수정) |

**AC 검증**: `consensus_kelly(1.0, 0.0) == fractional_kelly(1.0, 0.5)`, `consensus_kelly(1.0, 1.0) == fractional_kelly(1.0, 0.75)`. momo_btc_v2 기본 동작 불변 (`use_consensus_kelly=False`). 기존 `tests/test_risk_sizing.py` 회귀 0.

**의존성**: Phase 2와 독립 (병렬 가능).

---

### Phase 4: P3 알트코인 안정성 등급 (의존성: Phase 1)

| 단계 | 작업 | 파일 |
|------|------|------|
| RED | `test_stability_grade_*` — 초대형=A, 마이크로캡=F, 중간=C, dev_activity None 시 재분배 | `tests/test_stability_grade.py` (신규) |
| GREEN | `StabilityGrade.grade(mcap_usd, vol_30d_usd, dev_activity=None) -> str` | `src/universe/stability_grade.py` (신규) |
| DOCS | 스펙 문서 | `docs/specs/universe-stability-grade.md` (신규) |

**AC 검증**: 등급 범위 A-F. `dev_activity=None` 시 가중치 mcap:0.5/volume:0.5 자동 재분배. 순수 함수, 외부 I/O 없음. 테스트(`test_stability_grade.py`)는 순수 함수 단위 테스트만 포함. DSL 통합 테스트 추가 금지.

**stability_grade DSL 배선 — deferred, out of scope for #87**: 이번 이슈 산출물은 `src/universe/stability_grade.py`의 pure function만. DSL 배선 (예: `PerPortfolioRisk.stability_grade_min: str | None` 블록 추가) 및 `evaluate_order`에서 필터 적용 로직은 별도 follow-up chore 이슈로 처리.

**의존성**: Phase 2/3과 독립 (병렬 가능).

---

### Phase 5: 다중 alpha CVaR 계층 + fear_greed_proxy (의존성: Phase 1)

| 단계 | 작업 | 파일 |
|------|------|------|
| RED | `test_cvar_levels_*` — 3-alpha 반환값 구조, 기존 historical_cvar 호환 | `tests/test_cvar_levels.py` (신규) |
| RED | `test_fear_greed_proxy_*` — 정규화 범위 [0,1], rolling_max 252d | `tests/test_fear_greed_proxy.py` (신규) |
| GREEN | `historical_cvar_levels(returns, levels=[(0.95,"warn"),(0.975,"reduce"),(0.99,"halt")]) -> dict` | `src/risk/portfolio.py` (수정) |
| GREEN | `PortfolioRiskReport`에 `cvar_levels: Optional[dict] = None` 필드 추가 | `src/risk/portfolio.py` (수정) |
| GREEN | `Snapshot`에 `fear_greed_proxy: Optional[float] = None` 필드 추가 | `src/risk/dsl.py` (수정) |
| GREEN | `compute_fear_greed_proxy(price_history: pd.Series) -> float` 헬퍼 추가, `evaluate_order()`에서 호출하여 Snapshot에 주입. `refresh_portfolio_risk()` 시그니처 불변. `cvar_levels` 배선만 orchestrator에서 처리 | `src/portfolio/orchestrator.py` (수정) |
| GREEN | DSL `PerPortfolioRisk`에 `cvar_levels` + `extreme_fear_block` Optional 필드 추가 | `src/risk/dsl.py` (수정) |
| GREEN | `evaluate()`에 `cvar_levels` + `extreme_fear_block` 평가 경로 추가. `extreme_fear_block`은 `snap.fear_greed_proxy`를 직접 참조 (not `snap.portfolio_risk.fear_greed_proxy`) | `src/risk/dsl.py` (수정) |
| DOCS | risk-rule-dsl.md에 YAML 스키마 업데이트 (cvar_levels 배열 + extreme_fear_block) | `docs/specs/risk-rule-dsl.md` (수정) |
| REFACTOR | `__init__.py` export 추가 | `src/risk/__init__.py` (수정) |

**AC 검증**: `historical_cvar_levels()` 반환 dict에 3개 alpha별 cvar_pct + label + action. 기존 `historical_cvar(alpha=0.975)` 호출부 완전 보존. `PortfolioRiskReport` 기존 필드 불변 (`cvar_levels`만 추가). `Snapshot` 기존 필드 불변 (`fear_greed_proxy`만 추가, Optional 기본 None). 기존 policies/*.yaml 파싱 회귀 0 (신규 키 없으면 None). `evaluate()`에서 `extreme_fear_block` 평가 시 `snap.fear_greed_proxy` 직접 참조.

**`cvar_levels` vs `max_cvar_pct` 공존 의미론**: `cvar_levels`와 `max_cvar_pct`는 독립 평가 / first-violation-wins. 기존 `src/risk/dsl.py:209-223`의 선형 스캔 패턴 유지. `max_cvar_pct`가 먼저 체크되고, 그 다음 `cvar_levels` 각 레벨이 순서대로 체크됨. 둘 중 먼저 위반되는 게 승. override 의미론 없음.

**의존성**: Phase 2/3/4와 독립. 다만 Phase 6/7보다 먼저 완료 권장 (DSL 확장이 evaluate_order 필터에 선행).

---

### Phase 6: ERC 볼록 근사 (의존성: Phase 1 scipy)

| 단계 | 작업 | 파일 |
|------|------|------|
| RED | `test_erc_*` — N=3 균등, N=10 비대각, N=50 stress, 수렴 실패 시 IVP fallback | `tests/test_position_sizer_erc.py` (신규) |
| GREEN | `equal_risk_contribution_convex(cov, target_contrib=None) -> np.ndarray` | `src/risk/position_sizer.py` (신규) |
| REFACTOR | `__init__.py` export 추가 | `src/risk/__init__.py` (수정) |

**AC 검증**:
- N=3 identity cov → 균등 가중 `[1/3, 1/3, 1/3]` (tolerance 1e-4).
- N=50 random PSD cov → 수렴 성공, 가중치 합 = 1.0, 모든 가중치 > 0.
- 조건수 높은 cov → SLSQP 실패 시 IVP(역분산) fallback 반환 + assertion.
- scipy SLSQP 사용 (cvxpy 없음).

**의존성**: Phase 5와 독립 (병렬 가능).

---

### Phase 7: 2단계 클러스터-HRP (의존성: Phase 1 scipy)

| 단계 | 작업 | 파일 |
|------|------|------|
| RED | `test_hrp_*` — k_clusters=None fallback, k=3 2단계, 가중치 합=1.0, 음수 가중치 없음 | `tests/test_position_sizer_hrp.py` (신규) |
| GREEN | `hrp_with_clustering(returns, k_clusters=None) -> np.ndarray` | `src/risk/position_sizer.py` (수정 — Phase 6에서 생성된 파일) |
| REFACTOR | `__init__.py` export 추가 | `src/risk/__init__.py` (수정) |

**AC 검증**:
- `k_clusters=None` → 단일 HRP fallback (기존 동작과 동등).
- `k_clusters=3`, N=20 → 가중치 합 = 1.0, 모든 가중치 >= 0.
- scipy.cluster.hierarchy만 사용 (외부 클러스터링 라이브러리 없음).

**의존성**: Phase 6과 동일 파일이므로 Phase 6 완료 후 순차 실행.

---

### Phase 8: 마무리 (의존성: Phase 2~7 전체)

| 단계 | 작업 | 파일 |
|------|------|------|
| 8a | `.ai.md` 갱신 — src/risk/, src/portfolio/, src/universe/ | 3개 파일 수정/신규 |
| 8b | `check_invariants.py --strict` 통과 확인 | 검증 |
| 8c | 전체 테스트 스위트 실행 — 기존 + 신규 7개 파일 회귀 0 확인 | 검증 |
| 8d | 특허 출처 주석 확인 (각 신규 모듈 상단) | 검증 |

---

## 3. 변경/신규 파일 목록

### src/

| 파일 | 상태 | 설명 |
|------|------|------|
| `src/risk/sizing.py` | 수정 | `user_risk_vol_target()` + `consensus_kelly()` 추가 |
| `src/risk/portfolio.py` | 수정 | `historical_cvar_levels()` 추가, `PortfolioRiskReport`에 `cvar_levels` 필드 |
| `src/risk/dsl.py` | 수정 | `Snapshot`에 `fear_greed_proxy` 필드, `PerPortfolioRisk`에 `cvar_levels` + `extreme_fear_block` 필드, `evaluate()`에 평가 경로 |
| `src/risk/position_sizer.py` | **신규** | `equal_risk_contribution_convex()` + `hrp_with_clustering()` |
| `src/risk/__init__.py` | 수정 | 신규 함수 4개 + 클래스 export |
| `src/risk/.ai.md` | 수정 | position_sizer 모듈 설명 추가 |
| `src/portfolio/orchestrator.py` | 수정 | `compute_fear_greed_proxy()` 헬퍼 추가, `evaluate_order()`에서 Snapshot에 fear_greed_proxy 주입, `refresh_portfolio_risk()`에 cvar_levels 배선. `refresh_portfolio_risk()` 시그니처 불변 |
| `src/portfolio/.ai.md` | 수정 | fear_greed_proxy 흐름 설명 추가 |
| `src/universe/__init__.py` | **신규** | 패키지 초기화 |
| `src/universe/stability_grade.py` | **신규** | `StabilityGrade.grade()` A~F 등급 분류 |
| `src/universe/.ai.md` | **신규** | 유니버스 모듈 목적/구조/역할 |
| `src/backtest/strategies/momo_btc_v2.py` | 수정 | `use_consensus_kelly: bool = False` 옵션 추가 |

### tests/

| 파일 | 상태 | 설명 |
|------|------|------|
| `tests/test_sizing_user_risk.py` | **신규** | P1 user_risk_vol_target 파라메트릭 테스트 |
| `tests/test_consensus_kelly.py` | **신규** | P5 consensus_kelly agreement 스위프 |
| `tests/test_stability_grade.py` | **신규** | P3 등급 경계값 + dev_activity None |
| `tests/test_cvar_levels.py` | **신규** | 다중 alpha CVaR 구조 + 기존 호환 |
| `tests/test_fear_greed_proxy.py` | **신규** | 정규화 범위 + rolling_max 로직 |
| `tests/test_position_sizer_erc.py` | **신규** | ERC N=3/10/50 + IVP fallback |
| `tests/test_position_sizer_hrp.py` | **신규** | HRP k_clusters=None fallback + 2단계 |

### docs/

| 파일 | 상태 | 설명 |
|------|------|------|
| `docs/specs/risk-rule-dsl.md` | 수정 | cvar_levels YAML 스키마 + extreme_fear_block 블록 |
| `docs/specs/universe-stability-grade.md` | **신규** | 안정성 등급 스펙 (frontmatter type: spec-architecture) |

### 기타

| 파일 | 상태 | 설명 |
|------|------|------|
| `pyproject.toml` | 수정 | `scipy>=1.10` direct 의존성 승격 (scikit-learn transitive → explicit) |

---

## 4. AC 매핑

| AC # | AC 설명 | 검증 테스트 | 검증 함수 |
|------|---------|-------------|-----------|
| 1 | P1 vol_target R1 파라미터화 | `tests/test_sizing_user_risk.py` | `test_user_risk_vol_target_floor`, `test_user_risk_vol_target_ceil`, `test_user_risk_vol_target_mid`, `test_user_risk_vol_target_rejects_invalid` |
| 2 | P3 알트코인 안정성 등급 | `tests/test_stability_grade.py` | `test_grade_large_cap_is_A`, `test_grade_micro_cap_is_F`, `test_grade_mid_is_C`, `test_grade_dev_activity_none_reweights` |
| 3 | P4 fear_greed_proxy | `tests/test_fear_greed_proxy.py` | `test_proxy_range_zero_one`, `test_proxy_at_rolling_max_is_one`, `test_proxy_at_half_max_is_half`, `test_snapshot_field_optional_default_none` |
| 4 | P5 consensus_kelly | `tests/test_consensus_kelly.py` | `test_zero_agreement_equals_base`, `test_full_agreement_equals_max`, `test_mid_agreement_linear`, `test_delegates_to_fractional_kelly` |
| 5 | 다중 alpha CVaR 계층 | `tests/test_cvar_levels.py` | `test_three_levels_returned`, `test_existing_cvar_preserved`, `test_dsl_cvar_levels_yaml_parse`, `test_dsl_extreme_fear_block_default_off` |
| 6 | ERC 볼록 근사 | `tests/test_position_sizer_erc.py` | `test_erc_identity_cov_equal_weight`, `test_erc_weights_sum_one`, `test_erc_50_asset_stress`, `test_erc_ill_conditioned_fallback_ivp` |
| 7 | 2단계 클러스터-HRP | `tests/test_position_sizer_hrp.py` | `test_hrp_k_none_single_fallback`, `test_hrp_k3_two_stage`, `test_hrp_weights_sum_one_nonneg`, `test_hrp_scipy_hierarchy_only` |
| 공통 | 특허 출처 주석 | 수동 검증 | 각 신규 모듈 상단 docstring 확인 |
| 공통 | check_invariants --strict | CI | `scripts/check_invariants.py --strict` 종료 코드 0 |
| 공통 | 기존 테스트 회귀 0 | 기존 테스트 스위트 | `test_risk_dsl.py`, `test_risk_sizing.py`, `test_portfolio_risk.py`, `test_portfolio_orchestrator.py` 전체 PASS |
| 공통 | .ai.md 갱신 | 수동 검증 | src/risk/, src/portfolio/, src/universe/ 각 .ai.md에 신규 모듈/함수 반영 |

---

## 5. 엣지케이스/리스크

| # | 리스크 | Trigger | Response | Gate |
|---|--------|---------|----------|------|
| 1 | **PortfolioRiskReport.cvar_levels 필드 회귀** | 기존 `test_portfolio_risk.py` 의 스냅샷 assertion이 unknown field로 실패 | `cvar_levels: Optional[dict] = None` 으로 추가. extra=forbid 모델이므로 필드 선언 필수, 기본 None → 기존 생성 코드에서 미전달 시 자동 None. **fear_greed_proxy는 PortfolioRiskReport가 아닌 Snapshot에 추가되므로 이 리스크는 cvar_levels 1건만 해당** | `test_portfolio_risk.py` 전체 PASS 확인 후 Phase 5 GREEN 종료 |
| 2 | **Snapshot.fear_greed_proxy 필드 회귀** | Snapshot(extra=forbid)에 신규 Optional 필드 추가 시 기존 Snapshot 생성 경로 영향 | `fear_greed_proxy: Optional[float] = None` 기본값 None → 기존 `evaluate_order()` 의 `Snapshot(...)` 생성 경로 불변 (snap_extras로 선택 주입). orchestrator.py:116-120 에서 기존 키워드 인자에 영향 없음 | `test_risk_dsl.py` + `test_portfolio_orchestrator.py` 전체 PASS 확인 |
| 3 | **historical_cvar_levels 성능** | 동일 returns에 3번 percentile 계산 | O(N log N) x 3. N=200 기준 < 1ms. 명시적 벤치마크 불필요 | 성능 문제 발생 시 단일 정렬 후 3개 cutoff 인덱싱으로 최적화 (후속) |
| 4 | **scipy SLSQP 수렴 실패** | 조건수 높은 공분산 행렬 (near-singular) | 초기값 `1/N` 균등 가중, `maxiter=300`. 실패 시 IVP(역분산) fallback 반환. 반환 직전 `assert abs(sum(w) - 1.0) < 1e-6` | `test_erc_ill_conditioned_fallback_ivp` 테스트 |
| 5 | **N<20에서 2단계 HRP가 단일 HRP 대비 손해** | k_clusters 지정 시 오버피팅 가능 | `k_clusters=None` → 단일 HRP fallback. docstring에 "N<50은 단일 HRP 권고" 명시 | `test_hrp_k_none_single_fallback` 테스트 |
| 6 | **DSL extreme_fear_block 정책 충돌** | 기존 policies/*.yaml에 이 키가 없음 | `extreme_fear_block: Optional[float] = None` (기본 비활성). strict schema이므로 Optional 선언 필수 | 기존 3개 policy YAML 파싱 테스트 추가 (test_cvar_levels.py 내) |
| 7 | **momo_btc_v2 consensus_kelly 회귀** | `use_consensus_kelly=True`로 설정 시 기존 signal_agreement 미전달 | `use_consensus_kelly=False` 기본값. True일 때 signal_agreement 미전달 시 0.0 기본값 (k_base 사용) | `tests/test_risk_sizing.py` 기존 momo 테스트 전체 PASS |
| 8 | **scipy 미설치 환경** | `import scipy` 실패 | `position_sizer.py` 모듈 레벨 import. scipy가 pyproject.toml dependencies에 포함되므로 정상 설치 환경에서는 발생 안 함. 미설치 시 ImportError (fail-fast) | pyproject.toml 의존성 확인 |

---

## 6. 롤백 전략

| Phase | 독립 Revert 가능? | 파일 원자성 | 마이그레이션 필요 |
|-------|-------------------|-------------|-------------------|
| P2 (vol_target R1) | **가능** | `sizing.py` 함수 추가만. 제거 시 기존 코드 영향 0 | 없음 |
| P3 (consensus_kelly) | **가능** | `sizing.py` 함수 추가 + `momo_btc_v2.py` 옵션 추가. 옵션 기본 False이므로 제거해도 기존 동작 보존 | 없음 |
| P4 (stability_grade) | **가능** | `src/universe/` 전체 삭제로 완전 롤백. 다른 모듈에서 import하지 않으므로 (evaluate_order 필터는 Phase 5에서 배선) | 없음 |
| P5 (CVaR levels + fear_greed_proxy) | **조건부** | `PortfolioRiskReport`에 `cvar_levels` 필드 + `Snapshot`에 `fear_greed_proxy` 필드 + `dsl.py` `PerPortfolioRisk` 필드 추가는 schema-level 변경. Revert 대상 파일: `src/risk/portfolio.py` (cvar_levels), `src/risk/dsl.py` (fear_greed_proxy on Snapshot + PerPortfolioRisk 필드), `src/portfolio/orchestrator.py` (compute_fear_greed_proxy 헬퍼 + cvar_levels 배선). 이미 생성된 직렬화 데이터(JSON/pickle)가 있으면 역직렬화 실패 가능 | **있음**: 직렬화된 PortfolioRiskReport/Snapshot이 존재하면 필드 제거 시 extra=forbid에 걸림. 다만 현재 직렬화 경로가 없으므로 실질적 마이그레이션 불필요 |
| P6 (ERC) | **가능** | `position_sizer.py` 신규 파일. 제거 시 HRP도 함께 제거 필요 (동일 파일) | 없음 |
| P7 (HRP) | **P6과 결합** | 동일 파일 `position_sizer.py`. P6 없이 P7만 롤백하려면 함수 단위 제거 | 없음 |
| P8 (마무리) | **가능** | .ai.md + docs만. 코드 기능에 영향 없음 | 없음 |

**전체 롤백**: Phase별 독립 revert 가능 (P5 조건 주의). 가장 안전한 순서: P8 → P7 → P6 → P5 → P4 → P3 → P2 → P1 (역순).

---

## ADR (Architecture Decision Record)

**Decision**: 7개 특허 차용 구현을 scipy SLSQP 기반 + 순수 함수 설계로 단일 이슈에 통합 구현한다.

**Drivers**: Python 3.14 호환, 특허 회피, 기존 포트폴리오 리스크 경로 보존.

**Alternatives considered**:
1. cvxpy 기반 ERC — Python 3.14/Windows 빌드 불안정으로 기각.
2. stability_grade 내장 어댑터 — 외부 I/O 의존성 + 특허 구성요소 확대로 기각.
3. 7개 제안을 개별 이슈로 분리 — 상호 의존성 낮아 가능하나, DSL/Report 스키마 변경이 여러 이슈에 분산되면 충돌 리스크 증가. 단일 이슈 통합이 스키마 일관성 유지에 유리.

**Why chosen**: scipy는 numpy 생태계 내 안정 라이브러리로 Python 3.14 Windows 호환 검증 완료. 순수 함수 설계는 테스트 용이성 + 특허 회피(데이터 수집 경로 미구현) 동시 달성. 단일 이슈 통합은 PortfolioRiskReport 스키마 변경을 한 번에 처리.

**Consequences**:
- (+) scipy 의존성 추가로 향후 최적화/통계 함수 활용 폭 확대.
- (+) `src/universe/` 패키지 신설로 향후 유니버스 필터 확장 기반 마련.
- (-) tests/ 루트에 파일 13개로 증가. 별도 chore에서 폴더링 필요.
- (-) PortfolioRiskReport에 Optional 필드 1개(cvar_levels) + Snapshot에 Optional 필드 1개(fear_greed_proxy) 추가 — 향후 필드 증가 시 모델 비대화 모니터링 필요.

**Follow-ups**:
- CoinGecko 어댑터 (stability_grade 데이터 수집) — 별도 chore 이슈.
- stability_grade DSL 배선 (`PerPortfolioRisk.stability_grade_min` + `evaluate_order` 필터) — 별도 chore 이슈 (OQ3 deferred).
- tests/ 폴더링 — 별도 chore 이슈.
- consensus_kelly 활성화 + 백테스트 비교 — momo_btc_v2 전략 고도화 이슈에서.
- extreme_fear_block 정책 프리셋 추가 — policies/ YAML 업데이트.
