# 01_plan — #70 포트폴리오 리스크 관리 (CVaR + 상관 매트릭스)

> ralplan consensus (deliberate mode) 완료 · Planner → Architect(NEEDS_MAJOR_REVISION) → Critic(APPROVE, 13 amendments baked)
> 작성: 2026-04-24

## AC 체크리스트

- [x] `src/risk/portfolio.py` — 상관 매트릭스, CVaR, 포트폴리오 VaR (순수함수 4개 + pandas wrapper)
- [x] Ledoit-Wolf shrinkage 공분산 추정 (`sklearn.covariance.LedoitWolf`)
- [x] 전략 간 상관 기반 경고/halt (`per_portfolio_risk.max_corr_avg` → BLOCK, `min_enb_ratio` → HALT)
- [x] 2+ 전략 시뮬레이션에서 포트폴리오 CVaR 계산 (E2E 테스트 + 라이브 스모크 5코인 365일)
- [x] 상관 임계치 초과 시 경고 로직 (`Decision.rule_id="per_portfolio_risk.max_corr_avg"` + 감사 로그 포맷)
- [x] 단위 테스트 + risk DSL 연동 (37 tests: 33 risk + 4 orchestrator stub)

## 스코프 확장 (배선 공백 해소)

- [x] `src/portfolio/orchestrator.py` 스텁 — `from risk import` 첫 실제 사용처
- [x] `src/backtest/strategies/.ai.md` "리스크 연동 (필수)" 섹션
- [x] `CLAUDE.md` "새 전략 추가 시 필수 (#70 이후)" 블록
- [x] 후속 이슈 3개 백로그 생성 (#78 #79 #80) — 의존성 번호 포함

## 참고 research

- [[19-portfolio-risk]] — Ledoit-Wolf·ENB·CVaR·팩터 노출 이론 (α=0.975 → §4.1 FRTB, ENB ≥ 0.3·N → §7)
- [[risk-rule-dsl]] — YAML DSL 연동 대상 (§8 v2 슬롯 이번 PR 로 delivered)
- [[20-position-sizing]] — sizing 결과가 portfolio risk 에 입력 (#71 에서 LW Σ consumer)
- [[12-validation-protocol]] — 롤백 트리거 연계 (§4 "일간 손실 > VaR(99%) × 1.5")

---

## 구현 계획

### A. RALPLAN-DR 요약

#### A.1 Principles (5)
1. **평가자는 순수함수** — LLM 은 결정 루프 밖 (CLAUDE.md 불변식 #6). side-effect 금지.
2. **YAML 스키마 파괴 금지** — `ConfigDict(extra="forbid")` 하에서 `policies/{conservative,neutral,aggressive}.yaml` 3개가 **수정 없이** 파싱돼야 함. 신규 필드는 전부 `Optional[...] = None`.
3. **모든 신규 rule 은 테스트로 게이트** — (a) 트리거되는 snapshot, (b) 트리거되지 않는 snapshot 양쪽 커버. `rule_id` 형식 `"<block>.<field>"` 유지.
4. **비싼 계산은 호출자 책임** — `shrinkage_covariance`/`historical_cvar`/`compute_portfolio_risk_from_df` 는 `Snapshot` 바깥에서 실행. `Snapshot.portfolio_risk` 로 결과 스칼라만 주입.
5. **기본값은 `19-portfolio-risk.md` §번호를 docstring 에 인용** — α=0.975 → §4.1 (Basel III FRTB), `min_enb_ratio=0.3` → §7 ("ENB ≥ 0.3·N").

#### A.2 Decision Drivers (top 3)
| # | Driver | 의미 |
|---|--------|------|
| D1 | Zero-regression on existing test suite | `test_risk_dsl.py` 전체 + `test_load_all_policy_files` 그대로 green |
| D2 | Future `PositionSizer` (#71) 정합 | `20-position-sizing.md` §7.1 은 `cov: np.ndarray` 를 consumer. **numpy 중심 public API**. |
| D3 | per-order `evaluate()` 실측 p99 불악화 | Architect 지적대로 `evaluate()` 는 이미 O(sectors). Amendment #7 벤치로 검증/폐기 |

#### A.3 Viable Options & 채택
- **Option A (Planner 초안)** — `PerPortfolio` 에 필드 추가 + `evaluate()` 안 gated block
- **Option B** — 신규 `per_portfolio_risk` 블록 + 별도 `evaluate_portfolio()` + 신규 `PortfolioSnapshot`
- **Option C (Planner 채택)** — A+B 하이브리드
- **Option A-lite (Architect 역제안 → Critic 승인 → 최종 채택)** — `Snapshot.portfolio_risk: Optional=None` + `per_portfolio_risk` 를 Policy **sibling** 블록으로 추가 + 단일 `evaluate()` gated block

**채택: Option A-lite.** 이유:
- Option C 의 D3 "O(1)" 전제가 `dsl.py:181-185` sector 루프로 **이미 반증**됨 (3개 스칼라 비교는 floor noise)
- C 는 (a) Snapshot 타입 2개, (b) evaluator 2개, (c) "first-violation-wins" 가 evaluator 경계에서 깨짐, (d) 호출자는 두 객체 스레딩 → 구조적 부채만 추가
- A-lite 는 `Snapshot` 에 한 줄 추가 (Optional=None) 로 동일한 zero-regression 달성하면서 단일 precedence chain 유지

#### A.4 Pre-mortem (3 scenarios — deliberate mode 필수)

**S1 — 스키마 파괴로 프로덕션 YAML 로드 실패**
- 시나리오: pydantic `extra="forbid"` + 필수 필드 추가 → 기존 YAML `ValidationError` → `test_load_all_policy_files` 실패 → CI 전체 red
- 완화: 모든 신규 필드 `Optional[...] = None`; `on_*_breach` 에 기본 `Action` 부여; Step 11 에서 `pytest tests/test_risk_dsl.py::test_load_all_policy_files -x` 를 **제일 먼저** 단독 실행
- 검증 테스트: `test_load_all_policy_files` (기존), `test_per_portfolio_risk_absent_allows` (신규)

**S2 — LW 공분산이 non-PSD / NaN 로 조용히 고장**
- 시나리오: returns 에 NaN 또는 T<N+ddof → `LedoitWolf.fit()` 경고 없이 통과 → Σ non-PSD → `ENB = (w'Σw)²/(w'Σ²w)` NaN → `min_enb_ratio` 체크가 비교 False → 리스크 룰이 침묵
- 완화: 입력 단계 `dropna(axis=0, how="any")`; `T < max(30, 2N)` 시 `ShortSampleWarning`; 반환 전 `eigvalsh >= -1e-10` PSD assert; ENB NaN → `min_enb_ratio` 체크에서 **위반으로 간주**
- 검증 테스트: `test_short_sample_warning`, `test_nan_column_dropped`, `test_psd_guard_asserts`, `test_enb_nan_treated_as_breach`

**S3 — HALT 트리거됐으나 observability 사일런트**
- 시나리오: `rule_id` 라벨 포맷이 기존 `qta_risk_breach_total` 라벨 공간과 어긋남 → 메트릭 drop 또는 새 라벨 생성 → 운영자는 주문 차단 이유를 모름
- 완화: 신규 `rule_id` 는 `per_portfolio_risk.<field>` 포맷 고정 (Amendment #2); `docs/specs/risk-rule-dsl.md` §7 에 3개 신규 `rule_id` 명시 (Amendment #10 위키링크 포함); per-rule 기본 action 은 **의미론적으로** 선택 (Amendment #3)
- 검증 테스트: `test_cvar_breach_emits_metric_once`, `test_enb_breach_halts_not_reduces`, `test_breach_message_matches_spec_section_7_format` (관측가능성 트리오)

#### A.5 Expanded test plan
상세는 §C 참조. 요약: **Unit 13 + Integration 6 + E2E 3 + Observability 3 + Regression (기존 전체) + Edge 6**.

---

### B. ADR

**Decision**: **Option A-lite** — `Snapshot.portfolio_risk: Optional[PortfolioRiskReport] = None` 를 `dsl.py:88` 에 추가, `PerPortfolioRisk` 를 `Policy` sibling 블록으로 신설, `evaluate()` 안 per_portfolio 블록과 per_position 블록 사이 (현 파일 line 168-171 구간) 에 gated block 삽입. `Snapshot`·`Policy`·`evaluate()` 시그니처는 무변경 (Optional 한 줄씩만 추가).

**Drivers addressed**
- D1: 모든 신규 필드 Optional → 기존 테스트·YAML·호출자 전부 무수정 통과
- D2: `shrinkage_covariance(np.ndarray) -> np.ndarray` 가 `PositionSizer` §7.1 시그니처와 직접 호환; 편의용 `compute_portfolio_risk_from_df()` 는 wrapper 로 분리
- D3: Amendment #7 `timeit` 벤치로 p99 < 100µs 실측; 통과 시 D3 은 근거 있는 검증 기준으로 확정, 실패 시 이슈 재오픈 트리거

**Alternatives considered**
- Option C (Planner 초안): 두 evaluator 간 precedence 파편화·Snapshot 타입 2개의 구조적 부채. ❌ rejected.
- Option A (순수): `PerPortfolio` 에 필드 섞기 → order-level 제약과 portfolio-state 제약이 한 블록에 공존. ❌ schema semantics 혼탁.
- Option B (분리): 블록 분리는 좋으나 evaluator 분리는 불필요. 부분 채택.

**Why chosen**
A-lite 는 Architect 의 실증적 반박 (evaluate() 는 이미 non-O(1)) + Critic 의 precedence 단일성 요구를 동시에 만족. 단일 evaluator → 단일 first-violation-wins chain → 호출자는 기존 `evaluate(policy, snap)` 시그니처만 사용.

**Consequences**
- Good: 기존 테스트 전체 무수정, 구조 복잡도 증가 zero, Sizer 호환 명확
- Bad: `Snapshot` 에 Optional 필드 1개 추가; gated block 이 `evaluate()` 길이를 10~15 줄 늘림 (evaluator 가 `per_portfolio_risk` 블록 추가로 이제 7단계 precedence — 허용 범위)

**Follow-ups (이번 PR OUT of scope)**
- EVT / 파라메트릭 CVaR 대체 (`19-portfolio-risk.md §4.2` 후반)
- Factor exposure 제약 (§5 Fama-French)
- CVaR 최소화 LP 최적화기 (Rockafellar-Uryasev)
- Rolling LW Σ 스트림 업데이트
- `PositionSizer` 실제 구현 → **#71**
- Prometheus counter 실제 wiring (observability layer PR)
- `PortfolioRiskReport.returns_frame` 의 DataFrame index 계약 → #71 에서 확정

---

### C. 전체 테스트 계획 (deliberate mode 요구 버킷 전부 채움)

#### C.1 Unit (13)

| 테스트 | 대상 | 검증 |
|--------|------|------|
| `test_shrinkage_covariance_basic` | `shrinkage_covariance` | 2-전략 30일 난수 → Σ shape (2,2), 대칭, PSD, 대각 > 0 |
| `test_shrinkage_covariance_dropna` | " | NaN 행 있어도 결과 동일 |
| `test_shrinkage_covariance_short_sample_warns` | " | T=10, N=5 → `ShortSampleWarning`, 계산은 진행 |
| `test_shrinkage_covariance_psd_guard` | " | rank-deficient 입력도 LW 수축으로 PSD 유지 |
| `test_shrinkage_covariance_numpy_only` | " | 입력·출력 전부 `np.ndarray`, pandas import 없이 동작 |
| `test_historical_cvar_monotone` | `historical_cvar` | α=0.90 CVaR ≥ α=0.975 CVaR |
| `test_historical_cvar_all_negative` | " | 전부 음수 → 양수 손실 크기 반환 |
| `test_historical_cvar_single_obs` | " | N=1 → 해당 값 절댓값 |
| `test_effective_number_of_bets_uncorrelated` | `effective_number_of_bets` | 등가중 독립 N → ENB ≈ N |
| `test_effective_number_of_bets_perfectly_correlated` | " | ρ=1 → ENB ≈ 1 |
| `test_effective_number_of_bets_degenerate_sigma` | " | near-singular Σ → finite 또는 NaN (명세 일치) |
| `test_average_pairwise_correlation_clamped` | `average_pairwise_correlation` | 결과 ∈ [-1, 1], N=1 → 0.0 |
| `test_portfolio_risk_report_frozen` | `PortfolioRiskReport` | extra=forbid, frozen=True, `ts` 필수 |

#### C.2 Integration (6)

| 테스트 | 시나리오 |
|--------|---------|
| `test_evaluate_cvar_breach_reduces` | 2-전략 DF → report(cvar=0.12) → `max_cvar_pct=0.08` → `Decision(action=REDUCE, rule_id="per_portfolio_risk.max_cvar_pct")` |
| `test_evaluate_cvar_allow` | 동 DF, `max_cvar_pct=0.99` → `Decision(ALLOW)` |
| `test_evaluate_corr_breach_blocks` | ρ=0.95 강제 → `max_corr_avg=0.80` → `Decision(BLOCK, "per_portfolio_risk.max_corr_avg")` |
| `test_evaluate_enb_breach_halts` | 고상관 4전략 → ENB=1.2 → `min_enb_ratio=0.5` → `Decision(HALT, "per_portfolio_risk.min_enb_ratio")` |
| `test_evaluate_no_report_allows` | `snap.portfolio_risk = None` → `ALLOW` (회귀 방지, Principle #2) |
| `test_precedence_per_portfolio_before_risk` | per_portfolio (exposure) 와 per_portfolio_risk (cvar) 동시 위반 → exposure rule_id 먼저 (Amendment #6 순서 고정) |

#### C.3 E2E (3 — Critic Amendment #11)

| 테스트 | 경로 |
|--------|------|
| `test_e2e_cvar_path` | `compute_portfolio_risk_from_df(df)` → `Snapshot(portfolio_risk=report)` → `evaluate()` → Decision(REDUCE) |
| `test_e2e_corr_path` | 동, corr 트리거 → Decision(BLOCK) |
| `test_e2e_enb_path` | 동, ENB 트리거 → Decision(HALT) |

각 테스트는 rule_id 문자열 동등성 + action Enum 동등성 동시 assert.

#### C.4 Observability (3 — Critic Amendment #12)

| 테스트 | 검증 |
|--------|------|
| `test_breach_emits_rule_id_once` | caplog 로 cvar breach 시 `qta_risk_breach_total{rule_id="per_portfolio_risk.max_cvar_pct"}` 상응 로그 레코드 1회 |
| `test_short_sample_warning_category` | `pytest.warns(ShortSampleWarning)` 로 T<60 에서 정확히 해당 카테고리 |
| `test_decision_message_format_snapshot` | `Decision.message` 가 `risk-rule-dsl.md §7` 감사로그 포맷 ("cvar X > Y") 에 일치 |

#### C.5 Edge (6)

- `test_n1_strategy_avg_corr_zero` — 단일 전략 → ρ̄=0.0
- `test_t_less_than_n_warning` — T<N → ShortSampleWarning
- `test_nan_column_treated` — 전부 NaN 컬럼 → RuntimeError (지속 가능한 계산 불가)
- `test_zero_equity_does_not_crash` — equity=0 스냅샷 → evaluator 통과, portfolio_risk 체크는 여전히 동작
- `test_rho_clamp_out_of_range` — float 오차로 ρ=1.0000001 → clamp 1.0
- `test_near_singular_sigma_enb_nan_is_breach` — degenerate Σ → ENB NaN → `min_enb_ratio` 체크 위반

#### C.6 Regression
- `tests/test_risk_dsl.py` **전부** 기존대로 green.
- 특히 `test_load_all_policy_files` 를 Step 11 에서 단독 먼저 실행 (S1 mitigation).

---

### D. 구현 단계 (15 → 18, step 6 분할 + 벤치 + 테스트 강화)

#### Step 1 — 의존성 선언
**파일**: `pyproject.toml`
- `[project].dependencies` 에 `"scikit-learn>=1.4"` 추가
- 검증: `python -c "from sklearn.covariance import LedoitWolf; print('ok')"`

#### Step 2 — `src/risk/portfolio.py` 신규 (numpy 중심 core + pandas wrapper, Amendment #4)

```python
# src/risk/portfolio.py
"""Portfolio-level risk metrics (CVaR / ENB / LW Σ).

Theory:
- Ledoit-Wolf shrinkage: docs/background/19-portfolio-risk.md §2.2
- Meucci ENB: §3.1
- Historical CVaR: §4.1 (Basel III FRTB default α=0.975)
- Meucci ENB ≥ 0.3·N 권고: §7
"""
from __future__ import annotations

import math
import warnings
from datetime import datetime
from typing import Optional

import numpy as np
from pydantic import BaseModel, ConfigDict, Field
from sklearn.covariance import LedoitWolf


class ShortSampleWarning(UserWarning):
    """T 가 max(30, 2N) 미만일 때 발생."""


class PortfolioRiskReport(BaseModel):
    """주기 평가기에서 계산돼 Snapshot.portfolio_risk 로 주입되는 불변 스냅샷."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    cvar_pct: float = Field(..., ge=0.0, description="Historical CVaR at alpha (positive loss fraction)")
    var_pct: float = Field(..., ge=0.0)
    corr_avg: float = Field(..., ge=-1.0, le=1.0)
    enb: float = Field(..., ge=0.0)
    enb_ratio: float = Field(..., ge=0.0, le=1.0, description="enb / N; NaN allowed on degenerate Σ")
    n_strategies: int = Field(..., ge=1)
    n_observations: int = Field(..., ge=1)
    alpha: float = Field(0.975, gt=0.0, lt=1.0, description="Cited: 19-portfolio-risk.md §4.1 (FRTB)")
    ts: datetime                                                                     # Amendment #9: mandatory audit timestamp


# ---- pure numpy core (Amendment #4) --------------------------------------

def shrinkage_covariance(returns: np.ndarray) -> np.ndarray:
    """Ledoit-Wolf shrinkage. returns: T×N, NaN-free, rows=time, cols=assets."""
    if returns.ndim != 2:
        raise ValueError("returns must be 2D (T, N)")
    T, N = returns.shape
    if T < max(30, 2 * N):
        warnings.warn(
            f"Short sample: T={T}, N={N}; LW may be noisy (§2.2).",
            ShortSampleWarning, stacklevel=2,
        )
    lw = LedoitWolf().fit(returns)
    cov = lw.covariance_
    cov = 0.5 * (cov + cov.T)                                  # symmetrize
    eig_min = float(np.linalg.eigvalsh(cov).min())
    if eig_min < -1e-10:
        raise RuntimeError(f"LW covariance not PSD: eig_min={eig_min}")
    return cov


def historical_cvar(returns: np.ndarray, alpha: float = 0.975) -> float:
    """Left-tail CVaR. Returns POSITIVE loss fraction. α cited: §4.1 FRTB."""
    if returns.ndim != 1 or returns.size == 0:
        raise ValueError("returns must be non-empty 1D")
    q = float(np.quantile(returns, 1.0 - alpha))
    tail = returns[returns <= q]
    if tail.size == 0:
        return -q                                              # single-obs degenerate
    return float(-tail.mean())


def effective_number_of_bets(weights: np.ndarray, cov: np.ndarray) -> float:
    """Meucci ENB = (w'Σw)² / (w'Σ²w). degenerate → NaN."""
    num = float(weights @ cov @ weights) ** 2
    cov2 = cov @ cov
    den = float(weights @ cov2 @ weights)
    if den <= 0.0:
        return math.nan
    return num / den


def average_pairwise_correlation(cov: np.ndarray) -> float:
    """Σ → upper-triangular ρ 평균. [-1,1] clamp. N=1 → 0.0."""
    std = np.sqrt(np.diag(cov))
    if std.size < 2:
        return 0.0
    denom = np.outer(std, std)
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = cov / denom
    corr = np.clip(corr, -1.0, 1.0)
    iu = np.triu_indices_from(corr, k=1)
    return float(np.nanmean(corr[iu]))


# ---- thin pandas wrapper (ergonomics only) --------------------------------

def compute_portfolio_risk_from_df(
    df,                                  # type: "pd.DataFrame"
    weights: Optional[np.ndarray] = None,
    alpha: float = 0.975,
    ts: Optional[datetime] = None,
) -> PortfolioRiskReport:
    """Wrapper: pd.DataFrame(T×N, rows=time, cols=strategy_id) → report.

    Contract: rows dropped where any NaN; T≥2 required.
    numpy core functions used inside → D2 (Sizer 정합) 유지.
    """
    import pandas as pd                                        # lazy import
    if not isinstance(df, pd.DataFrame):
        raise TypeError("df must be pd.DataFrame")
    clean = df.dropna(axis=0, how="any").to_numpy(dtype=float)
    T, N = clean.shape
    if T < 2:
        raise ValueError(f"Need T≥2 observations after dropna; got {T}")
    cov = shrinkage_covariance(clean)
    if weights is None:
        weights = np.ones(N) / N                               # equal weight default
    portfolio_returns = clean @ weights
    var_pct = float(-np.quantile(portfolio_returns, 1.0 - alpha))
    cvar_pct = historical_cvar(portfolio_returns, alpha)
    enb = effective_number_of_bets(weights, cov)
    enb_ratio = math.nan if math.isnan(enb) else min(enb / N, 1.0)
    corr_avg = average_pairwise_correlation(cov)
    return PortfolioRiskReport(
        cvar_pct=cvar_pct, var_pct=var_pct, corr_avg=corr_avg,
        enb=0.0 if math.isnan(enb) else enb,
        enb_ratio=0.0 if math.isnan(enb_ratio) else enb_ratio,
        n_strategies=N, n_observations=T, alpha=alpha,
        ts=ts or datetime.utcnow(),
    )
```

#### Step 3 — `src/risk/__init__.py` export (append only)
```python
from .portfolio import (
    PortfolioRiskReport, ShortSampleWarning,
    shrinkage_covariance, historical_cvar,
    effective_number_of_bets, average_pairwise_correlation,
    compute_portfolio_risk_from_df,
)
```
기존 export 순서·이름 보존.

#### Step 4 — `tests/test_portfolio_risk.py` — Unit tests **먼저** (TDD red)
- §C.1 의 13개 + §C.5 의 6개 (Edge) 작성.
- 헬퍼: `_returns(T=60, N=3, rho=0.0, seed=42) -> np.ndarray` / 동일 시그니처 pandas 버전.
- import: `from risk.portfolio import ...` (`pythonpath=["src", "."]`).

#### Step 5 — Step 2 구현 → Unit/Edge 전부 green
Core + wrapper 동시 구현. pytest 로 green 확인 후 진행.

#### Step 6a — `src/risk/dsl.py` 스키마 확장 (Amendment #2, 분할)
**`PerPortfolioRisk` 를 sibling 블록으로 신설** (Policy 에 `per_portfolio_risk: Optional[PerPortfolioRisk] = None`):

```python
class PerPortfolioRisk(_Strict):
    # 임계값 (전부 Optional)
    max_cvar_pct: Optional[PositiveFloat] = Field(
        default=None, lt=1.0,
        description="Max portfolio CVaR (positive loss fraction). Cited: 19-portfolio-risk.md §4.1 FRTB.",
    )
    max_corr_avg: Optional[float] = Field(
        default=None, ge=-1.0, le=1.0,
        description="Max allowed average pairwise correlation.",
    )
    min_enb_ratio: Optional[PositiveFloat] = Field(
        default=None, le=1.0,
        description="Min ENB/N ratio. Default rationale: 19-portfolio-risk.md §7 (ENB ≥ 0.3·N).",
    )
    alpha: Optional[float] = Field(
        default=0.975, gt=0.0, lt=1.0,
        description="CVaR/VaR α. Default 0.975 cited: §4.1 FRTB.",
    )
    # 의미론적으로 맞는 기본 action (Amendment #3)
    on_cvar_breach: Action = Action.REDUCE    # CVaR ∝ order size → REDUCE 유효
    on_corr_breach: Action = Action.BLOCK     # correlation 은 state → 새 주문 차단
    on_enb_breach: Action = Action.HALT       # 다변화 구조 문제 → 사람이 rebalance


class Policy(_Strict):
    ...
    per_portfolio: Optional[PerPortfolio] = None
    per_portfolio_risk: Optional[PerPortfolioRisk] = None     # ← 신규 sibling
    per_position: Optional[PerPosition] = None
    ...
```

`Snapshot` 에 optional 필드 한 줄 추가 (Amendment #1):
```python
class Snapshot(_Strict):
    ...
    intraday_dd_pct: float = 0.0
    running_dd_pct: float = 0.0
    portfolio_risk: Optional["PortfolioRiskReport"] = None    # ← 신규 Optional
```
forward-ref 회피: `from .portfolio import PortfolioRiskReport` 를 `dsl.py` 상단에 추가 (순환 없음 — portfolio.py 는 dsl 을 import 하지 않음).

#### Step 6b — `evaluate()` gated block 삽입 (Amendment #1, #6 precedence)
현 `dsl.py` line 168 (per_portfolio 끝) 과 line 171 (per_position 시작) 사이에 삽입:
```python
    # per_portfolio_risk (주기 평가 결과 주입 시에만 동작)
    ppr = policy.per_portfolio_risk
    rep = snap.portfolio_risk
    if ppr is not None and rep is not None:
        if ppr.max_cvar_pct is not None and rep.cvar_pct > ppr.max_cvar_pct:
            return Decision(action=ppr.on_cvar_breach,
                            rule_id="per_portfolio_risk.max_cvar_pct",
                            message=f"cvar {rep.cvar_pct:.4f} > {ppr.max_cvar_pct:.4f}")
        if ppr.max_corr_avg is not None and rep.corr_avg > ppr.max_corr_avg:
            return Decision(action=ppr.on_corr_breach,
                            rule_id="per_portfolio_risk.max_corr_avg",
                            message=f"corr_avg {rep.corr_avg:.3f} > {ppr.max_corr_avg:.3f}")
        if ppr.min_enb_ratio is not None and (
            math.isnan(rep.enb_ratio) or rep.enb_ratio < ppr.min_enb_ratio
        ):
            return Decision(action=ppr.on_enb_breach,
                            rule_id="per_portfolio_risk.min_enb_ratio",
                            message=f"enb_ratio {rep.enb_ratio:.3f} < {ppr.min_enb_ratio:.3f}")
```
(`math` 는 `dsl.py` 상단 import 추가.)

**Precedence 확정**: `per_trade → per_day → per_portfolio → per_portfolio_risk → per_position → sector_limits → drawdown`. First-violation-wins.

#### Step 6c — Precedence 통합 테스트
`test_precedence_per_portfolio_before_risk` 작성: per_portfolio (leverage 초과) + per_portfolio_risk (cvar 초과) 동시 위반 → `rule_id == "per_portfolio.max_leverage"` 먼저 반환 검증.

#### Step 7 — Integration + E2E + Observability 테스트 추가
- §C.2 Integration 6개
- §C.3 E2E 3개 (Amendment #11)
- §C.4 Observability 3개 (Amendment #12)

`_policy(**overrides)` / `_snap_with_report(report=...)` 헬퍼 신설 (기존 `test_risk_dsl.py::_policy/_snap` 컨벤션 모사).

#### Step 8 — 벤치 (Amendment #7)
`tests/bench_risk.py` 또는 `tests/test_portfolio_risk.py::test_evaluate_latency_p99`:
```python
def test_evaluate_latency_p99_under_100us():
    import timeit
    policy, snap = _policy(...), _snap_with_report(...)
    ts = timeit.repeat(lambda: evaluate(policy, snap), repeat=100, number=100)
    p99_us = sorted([t/100 for t in ts])[int(0.99*100)] * 1e6
    assert p99_us < 100, f"evaluate() p99={p99_us:.1f}µs > 100µs"
```
통과 시 D3 정량 확정, 실패 시 이슈 재오픈.

#### Step 9 — 회귀 검증 분할 실행 (S1 mitigation)
```bash
pytest tests/test_risk_dsl.py::test_load_all_policy_files -x       # 스키마 호환 먼저
pytest tests/test_risk_dsl.py -x                                    # 전체 기존 스위트
pytest tests/test_portfolio_risk.py -x                              # 신규
pytest tests/test_risk_dsl.py tests/test_portfolio_risk.py          # 결합
```

#### Step 10 — 문서 업데이트 (`docs/specs/risk-rule-dsl.md`, Amendment #6, #10)
- §3 YAML 예시에 `per_portfolio_risk` 블록 추가 (max_cvar_pct / max_corr_avg / min_enb_ratio / alpha / on_*_breach)
- §2.2 에 **Precedence 조항** 추가: "Order: per_trade → per_day → per_portfolio → per_portfolio_risk → per_position → sector_limits → drawdown. First violation wins."
- §7 관측가능성에 3개 신규 `rule_id` 명시 + `[[19-portfolio-risk]]` 백링크 추가 (CI 불변식 #3 충족)
- §8 로드맵 v2 → "delivered (#70)" 이동
- §9 관련 노트에 `[[20-position-sizing]]` 은 이미 존재 (재확인)

#### Step 11 — `src/risk/.ai.md` 갱신
- Public API 목록 갱신: `portfolio.py` 의 7개 심볼 + `PerPortfolioRisk` 추가
- 운영 규칙에 "포트폴리오 리스크 report 는 주기 평가기가 계산해 `Snapshot.portfolio_risk` 로 주입" 명시

#### Step 12 — `docs/background/19-portfolio-risk.md` §6 로드맵 체크박스
- `[x] v2.1 Historical CVaR (α=0.975)`
- `[x] v2.2 LW shrinkage Σ`
- `[x] v2.3 Meucci ENB + 평균 ρ`
- EVT / 팩터 / CVaR 최적화는 `[ ]` 유지

#### Step 13 — `docs/work/active/000070-portfolio-risk/02_implementation.md` 작성
- 최종 ADR 요약, 벤치 실측치, 신규 테스트 카운트, 후속 이슈 (#71 sizer) 링크

#### Step 14 — 전체 스위트
```bash
pytest
```
기존 + 신규 + 벤치 전부 green.

#### Step 15 — 불변식 검증
```bash
python scripts/check_invariants.py --strict
```
프론트매터·위키링크·ttl 파싱 전부 green.

#### Step 16 — 스모크 테스트 (Amendment #13 #7)
```bash
python -c "from risk.portfolio import compute_portfolio_risk_from_df; import pandas as pd, numpy as np; df=pd.DataFrame(np.random.RandomState(0).randn(1000,5)); r=compute_portfolio_risk_from_df(df); assert r.cvar_pct > 0 and 0 < r.enb_ratio <= 1; print('ok')"
```

#### Step 17 — `.last-task-summary` 기록
`~/.claude/.last-task-summary` 에 "feat/000070 포트폴리오 리스크 구현 완료 — 커밋 승인 대기" 기록 (CLAUDE.md 텔레그램 룰).

#### Step 18 — 정지 + 사용자 승인 대기
`git commit` / `git push` 는 수동. CLAUDE.md 행동 규칙에 따라 "커밋할까?" 로 물어본 뒤 실행.

---

### E. AC 매핑

| # | Issue AC | 구현 단계 | 검증 테스트 |
|---|----------|-----------|-------------|
| AC1 | `src/risk/portfolio.py` — 상관/CVaR/VaR | Step 2, 5 | Unit C.1 전체 |
| AC2 | LW shrinkage 공분산 | Step 2, 5 | `test_shrinkage_covariance_*` 4개 |
| AC3 | 전략 간 상관 기반 경고/halt | Step 6a, 6b, 7 | `test_evaluate_corr_breach_blocks`, `test_evaluate_enb_breach_halts`, E2E `test_e2e_corr_path` |
| AC4 | 2+ 전략 시뮬 CVaR 계산 | Step 2, 7 | `test_e2e_cvar_path`, Integration `test_evaluate_cvar_breach_reduces` |
| AC5 | 상관 임계치 초과 시 경고 | Step 6a (on_corr_breach=BLOCK + WARN log), 7 | Observability `test_breach_emits_rule_id_once` |
| AC6 | 단위 테스트 + risk DSL 연동 | Step 4, 6, 7 | §C 전체 |

---

### F. 리스크 매트릭스

| # | 범주 | 리스크 | 완화 | 검증 |
|---|------|-------|------|------|
| R1 | Schema | `extra=forbid` + 신규 필드로 기존 YAML 파괴 | 모든 신규 필드 Optional=None; `on_*_breach` 기본값 | Step 9 의 `test_load_all_policy_files` 단독 |
| R2 | Numerical | LW non-PSD / ENB NaN silent | dropna + ShortSampleWarning + PSD assert + ENB NaN → breach | Unit `test_*_psd_guard`, `test_enb_*_degenerate` |
| R3 | Perf | per-order `evaluate()` 지연 악화 | Optional gated block → `portfolio_risk is None` 이면 skip; 벤치 p99<100µs gate | Step 8 벤치 테스트 |
| R4 | Ops | silent kill-switch (rule_id 라벨 오염) | 고정 포맷 `per_portfolio_risk.<field>` + spec §7 명시 + 의미론적 기본 action | Observability 3개 |
| R5 | Dep | scikit-learn 미선언 | Step 1 `pyproject.toml` + CI 에서 import 확인 | 스모크 Step 16 |
| R6 | Governance | LLM 이 CVaR 해석에 개입 (불변식 #6) | portfolio.py 순수함수, docstring 에 "Not LLM-invocable" 명시, MCP tool 목록에서 제외 | 코드 리뷰 |
| R7 | Precedence | evaluator 순서가 구현 정의 | spec §2.2 에 명시 + `test_precedence_*` 테스트 | Step 6c, 10 |

---

### G. 파일 변경 목록

**Created**
- `src/risk/portfolio.py`
- `tests/test_portfolio_risk.py`
- `docs/work/active/000070-portfolio-risk/02_implementation.md` (Step 13)

**Modified**
- `pyproject.toml` — `scikit-learn>=1.4` 추가
- `src/risk/__init__.py` — 7개 신규 심볼 export (append)
- `src/risk/dsl.py` — `Snapshot.portfolio_risk` 1줄 + `PerPortfolioRisk` 클래스 + `Policy.per_portfolio_risk` 1줄 + `evaluate()` gated block
- `src/risk/.ai.md` — Public API 갱신
- `docs/specs/risk-rule-dsl.md` — §2.2 precedence, §3 YAML, §7 observability + 위키링크, §8 로드맵
- `docs/background/19-portfolio-risk.md` — §6 로드맵 체크박스

**NOT modified (의도적)**
- `policies/{conservative,neutral,aggressive}.yaml` — 신규 필드 Optional 이므로 기존 파일 무변경 파싱 가능. 실사용 예시는 신규 `policies/portfolio-risk-example.yaml` 로 별도 추가 여부 후속 결정.
- `tests/test_risk_dsl.py` — 전체 무변경 통과 (회귀 게이트).
- `src/risk/dsl.py::evaluate()` 기존 블록 (per_trade/per_day/per_portfolio/per_position/sector_limits/drawdown) — 추가만, 수정 없음.

---

### H. 검증 체크리스트 (blocker vs evidence)

| # | 명령 | 유형 |
|---|------|------|
| 1 | `pytest tests/test_portfolio_risk.py -v` (≥13 unit + ≥6 edge + ≥6 integ + ≥3 e2e + ≥3 obs = **≥31 pass**, 0 skip) | **Blocker** |
| 2 | `pytest tests/test_risk_dsl.py -v` (기존 전부 green) | **Blocker** — D1 |
| 3 | `pytest tests/test_risk_dsl.py::test_load_all_policy_files -v` | **Blocker** — P2 / S1 |
| 4 | `pytest tests/test_portfolio_risk.py -k precedence` | **Blocker** — Amendment #6 |
| 5 | `pytest tests/test_portfolio_risk.py::test_evaluate_latency_p99_under_100us` | Evidence — D3 |
| 6 | `python scripts/check_invariants.py --strict` | **Blocker** — CI 불변식 #3 (위키링크) |
| 7 | Step 16 스모크 one-liner | Evidence |
| 8 | `grep -n "19-portfolio-risk" docs/specs/risk-rule-dsl.md` → ≥1 hit in §7 | **Blocker** — Amendment #10 |
| 9 | `pytest tests/test_portfolio_risk.py -k breach_emits` | Evidence — Amendment #12 observability |

1–4, 6, 8 은 green 필수; 5, 7, 9 는 evidence 수집.

---

### I. 미해결 질문 (PR 전 확정)

- [x] **`ts` 필수 vs optional?** → **필수** (Amendment #9; 감사로그 §7)
- [x] **`alpha` YAML 노출?** → **예**, `PerPortfolioRisk.alpha: Optional[float] = 0.975` (Amendment #9)
- [x] **`on_*_breach` 기본값?** → **의미론적**: cvar=REDUCE, corr=BLOCK, enb=HALT (Amendment #3)
- [ ] **`shrinkage_covariance` 반환 `pd.DataFrame` 의 index/columns 계약** → **#71 PositionSizer PR 로 이월** (본 PR 에서는 numpy core + wrapper, 계약 미확정 — §B Follow-ups)
- [ ] **신규 예시 정책 파일 `policies/portfolio-risk-example.yaml` 추가 여부** → 본 PR 에서는 생략, `docs/specs/risk-rule-dsl.md §3` 스니펫으로 충분. 운영 시점에 필요하면 후속 PR.

---

### J. 승인 게이트

**Critic 최종 verdict: APPROVE**
- Architect 10 amendment 전부 반영
- Critic 3 test-plan 강화안 (E2E ×3, Observability ×3, 검증 체크리스트) 반영
- Principles 5/5 유지, Drivers D1-D3 충족, Pre-mortem S1-S3 mitigation 테스트 연결 완료
- Deliberate mode 필수 요건 (Unit+Integration+E2E+Observability+Edge+Regression) 전부 충족

**다음 단계**: 사용자 승인 후 Step 1부터 순차 구현 (team/ralph 경유 가능).
