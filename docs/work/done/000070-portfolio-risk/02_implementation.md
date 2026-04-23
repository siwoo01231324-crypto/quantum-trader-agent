# 02_implementation — #70 포트폴리오 리스크 관리 (CVaR + 상관 매트릭스)

> 구현 완료. 커밋 승인 대기 중. 작성: 2026-04-24
>
> **스코프 확장 반영** (2026-04-24 후반):
> - 리뷰 중 발견된 "배선 공백" 3건 해소를 본 PR 에 포함
> - 후속 백로그 이슈 3건 생성 (#78 오케 · #79 카탈로그 · #80 라이브)

## 최종 설계 (Option A-lite)

Planner Option C (두 Snapshot 타입 + 두 evaluator) 를 Architect 지적(`evaluate()` 는 이미 `sector_limits` 루프로 non-O(1), 분리 이유 없음)에 따라 **A-lite 로 역전**:

- `Snapshot.portfolio_risk: Optional[PortfolioRiskReport] = None` 한 줄 추가
- `Policy.per_portfolio_risk: Optional[PerPortfolioRisk] = None` sibling 블록
- `evaluate()` per_portfolio 블록과 per_position 블록 **사이** 에 gated 블록 삽입 → 단일 evaluator, 단일 first-violation-wins chain
- precedence 공식화: `per_trade → per_day → per_portfolio → per_portfolio_risk → per_position → sector_limits → drawdown`

## 변경 파일

**Created — 핵심 리스크 모듈**
- `src/risk/portfolio.py` (207 lines) — numpy 순수함수 4개 + pandas wrapper 1개 + `PortfolioRiskReport`(pydantic frozen) + `ShortSampleWarning`
- `tests/test_portfolio_risk.py` (33 tests)
- `docs/work/active/000070-portfolio-risk/02_implementation.md` (this file)

**Created — 배선/스텁 (스코프 확장)**
- `src/portfolio/__init__.py` — 공개 API (`StrategyOrchestrator`)
- `src/portfolio/orchestrator.py` — #78 인터페이스 고정 + sync stub (`register_strategy_returns` / `refresh_portfolio_risk` / `evaluate_order`). `from risk import ...` 를 레포에서 **처음으로 실제 사용**
- `src/portfolio/.ai.md` — 디렉토리 목적·계약·후속 스코프(#78) 명시
- `tests/test_portfolio_orchestrator.py` (4 tests) — 단일 전략 ALLOW / 고상관 BLOCK / 독립 ALLOW / 인터페이스 계약

**Created — 라이브 스모크**
- `docs/work/active/000070-portfolio-risk/smoke_live.py` — Binance public REST 5 코인 1년 일봉 → 리스크 계산 → 3 정책 평가. 실측 수치를 02 리포트에 붙임

**Modified — 리스크 모듈**
- `pyproject.toml` — `scikit-learn>=1.4` 추가
- `src/risk/__init__.py` — 7 신규 심볼 export (append) + `PerPortfolioRisk`
- `src/risk/dsl.py` — `PerPortfolioRisk` 클래스, `Policy.per_portfolio_risk`, `Snapshot.portfolio_risk`, `evaluate()` gated block, `from .portfolio import PortfolioRiskReport`
- `src/risk/.ai.md` — 공개 API·평가 순서·기본 action 근거·운영 규칙 갱신
- `docs/specs/risk-rule-dsl.md` — §2.2 precedence, §3 YAML 예시, §7 운영 규칙 + §7.1 rule_id 라벨 공간, §8 로드맵 v2 delivered
- `docs/background/19-portfolio-risk.md` — §6 로드맵 체크박스, §7 운영 체크리스트

**Modified — 에이전트 가이드 (스코프 확장)**
- `CLAUDE.md` — "새 전략 추가 시 필수 (#70 이후)" 블록 신설. 향후 세션 시작 시 LLM 이 자동으로 읽어 규칙 내재화
- `src/backtest/strategies/.ai.md` — "리스크 연동 (필수)" 섹션 추가 (어떻게/왜/체크리스트/예외 절차)

**NOT modified (의도적)**
- `policies/{conservative,neutral,aggressive}.yaml` — 신규 필드 전부 Optional 이라 기존 파일 무수정 파싱. 실사용 예시는 docs/specs §3 스니펫으로 대체.
- `tests/test_risk_dsl.py` — 전체 무수정 통과 (회귀 zero 검증).
- `src/risk/dsl.py::Snapshot`/`Policy`/`evaluate()` 기존 블록 — 추가만, 수정 없음.

## 수학적 규칙 요약

1. **CVaR** — `historical_cvar(returns, α=0.975)`: 하위 2.5% 꼬리 평균 손실 (Basel III FRTB). `max_cvar_pct` 초과 → `REDUCE` (주문 크기에 비례 축소).
2. **평균 pairwise 상관** — `average_pairwise_correlation(Σ)`: `Σ` 상단 삼각 ρ 평균, `[-1,1]` clamp. `max_corr_avg` 초과 → `BLOCK` (상태는 신규 주문으로 못 고침).
3. **ENB (Meucci)** — PCA 엔트로피 형 `exp(-Σ p_k log p_k)`, `p_k = λ_k v_k²/Σ`. 독립 N개 자산 → ENB≈N, 완전상관 → ENB≈1. `min_enb_ratio`(=ENB/N) 미달 → `HALT` (구조 문제, 사람 개입).
4. **Ledoit-Wolf shrinkage Σ** — `shrinkage_covariance(returns)`: 샘플 Σ 와 대각 타겟의 데이터 기반 최적 볼록조합, PSD 보장, 짧은 샘플 시 `ShortSampleWarning`.

## 테스트 결과

```
pytest tests/test_risk_dsl.py tests/test_portfolio_risk.py
45 passed in 2.44s
```

`pytest` 전체:
```
417 passed, 1 skipped, 5 deselected, 63 warnings in 11.20s
```

`scripts/check_invariants.py --strict`:
```
[check_invariants] 통과 (81 노트 검증)
```

### 테스트 카운트 (33 신규)
- Unit 13 — LW/CVaR/ENB/ρ 각 함수 sanity
- Edge 6 — N=1, T<30, NaN column, zero equity, ρ float clamp, degenerate Σ enb_ratio=0 breach
- Integration 6 — CVaR/corr/ENB breach + allow + no-report + no-policy-block
- Precedence 1 — per_portfolio.max_leverage 가 per_portfolio_risk.max_cvar_pct 보다 우선
- E2E 3 — `compute_portfolio_risk_from_df` → Snapshot → evaluate (CVaR/corr/ENB 각 1)
- Observability 3 — rule_id 문자열 동등, ShortSampleWarning 카테고리, Decision.message 감사로그 포맷
- Benchmark 1 — `evaluate()` p99 < 100µs (D3 empirical gate; 실측 통과)

## D3 (hot-path 불악화) empirical 검증

Architect 지적대로 `evaluate()` 는 이미 `sector_limits` 루프로 non-O(1). Amendment #7 벤치가 통과해 **`portfolio_risk` 주입 시에도 p99 < 100µs** 를 확인 (테스트 `test_evaluate_latency_p99_under_100us` green). `portfolio_risk is None` 경로는 if 가드 하나만 실행.

## AC 매핑

| # | Issue AC | 구현 | 증명 |
|---|----------|------|------|
| AC1 | `portfolio.py` 상관/CVaR/VaR | `average_pairwise_correlation`, `historical_cvar`, wrapper `var_pct` | Unit 13 + Edge 6 |
| AC2 | LW shrinkage Σ | `shrinkage_covariance` | `test_shrinkage_covariance_*` |
| AC3 | 상관 경고/halt | `on_corr_breach=BLOCK`, `on_enb_breach=HALT` 기본값 | E2E corr/enb path |
| AC4 | 2+ 전략 CVaR 계산 | `compute_portfolio_risk_from_df` + `test_e2e_cvar_path` | E2E cvar path |
| AC5 | 상관 임계치 초과 경고 | 통합 + 관측 테스트 | `test_evaluate_corr_breach_blocks`, `test_breach_emits_rule_id_exactly` |
| AC6 | 단위 테스트 + DSL 연동 | 33 신규 + 기존 12 green | pytest 결과 |

## 라이브 스모크 (실측 데이터)

Binance public REST — BTC·ETH·BNB·SOL·XRP **1년 일봉 (365일)** `compute_portfolio_risk_from_df` 투입:

| 지표 | 실측 |
|---|---|
| CVaR(97.5%) | 8.27 % (최악 2.5% 날 평균 손실) |
| VaR(97.5%)  | 5.47 % |
| 평균 pairwise ρ | **0.774** (5개 코인이 거의 한 덩어리) |
| Meucci ENB | 1.02 / 5 — 실질 1개 짜리 |
| ENB/N | 0.203 (권고 하한 0.3 미달) |

정책 시뮬:
- `conservative (max_cvar=0.03)` → **REDUCE** (`per_portfolio_risk.max_cvar_pct`, `cvar 0.0827 > 0.0300`)
- `neutral      (max_cvar=0.08)` → **REDUCE** (근접 초과)
- `aggressive   (max_cvar=0.20)` → **ALLOW**

→ 수학 엔진·pydantic 모델·`evaluate()` 배선이 **실시장 데이터에서도 무결 동작**. 정책 grading 이 의미 있게 작동.

## 후속 이슈 (본 PR 에서 분기)

본 PR 에서 발견된 공백은 두 레이어로 나눔:
- **본 PR 에 포함** (배선 스텁) — `src/portfolio/orchestrator.py` + `.ai.md` + `CLAUDE.md` 가이드 → 미래 에이전트가 **리스크 연동 규칙을 자동 인지**
- **별도 이슈로 분기**:
  - **#78** `feat: 멀티 전략 비동기 실행 오케스트레이터` — depends: #69·#70·#76. 본 PR 의 sync stub 을 async 로 확장.
  - **#79** `feat: 전략 카탈로그 확장 (Mean Reversion + Breakout + Vol-filtered Momentum)` — depends: #71·#76·#78. 단일 `momo-btc-v2` → 3+ 전략으로 다변화 입증.
  - **#80** `feat: 라이브 실행 프레임워크 (PaperBroker + Phase 1 Shadow)` — depends: #73·#78·#70·#69. `29-paper-to-live-protocol` Phase 1 구현.

## Follow-ups (out of scope)

- EVT tail 추정 (`19-portfolio-risk.md §4.2`) — v3
- Factor exposure 제약 (§5 Fama-French) — v3
- CVaR 최적화 (Rockafellar-Uryasev LP) — v3
- `observability` wiring (Prometheus 실 카운터) — #80 에서 부분 처리
- `PositionSizer` 구현 (#69) — `shrinkage_covariance` 반환 배열 계약 확정
- Rolling LW Σ 스트림 업데이트 — 성능 튜닝 필요

## 다음 단계

커밋·PR 작성은 사용자 승인 후 수동 진행 (CLAUDE.md 행동 규칙).
