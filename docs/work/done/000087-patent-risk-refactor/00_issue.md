# chore: 특허 조사(#84) 차용 리팩토링 일괄 — 리스크·유니버스 강화

## 배경

#84 특허 리서치 결과 총 18개 강화 제안 중 기존 백로그 이슈(#76/#78/#80/#81/#85) 스코프와 **겹치지 않는 독립 7개 제안**을 본 이슈 하나로 통합 구현한다. 주로 리스크·유니버스 레이어 강화.

관련 리서치 노트:
- [[31-uprich-patent-analysis]]
- [[32-patents-portfolio-optimization]]
- #84 특허 리서치 이슈

## 범위 — 7 개 차용 구현

### A. 업리치 특허(KR 10-2024-0114873) 차용 4건

#### 1. P1 — `vol_target()` R1 사용자 프로필 파라미터화

**적용 대상**: `src/risk/sizing.py::vol_target(target_annual: float)`

**접목 방법**:
현재 `vol_target()` 은 `target_annual=0.10` 을 고정 기본값으로 사용한다. 업리치 특허의 R1(사용자 리스크 수용지수) 개념을 차용해, 호출자가 사용자 프로필 점수(`risk_score ∈ [0.0, 1.0]`)를 전달하면 `target_annual` 을 동적으로 결정하는 팩토리 함수를 `sizing.py` 에 추가한다:

```python
def user_risk_vol_target(
    risk_score: float,           # 0.0 = 보수적, 1.0 = 공격적
    vol_floor: float = 0.05,
    vol_ceil: float = 0.20,
) -> float:
    """risk_score 를 annualized vol target 으로 선형 매핑."""
    return vol_floor + (vol_ceil - vol_floor) * risk_score
```

`StrategyOrchestrator.evaluate_order()` 호출 전에 이 값을 `Snapshot` 에 주입하면, 동일 전략 신호에서도 사용자별로 서로 다른 포지션 크기가 산출된다. R1 의 입력변수(부채비율, 상환능력)는 우리 시스템이 다루는 영역이 아니므로 **`risk_score` 는 외부에서 주입** 하는 단순 스칼라로 충분하다 — 특허 청구항의 복잡한 R1 산출 로직 전체를 구현하지 않으므로 침해 구성요소 (a) 를 회피한다.

**기대 효과**: 단일 vol target 에서 사용자별 맞춤 리스크 버킷(보수·중립·공격)으로 전환. 멀티 계좌 운용 시 계좌별 drawdown 분산 개선.

**저비용 검증 경로**: `tests/test_sizing.py` 에 `risk_score=[0.0, 0.5, 1.0]` 에 대한 파라메트릭 테스트 3건 추가 → `vol_target()` 반환값이 `[0.05, 0.125, 0.20]` 범위에 드는지 assert.

---

#### 2. P3 — 알트코인 안정성 등급 (A~F) 유니버스 필터

**적용 대상**: 신규 모듈 `src/universe/stability_grade.py`

**접목 방법**:
현재 우리 시스템에는 알트코인 거래 유니버스를 필터링하는 전용 모듈이 없다. 업리치 특허의 A~F 안정성 등급 개념을 차용해, 시가총액·30일 평균 거래량·GitHub 커밋 활성도(온체인 대리지표) 세 가지를 입력으로 받는 단순 등급 분류기를 별도 모듈로 구현한다.

```python
class StabilityGrade:
    """A(최우수)~F(투기) 6단계 등급. 각 기준 독립 배점 합산."""

    WEIGHTS = {"mcap_score": 0.4, "volume_score": 0.4, "dev_score": 0.2}

    def grade(self, mcap_usd: float, vol_30d_usd: float, gh_commits_90d: int) -> str:
        score = (
            self._mcap_score(mcap_usd) * self.WEIGHTS["mcap_score"]
            + self._vol_score(vol_30d_usd) * self.WEIGHTS["volume_score"]
            + self._dev_score(gh_commits_90d) * self.WEIGHTS["dev_score"]
        )
        return "ABCDEF"[min(5, int((1 - score) * 6))]
```

이 등급을 `StrategyOrchestrator` 의 유니버스 필터 단계에 연결해, D등급 이하 알트코인은 매수 진입 차단 or 포지션 크기를 `fractional_kelly(k=0.25)` 로 강제 축소한다. 특허 청구항 (d) 의 세부 입력변수(온체인 개발 활성도 정의)를 다르게 구성하고 A~F 등급 정의도 자체 기준으로 재정의하므로 침해를 회피한다.

**기대 효과**: 유동성 낮은 알트코인 포지션 진입 방지 → 슬리피지·유동성 리스크 감소. 데이터 품질 기반 자동 유니버스 축소.

**저비용 검증 경로**: CoinGecko `/coins/markets` API 로 상위 200개 알트코인 등급 산출 → A-C 등급 비율 체크 (기대: 상위 30% 이내). 단위 테스트 3건 (경계값: 초대형 코인=A, 마이크로캡=F, 중간=C).

**회피**: 업리치 청구항 1-(d) 전체 구성요소 복제 금지. 등급 입력변수 재정의.

---

#### 3. P4 — PortfolioRiskReport `fear_greed_proxy` 필드

**적용 대상**: `src/risk/portfolio.py::PortfolioRiskReport` + `src/portfolio/orchestrator.py::StrategyOrchestrator.refresh_portfolio_risk()`

**접목 방법**:
업리치의 R2(소셜 감성 + 가격 + 거시경제 합산 지수) 개념에서 **가격 지표 컴포넌트만** 차용한다. `PortfolioRiskReport` 에 `fear_greed_proxy: float` 필드를 추가하고, `refresh_portfolio_risk()` 에서 이를 산출한다:

```python
# PortfolioRiskReport 에 추가
fear_greed_proxy: float = Field(0.5, ge=0.0, le=1.0,
    description="0=극단 공포, 1=극단 탐욕. 가격 기반 단순 추정.")
```

산출 방법: `(현재가격 / 52주_최고가)` 를 정규화한 단순 지수 — 소셜 크롤링 없이 가격 데이터만 사용하므로 특허 구성요소 (b) 의 웹크롤링 소셜 감성 요소를 의도적으로 제외. 이 값이 임계(0.2 이하=극단 공포)일 때 `evaluate_order()` 에서 신규 매수 차단 플래그를 반환하게 `dsl.py` 를 확장한다.

**기대 효과**: 시장 극단 공포 구간에서 자동 매수 보류 → 급락장 진입 리스크 완화. 기존 CVaR·ENB 외에 시장 심리 차원 리스크 신호 추가.

**저비용 검증 경로**: 2020-03 코로나 급락, 2022-11 FTX 붕괴 구간에서 `fear_greed_proxy` 값이 0.2 이하로 내려가는지 히스토리컬 백테스트 단일 플롯으로 확인.

---

#### 4. P5 — `consensus_kelly` 지표 합의도 기반 Kelly 배율

**적용 대상**: `src/risk/sizing.py::fractional_kelly()` + `src/backtest/strategies/momo_btc_v2.py`

**접목 방법**:
청구항 8의 "두 지표 모두 양수일 때 BTC 현물 롱 + 알트 선물 숏" 페어 전략 개념에서 **신호 방향 일치도에 따른 kelly 배율 조정** 아이디어를 차용한다. 현재 `fractional_kelly(full_kelly, k=0.5)` 는 단방향 신호에 고정 k를 적용하지만, 복수 지표가 같은 방향을 가리킬 때 k를 상향하는 `consensus_kelly()` 함수를 추가한다:

```python
def consensus_kelly(
    full_kelly: float,
    signal_agreement: float,  # 0.0~1.0: 지표 간 방향 일치도
    k_base: float = 0.5,
    k_max: float = 0.75,
) -> float:
    """지표 합의도가 높을수록 kelly 배율을 k_base~k_max 로 선형 상향."""
    k = k_base + (k_max - k_base) * signal_agreement
    return fractional_kelly(full_kelly, k)
```

`momo_btc_v2.py` 의 신호 생성 단계에서 momentum + vol regime + btc_dominance 세 신호의 방향 일치도를 `signal_agreement` 로 전달한다.

**기대 효과**: 지표 합의 구간에서 포지션 확대, 불일치 구간에서 자동 축소 → 신호 품질 기반 동적 사이징. 기존 고정 Half Kelly 대비 합의 구간 수익 개선 가능.

**저비용 검증 경로**: `momo_btc_v2` 백테스트에서 `k=0.5` 고정 vs `consensus_kelly` 비교 → Sharpe, max drawdown, 평균 포지션 크기 비교 리포트 1개.

---

### B. 포트폴리오 최적화 차용 3건

#### 5. 제안 A — 다중 α CVaR 계층화 (0.95/0.975/0.99 → warn/reduce/halt)

**적용 대상**: `src/risk/portfolio_orchestrator.py` (현재 `historical_cvar(α=0.975)` 단일 수준)

**접목 방법**: `PortfolioOrchestrator.compute_risk_report()` 또는 동등 메서드에서 현재 단일 α=0.975 CVaR 계산을 `[(0.95, 'warn'), (0.975, 'reduce'), (0.99, 'halt')]` 형태의 계층 구조로 확장한다. 각 레벨마다 [[risk-rule-dsl]]의 `per_portfolio_risk` 블록에 대응하는 액션(warn→reduce→halt)을 매핑하면 손실 분포의 상이한 꼬리 구간을 단계적으로 제어할 수 있다. 구현은 기존 `historical_cvar` 루프를 α 리스트로 파라미터화하는 것으로 충분하며 외부 의존성 추가 없이 가능하다.

**기대 효과**: 97.5% CVaR 임계치 도달 전 95% CVaR 수준에서 조기 경보를 발생시켜 급격한 할트 없이 점진적 리스크 감축이 가능해진다. 규제 보고 (Basel FRTB 97.5% ES + 내부 모니터링 95% ES 병행)와도 일치한다.

**저비용 검증**: 기존 `tests/test_portfolio_orchestrator.py`에 α 리스트를 파라미터로 받는 테스트 케이스 1건 추가 후 threshold별 액션 분기 확인.

**출처 특허**: Axioma US20210110479A1 (활성) — GUI 없이 백엔드만 구현하므로 청구항 전체 구성요소 미충족, 비침해.

---

#### 6. 제안 B — ERC 볼록 근사 수치 안정화 (포기 특허 자유 차용)

**적용 대상**: `src/risk/position_sizer.py` (현재 `ERC` 또는 `HRPOpt` 계열 함수)

**접목 방법**: [[20-position-sizing]] §4.2의 ERC 조건은 비볼록 방정식으로 수치 최적화에서 지역 최솟값에 빠질 수 있다. Goldman Sachs가 포기한 이 특허의 볼록 근사 접근법 — 원문제를 `min Σ_i (w_i·(Σw)_i - target)²` 형태의 순볼록 문제로 재정식화하는 방식 — 을 `position_sizer.py`의 ERC 계산 경로에 적용한다. PyPortfolioOpt의 `EfficientRisk` 또는 `cvxpy` 로 직접 구현 가능하며, 고유한 전역 해를 보장하므로 최적화 실패(`solver_error`) 빈도를 낮출 수 있다. 포기 특허이므로 법적 리스크 없음.

**기대 효과**: 종목 수 증가(20→50종목) 시 ERC 수렴 실패율 감소, 포트폴리오 리밸런스 안정성 향상.

**저비용 검증**: 현재 ERC 구현에 50종목 스트레스 테스트 추가, 볼록 근사 버전과 수렴 실패 횟수 비교.

**법적 리스크 0**: Goldman Sachs US20140081888A1 은 **포기 특허**. 가장 안전한 차용 후보.

---

#### 7. 제안 C — 2단계 클러스터-HRP 분해 (양자 컴포넌트 제외)

**적용 대상**: `src/risk/position_sizer.py`의 HRP 관련 함수 (현재 `HRPOpt` 기반 구현), 그리고 [[20-position-sizing]] §5 에서 언급한 재귀적 이등분(Recursive Bisection) 알고리즘

**접목 방법**: IBM 특허의 클러스터 분해 아이디어(양자 컴퓨팅 부분 제외)를 고전 최적화에 그대로 적용할 수 있다. 현재 HRP는 전체 공분산 행렬을 한 번에 처리하지만, 종목 수 N>100이 되면 계층적 클러스터링 → 서브클러스터별 평균-분산(또는 CVaR) 최적화 → 클러스터 간 HRP로 재귀 합산하는 **2단계 분해 구조**로 전환하면 계산 복잡도를 O(N³) → O(k·(N/k)³)으로 낮출 수 있다(k = 클러스터 수). 양자컴퓨터 전송 컴포넌트는 생략하고 클러스터별 `cvxpy` 최적화로 대체.

**기대 효과**: KOSPI 전종목(N≈2500) 또는 팩터 알파 파이프라인이 확장될 경우 현재 단일 HRP 호출의 메모리·시간 비용을 선형 축소. 또한 서브클러스터가 섹터·팩터 경계와 일치하면 포트폴리오 해석 가능성(interpretability)이 높아진다.

**저비용 검증**: N=200 모의 유니버스에서 단일 HRP vs 2단계 클러스터-HRP 샤프 비율 및 실행 시간 비교 (100회 롤링 백테스트).

**회피**: IBM US11562281B2 의 양자컴퓨터 부분 제외, 고전 HRP 만 구현.

---

## 완료 기준

### 1. P1 vol_target R1 파라미터화
- [ ] `src/risk/sizing.py::vol_target()` 에 `risk_score: float` 파라미터 추가
- [ ] 단위 테스트 1건 (risk_score=0.3 vs 0.9 비교)

### 2. P3 알트코인 안정성 등급 필터
- [ ] `src/universe/stability_grade.py` 신규 모듈
- [ ] 등급 A~F 분류 로직 + 입력 변수 재정의 (시총·유동성·변동성)
- [ ] 단위 테스트

### 3. P4 fear_greed_proxy 필드
- [ ] `src/risk/portfolio.py::PortfolioRiskReport` 에 `fear_greed_proxy: float` 필드 추가
- [ ] `src/risk/portfolio_orchestrator.py` 에 계산 경로 배선

### 4. P5 consensus_kelly
- [ ] `src/risk/sizing.py::consensus_kelly()` 신규 함수
- [ ] 지표 합의도 계산 로직

### 5. 다중 CVaR 계층
- [ ] `src/risk/portfolio_orchestrator.py` 에 α 리스트 파라미터화 (기본 `[0.95, 0.975, 0.99]`)
- [ ] 레벨별 `warn/reduce/halt` 액션 매핑
- [ ] `docs/specs/risk-rule-dsl.md` 에 `cvar_levels` 배열 스키마 추가

### 6. ERC 볼록 근사
- [ ] `src/risk/position_sizer.py::equal_risk_contribution_convex()` 신규
- [ ] 수치 안정화 테스트 (조건수 높은 covariance matrix)

### 7. 2단계 클러스터-HRP
- [ ] `src/risk/position_sizer.py::hrp_with_clustering()` 또는 `src/portfolio/hrp.py` 신규
- [ ] Linkage → 클러스터 → HRP 배분 2단계
- [ ] 양자 컴포넌트 제외 (고전 HRP 만)

### 공통
- [ ] 각 구현에 `docs/specs/strategies/` 또는 `docs/specs/risk-*.md` 스펙 작성
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] 특허 청구항 전체 구성요소를 한 모듈에 구현하지 않음 (회피설계 체크리스트)

## 법적 주의
- 변리사 리뷰 아님. 상용 직전 법무 검토 필수.
- 수식 직접 복제 금지 — 동등 목적이라도 다른 정의·파라미터.
- 각 모듈 상단에 특허 출처 주석 (학술 참고 표기).

## 관련 문서
- `docs/background/31-uprich-patent-analysis.md`
- `docs/background/32-patents-portfolio-optimization.md`
- `docs/work/active/000084-patent-research/00_issue.md` §후속 이슈 후보

## 연결 이슈
- #84 특허 리서치 (모선 이슈)
- #70 포트폴리오 리스크 관리 (다중 CVaR 선행)
- #69 포지션 사이징 (ERC·HRP 선행)



## 작업 내역

### 구현 (7 deliverables P1~P7)
- **P1 vol_target R1 파라미터화** — `src/risk/sizing.py::user_risk_vol_target(risk_score, vol_floor=0.05, vol_ceil=0.20)` + `tests/test_sizing_user_risk.py` (7건).
- **P3 알트코인 A~F 등급** — 신규 `src/universe/stability_grade.py` + `src/universe/.ai.md` + `docs/specs/universe-stability-grade.md` + `tests/test_stability_grade.py` (6건). pure function, 외부 I/O 없음.
- **P4 fear_greed_proxy** — `Snapshot.fear_greed_proxy` 필드 (dsl.py, NOT PortfolioRiskReport — `compute_portfolio_risk_from_df` 시그니처 불변) + `compute_fear_greed_proxy(price_history, window=252)` 헬퍼 (orchestrator) + `PerPortfolioRisk.extreme_fear_block` + `extreme_fear_threshold` + DSL evaluator 경로. 소셜 크롤 금지. `tests/test_fear_greed_proxy.py` (20건).
- **P5 cvar_levels** — `historical_cvar_levels(returns, levels)` (portfolio.py) + `PortfolioRiskReport.cvar_levels: Optional[dict]` + `PerPortfolioRisk.cvar_levels: Optional[list[tuple[float,str]]]` + DSL 독립 평가 / first-violation-wins. 기존 `max_cvar_pct` 경로 보존. `tests/test_cvar_levels.py` (22건).
- **P5 consensus_kelly** — `consensus_kelly(full_kelly, signal_agreement, k_base, k_max)` (sizing.py) + MomoBtcV2 `use_consensus_kelly: bool = False` 옵션 (기본 OFF, 기존 backtest 회귀 0). `tests/test_consensus_kelly.py` (10건).
- **P6 ERC 볼록 근사** — 신규 `src/risk/position_sizer.py::equal_risk_contribution_convex(cov, target_contrib=None)`. scipy SLSQP + IVP fallback. `tests/test_position_sizer_erc.py` (6건).
- **P7 2단계 클러스터-HRP** — 같은 파일 `hrp_with_clustering(returns, k_clusters=None)`. scipy.cluster.hierarchy 만 사용, k_clusters=None 시 단일 HRP fallback. `tests/test_position_sizer_hrp.py` (5건).

### 인프라
- `pyproject.toml` — scipy>=1.10 direct dependency **promotion** (scikit-learn transitive floor 매치, 신규 의존성 아님). cvxpy 배제 (Python 3.14/Windows 호환성).

### Planning / 합의
- `docs/work/done/000087-patent-risk-refactor/01_plan.md` — ralplan 합의 (Planner → Architect → Critic 라운드 2회), 5개 actionable edit 반영 (fear_greed_proxy 를 Snapshot 으로 이동, scipy version floor 명확화, OQ2/OQ3 해결, 리스크/롤백 테이블 업데이트).

### 발견 가능성 (discoverability) — 5개 수준 전부 확보
1. **Docstring + 타입힌트** — `momo_btc_v2.py` class docstring 에 `use_consensus_kelly` 옵션 명시.
2. **DSL 스펙 YAML 샘플** — `docs/specs/risk-rule-dsl.md` §3 (활성 예시), §7.1 (rule_id 확장), **§8.1 #87 확장 상세 (9행 기능 매핑표)**, §8 로드맵 v2.1.
3. **policies/*.yaml 주석 예시** — `conservative.yaml` / `neutral.yaml` / `aggressive.yaml` 3개 모두 `per_portfolio_risk` 활성 예시 블록 추가 (주석 처리).
4. **기능 카탈로그** — **신규 `docs/specs/feature-catalog.md`** (280+줄). Risk / Sizing / Universe / Strategy / Signals / Backtest 분야별 opt-in 기능 색인 + **§7 Dormant Features** — 이슈는 머지됐지만 프로덕션 호출 경로 없는 기능 대량 문서화:
   - 🔴 StrategyOrchestrator (#70), `required_factors` 훅 (#71), Execution 알고리즘 (#25), KRX handler, ops/triggers, tax calculator
   - 🟡 Binance/KIS Adapter (부분), 팩터 Parquet 캐시, lookahead_guard (테스트만), StabilityGrade (caller 없음)
   - 🟢 services/doc_agent, services/obsidian_mcp (active)
   - §7.6 라이브 운용 전 체크리스트 (#78/#79/#80/#81/#85/#86 매핑)
5. **Onboarding + AGENTS.md 링크** — `docs/onboarding/getting-started.md` 에 "활성화 가능한 기능 색인" 섹션 + `AGENTS.md` 핵심 문서 링크 목록.

### Whitepaper (#86) 연결
- `gh issue comment 86` — feature-catalog 가 기능 목록 단일 진실원임을 명시. Whitepaper §11 로드맵 작성 시 흡수/참조.

### .ai.md 갱신
- `src/risk/.ai.md` — position_sizer.py 추가, cvar_levels/fear_greed_proxy/consensus_kelly/user_risk_vol_target 반영.
- `src/portfolio/.ai.md` — compute_fear_greed_proxy 헬퍼 + Snapshot 주입 경로.
- `src/universe/.ai.md` — 신규 디렉토리 신설.
- `AGENTS.md` — 핵심 문서 링크에 feature-catalog 추가.

### 검증
- **pytest: 575 passed / 6 skipped / 6 deselected** (+68 신규 테스트, 기존 회귀 0).
- **invariants strict: 91 노트 통과** (+5 신규: 87 work folder + feature-catalog + universe spec 등).
- MomoBtcV2 기본 경로 동작 불변 (옵션 전부 기본 False/None).

### 팀 오케스트레이션
- `/team 3:executor` — 3 워커 병렬 실행:
  - worker-1 (Track A sizing): Task #1, #2
  - worker-2 (Track B risk/dsl/orchestrator): Task #3, #4
  - worker-3 (Track C universe + position_sizer + pyproject): Task #5~#8
  - team-lead (final): Task #9 (회귀 + invariants + .ai.md + 발견 가능성 5수준)
