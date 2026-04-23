# feat: 포트폴리오 리스크 관리 (CVaR + 상관 매트릭스)

## 목표
멀티 전략 운영 시 **전체 포트폴리오 위험을 관리**하는 리스크 모듈 구현.

## 배경
- `docs/background/19-portfolio-risk.md` 에 이론 조사 완료 (Ledoit-Wolf, CVaR, Fama-French)
- `src/risk/dsl.py` 에 개별 전략 리스크 룰은 있으나 포트폴리오 레벨 통합 없음

## 범위
- `src/risk/portfolio.py` — 상관 매트릭스, CVaR, 포트폴리오 VaR
- Ledoit-Wolf shrinkage 공분산 추정
- 전략 간 상관 기반 경고/halt

## 완료 기준
- [ ] 2+ 전략 시뮬레이션에서 포트폴리오 CVaR 계산
- [ ] 상관 임계치 초과 시 경고 로직
- [ ] 단위 테스트 + risk DSL 연동

## 선행 조건
- #67 (백테스트), 포지션 사이징 이슈

## 작업 내역

### 구현 (핵심)
- `src/risk/portfolio.py` 신규 — LW shrinkage Σ · Historical CVaR(α=0.975) · Meucci ENB (PCA 엔트로피) · 평균 pairwise ρ · pandas wrapper + frozen `PortfolioRiskReport` + `ShortSampleWarning`
- `src/risk/dsl.py` 확장 — `PerPortfolioRisk` sibling 블록 (max_cvar_pct·max_corr_avg·min_enb_ratio·alpha·on_*_breach) + `Snapshot.portfolio_risk` Optional 필드 + `evaluate()` gated block (per_portfolio 뒤, per_position 앞 precedence 유지)
- `src/risk/__init__.py` — 7 신규 심볼 export
- `pyproject.toml` — `scikit-learn>=1.4` 추가

### 설계 결정 (ralplan 합의)
- **Option A-lite 채택** (Planner 초안 Option C → Architect 반박으로 역전). `evaluate()` 가 이미 `sector_limits` 루프로 non-O(1) 이라 분리 불필요 → 단일 evaluator + 단일 precedence chain 유지
- **의미론적 기본 action**: `on_cvar_breach=REDUCE` (주문 크기 비례), `on_corr_breach=BLOCK` (상태는 못 고침), `on_enb_breach=HALT` (구조 문제)
- numpy 중심 core + pandas wrapper (Sizer #69 `cov: np.ndarray` 계약과 호환)

### 테스트 (49개, 회귀 zero)
- 신규 37: unit 13 + edge 6 + integration 6 + precedence 1 + e2e 3 + observability 3 + bench 1 + orchestrator stub 4
- 기존 12 (`test_risk_dsl.py`) 전부 무수정 통과
- 전체 스위트 421/421 green
- `evaluate()` p99 < 100µs 실측 (bench gate 통과)
- 라이브 스모크: BTC/ETH/BNB/SOL/XRP 365일 실측 — ρ̄=0.774, ENB=1.02/5, CVaR=8.27% → 3 정책에서 REDUCE/REDUCE/ALLOW 로 의미있게 분기

### 스코프 확장 (배선 공백 해소)
- `src/portfolio/orchestrator.py` 스텁 — `from risk import` 첫 실사용처. `register_strategy_returns` / `refresh_portfolio_risk` / `evaluate_order` 인터페이스 고정 (#78 의 async 확장 지점)
- `src/backtest/strategies/.ai.md` "리스크 연동 (필수)" 섹션 추가
- `CLAUDE.md` "새 전략 추가 시 필수 (#70 이후)" 블록 추가 — 모든 LLM 세션이 자동 인지

### 문서
- `docs/specs/risk-rule-dsl.md` §2.2 precedence + §3 YAML 예시 + §7.1 rule_id 라벨 공간 + §8 v2 delivered
- `docs/background/19-portfolio-risk.md` §6 로드맵·§7 운영 체크리스트
- `docs/work/active/000070-portfolio-risk/02_implementation.md` 전 과정 기록 + 라이브 스모크 실측치

### 후속 이슈 (본 PR 에서 분기)
- **#78** 멀티 전략 비동기 실행 오케스트레이터 (depends: #69·#70·#76)
- **#79** 전략 카탈로그 확장 (depends: #71·#76·#78)
- **#80** 라이브 실행 프레임워크 PaperBroker + Phase 1 Shadow (depends: #73·#78·#70·#69)

