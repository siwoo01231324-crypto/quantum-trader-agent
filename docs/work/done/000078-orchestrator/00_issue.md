# feat: 멀티 전략 비동기 실행 오케스트레이터 (전략 스케줄링 + 리스크/사이저 배선)

## 목표
복수 전략의 **동시 비동기 실행** 을 담당하는 중앙 오케스트레이터 구현. 전략별 시그널 → 포트폴리오 리스크 평가 → 포지션 사이징 → 주문 생성까지의 파이프라인을 연결하고, 전략별 일수익률 시계열을 집계해 `risk.compute_portfolio_risk_from_df` 에 흘려 `Snapshot.portfolio_risk` 를 갱신한다.

## 배경

### 기존 자산 (머지 완료·구현중)
- [[09-system-components]] §1 Mermaid 에 `StrategyEngine → PositionSizer → RiskManager → OMS` 파이프라인 정의됨. 지금 각 박스는 있으나 **서로 배선 안 됨**.
- `src/risk/` (#70) — `compute_portfolio_risk_from_df`, `evaluate(policy, snap)` 제공. `Snapshot.portfolio_risk` 주입 경로 준비됨.
- `src/backtest/strategies/momo_btc_v2.py` — `Strategy` protocol 1구현체. 단일 전략 백테스트 전용.
- `src/brokers/{kis,binance}` (#68) — 커넥터 준비됨. 주문/계정 API.

### 공백 (본 이슈 스코프)
- 레포 전체에서 `from risk import` 를 사용하는 코드 **0건**.
- 여러 전략을 동시에 돌릴 **이벤트 루프·스케줄러 없음**.
- 전략 시그널을 모아서 사이저·리스크 평가기로 흘려보내는 **배선 없음**.
- 전략별 일수익률 시계열을 [[19-portfolio-risk]] 공분산 입력 포맷(T×N DataFrame) 으로 정규화하는 **aggregator 없음**.

## 범위
- `src/portfolio/orchestrator.py` (또는 `src/engine/orchestrator.py`) — 메인 오케스트레이터.
- `src/portfolio/aggregator.py` — 전략별 일수익률을 T×N DataFrame 으로 집계 (PIT 보존).
- `src/portfolio/.ai.md` — 디렉토리 목적/구조 명시.
- `tests/test_orchestrator.py` — 2+ 전략 더미 실행 → 리스크 평가 → 결정 트레이스.
- `CLAUDE.md` — 오케스트레이터가 파이프라인의 단일 조립점임을 명시.

## 인터페이스 계약 (제안)
```python
class StrategyOrchestrator:
    def __init__(self, strategies: list[Strategy], policy: Policy, sizer: PositionSizer) -> None: ...
    async def run_bar(self, ts: datetime, market_snapshot: MarketSnapshot) -> list[OrderIntent]:
        # 1) 각 전략 on_bar(ctx) 병렬 실행 (asyncio.gather)
        # 2) 전략별 실현 수익률 스트림 업데이트 → aggregator
        # 3) compute_portfolio_risk_from_df(returns_df) → PortfolioRiskReport
        # 4) Snapshot(portfolio_risk=report) 구성
        # 5) signals → sizer.size(signal, account, cov, policy) → qty
        # 6) evaluate(policy, snap_per_order) → Decision
        # 7) ALLOW 인 Decision 만 OrderIntent 리스트로 반환
```

## 완료 기준
- [ ] `src/portfolio/orchestrator.py` 구현 + `.ai.md` 필수
- [ ] 2+ 전략 mock 으로 `run_bar` 루프 end-to-end 트레이스 통과
- [ ] 전략별 수익률 시계열 → `compute_portfolio_risk_from_df` 입력으로 흘러 `PortfolioRiskReport` 생성
- [ ] 생성된 Report 가 `Snapshot.portfolio_risk` 로 주입되고, `evaluate()` 가 breach 판정시 order 목록에서 제외
- [ ] 단위 테스트 (정상 흐름 · 리스크 breach · 사이저 skip · 전략 예외 격리)
- [ ] 통합 테스트 — [[12-validation-protocol]] 의 runner 로 2전략 1주일 시뮬

## 의존성
- **#69** (포지션 사이징) — `sizer.size(signal, ...)` 호출부. 없으면 오케스트레이터는 "임의 수량 1" 더미 사이저 플러그인 유지 상태에서 시작.
- **#70** (포트폴리오 리스크) — 본 이슈의 `compute_portfolio_risk_from_df` / `evaluate` / `Snapshot.portfolio_risk` 소비자. **Merged 필요.**
- **#76** (Signal 인터페이스 확장) — 전략 출력 표준. 없으면 현 `Signal{side, strength, ttl}` (09-system-components §2) 수준에서 운영.

## 참고 research
- [[09-system-components]] §1 (파이프라인 다이어그램), §2 (컴포넌트 인터페이스 표 — StrategyEngine·PositionSizer·RiskManager 의 책임과 시그니처), §5 (Phase 1 MVP 수용 기준), §6 (불변식 1·3 — 경계 스키마·RiskManager 통과 강제)
- [[19-portfolio-risk]] §6 v2 delivered (본 이슈가 consumer)
- [[20-position-sizing]] §7.1 PositionSizer Protocol (cov: np.ndarray 계약)
- [[13-feature-alpha-catalog]] — 피처 파이프라인 (#71) 이 공급하는 값이 전략 입력
- [[risk-rule-dsl]] §2.2 Evaluation precedence — 오케스트레이터가 breach rule_id 를 그대로 observability 로 forward

## 주의사항
- **주문·리스크 결정을 LLM 에 위임 금지** (CLAUDE.md 불변식 #6).
- **Idempotency-key 필수** ([[09-system-components]] §6 불변식 #2).
- **Reconciler 경로** — 전략 수익률 집계와 실체결 간 drift 체크 (별도 후속 이슈 후보).
- 본 이슈는 **페이퍼/백테스트 경로** 만 포함. 라이브 (PaperBroker + 실시간 WS) 는 별도 이슈 C.

## 후속 (out of scope)
- 실시간 WebSocket 기반 이벤트 루프 (→ C 이슈)
- 전략 간 capital budgeting / allocation weights 재계산 (→ #69 확장)
- RL agent 전략 지원 (→ v3)

---

## 🔍 특허 리서치 (#84) 보강

본 이슈의 "리스크/사이저 배선" 범위에 특허 리서치 결과에서 도출된 차용 제안 1건이 연관된다.

### CVaR 위반 시 점진적 포지션 감축 루프
- **출처**: `docs/background/32-patents-portfolio-optimization.md` §4 제안 D
- **특허**: AIG/Validus US10664914B2 (CVaR 반복 제약 강화)
- **내용**:

**제안 D: CVaR 위반 시 반복적 제약 강화 패턴 도입**

- **적용 대상**: `src/risk/portfolio_orchestrator.py` — 현재 CVaR 임계치 초과 시 즉시 `reduce`/`halt` 액션을 트리거하는 구조
- **접목 방법**: 이 특허의 반복 정제 패턴을 차용하여, CVaR 위반 발생 시 즉시 `halt` 대신 **점진적 포지션 축소 루프**를 도입한다. 예: `max_cvar_pct` 임계 초과 → 가장 큰 CVaR 기여 종목을 5% 축소 → CVaR 재계산 → 여전히 초과 → 추가 5% 축소 → 최대 N회 반복 후에도 미충족이면 `halt`. 이는 `portfolio_orchestrator.py`의 CVaR 체크 함수에 `reduce_loop(max_iter=10, step_pct=0.05)` 내부 루프로 구현 가능하다.
- **기대 효과**: 시장 충격 시 급격한 전량 청산 대신 점진적 포지션 감축이 가능해져 실현 슬리피지를 낮추고, 리스크 감축과 시장 충격 비용 간의 균형을 맞출 수 있다.
- **저비용 검증**: 단위 테스트에서 CVaR 임계 초과 시나리오를 시뮬레이션하고 루프 종료 조건(충족 vs. max_iter 도달) 분기 검증.

### 관련 연구 노트
- [[32-patents-portfolio-optimization]]

### 연결 이슈
- #70 포트폴리오 리스크 관리 (CVaR 기반) — 본 제안의 확장 출발점
- #84 특허 리서치 이슈



## 작업 내역

