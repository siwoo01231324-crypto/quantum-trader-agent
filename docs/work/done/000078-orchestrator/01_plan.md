# 01_plan — #78 멀티 전략 비동기 실행 오케스트레이터

> ralplan consensus iter 2 완료 · Planner → Architect (NEEDS_MAJOR_REVISION) → Critic (APPROVE_WITH_POLISH_BAKED, 13 amendments baked)
> 작성: 2026-04-24

## AC 체크리스트 (이슈 본문 참조)
- [ ] `src/portfolio/orchestrator.py` async 확장 (#70 stub 기반)
- [ ] `run_bar(ts, market_snapshot) -> list[OrderIntent]` tick driver
- [ ] 전략별 일수익률 시계열 → `compute_portfolio_risk_from_df` → `Snapshot.portfolio_risk`
- [ ] Signal → PositionSizer 배선 (#69, #76 활용)
- [ ] 리스크 breach 시 OrderIntent 제외
- [ ] 단위 + 통합 테스트 (정상·breach·사이저 skip·전략 예외 격리)

## 참고 research
- [[09-system-components]] §1 파이프라인 + §2 컴포넌트 테이블 + §5 Phase 1 MVP + §6 불변식
- [[19-portfolio-risk]] §6 v2 delivered (#70) — consumer
- [[20-position-sizing]] §7.1 PositionSizer Protocol
- [[risk-rule-dsl]] §2.2 precedence
- [[signal-interface]] §3 Signal dataclass (#76)
- `src/portfolio/orchestrator.py` — #70 sync stub (StrategyOrchestrator)

## 의존성 (전부 merged)
- #69 포지션 사이징 ✅
- #70 포트폴리오 리스크 ✅
- #76 Signal 인터페이스 확장 ✅

---

## 구현 계획

### A. RALPLAN-DR Summary (iter 2 최종, deliberate mode)

#### Principles (6, P6 added)
1. **Sync API preserved** — 기존 #70 sync stub API 시그니처 전부 보존 (`register_strategy_returns`, `refresh_portfolio_risk`, `evaluate_order`, `strategy_reliability_score`).
2. **Async only where helps** — `run_bar` / refresh loop 만 async. 계산/결정 경로는 sync 유지.
3. **Exception isolation 1st-class** — explicit state machine + 구조화 로그 + 메트릭 emit.
4. **Single risk evaluator** — `compute_portfolio_risk_from_df` + `risk.dsl.evaluate` 재사용 (불변식 #6 준수, LLM 결정 위임 금지).
5. **Interface contracts frozen** — #79 (Signal Router) / #80 (Broker Executor) 가 재작업 없이 소비 가능한 형태로 FREEZE.
6. **(NEW, P6) Composition over duck-typing** — 2-class composition 구조. public `AsyncStrategyOrchestrator` 가 private `_SyncStrategyOrchestrator` 를 감싸 lock / 타입 모호성 제거.

#### Decision Drivers (top 3)
- **DR1 #70 회귀 금지** — 기존 orchestrator sync 테스트가 재작성 없이 통과해야 함.
- **DR2 동시성 안전** — 동일 strategy.on_bar 가 refresh 중에도 안전해야 함 (lock 경합 없이).
- **DR3 하위 이슈 재작업 최소화** — #79/#80 가 소비할 interface 가 얼어있어야 함.

#### Finalized Decisions D1–D7 (iter 2 bake, no alternatives)

- **D1 (A1) — 2-class composition**
  - Public `AsyncStrategyOrchestrator` (src/portfolio/_async_orchestrator.py).
  - Private `_SyncStrategyOrchestrator` (renamed from `StrategyOrchestrator`, src/portfolio/orchestrator.py).
  - Sync APIs (`register_strategy_returns`, `refresh_portfolio_risk`, `evaluate_order`, `strategy_reliability_score`) → sync class, **lock-free**.
  - Async APIs (`run_bar`, start/stop refresh loop) → async class, `asyncio.Lock` 보호.
  - `src/portfolio/__init__.py` → `AsyncStrategyOrchestrator`, `OrderIntent` 만 공개. Sync 는 `_` prefix 로 private.
  - 기존 #70 테스트는 async class 를 통한 sync-path delegation 으로 통과.

- **D2 (A2) — explicit Protocol split**
  - `src/backtest/protocol.py` 에 `AsyncStrategy(Protocol)` 추가: `async def on_bar(ctx: BarContext) -> Signal | None`.
  - 기존 `Strategy(Protocol)` (sync `on_bar`) **무변경**.
  - 디스패치: `inspect.iscoroutinefunction(strategy.on_bar)` 로 런타임 분기.
  - `engine.py` (#76 백테스트) 는 sync `Strategy` 만 계속 소비 — 변경 없음.

- **D3 (A3) — quarantine state machine explicit**
  - `_fail_count: dict[strategy_id, int]` (session-scoped).
  - 증가 조건: strategy.on_bar 가 `Exception` 또는 `asyncio.TimeoutError` 발생.
  - **리셋 조건: on_bar 가 Signal (또는 None) 을 정상 반환** → `_fail_count[sid] = 0`.
  - Quarantine 진입: `_fail_count[sid] >= 3` → `_quarantined.add(sid)` + `log.warning(strategy_quarantined)` + `metrics.counter("orchestrator.quarantine").inc()`.
  - Quarantine 된 전략의 on_bar 호출 자체 skip.
  - Timeout = failure (별도 상태 아님).

- **D4 (A4) — resolve_size location**
  - 위치: `src/portfolio/sizing.py::resolve_size(signal: Signal, recent_returns: pd.Series | None) -> float`.
  - `src/backtest/sizing.py` **사용 금지** (기존 `src/risk/sizing.py` Kelly 와 의미 충돌).
  - `src/backtest/engine.py` 는 `from portfolio.sizing import resolve_size` 로 import, 내부 `_resolve_size = resolve_size` 별칭만 유지 (기존 engine 테스트 하위호환).

- **D5 (A5) — bar-clock refresh mode**
  - ctor 파라미터: `refresh_every_n_bars: int | None = None`.
  - `None` → wall-clock mode: `asyncio.Task` + jittered `300s` 기본 주기.
  - `int` (예: 10) → bar-driven mode: `run_bar` 내부 `_bar_count` 증가, refresh 는 bar index `i` 처리 **직후** `(i+1) % n == 0` 일 때 발화.
  - 명세 핀: "refresh fires after processing bar at index `i` where `(i+1) % n == 0` — never before bar 0; first refresh at bar index n-1 (0-indexed)".
  - 예: n=10 → refresh at bar index 9, 19, 29 ...

- **D6 (A6) — defer reliability gate**
  - `strategy_reliability_score` 는 public API 로 남겨두되, `run_bar` 내부 gate 에 **wiring 하지 않음**.
  - 후속 이슈로 분리.

- **D7 (A7) — delete BrokerExecutor, retain OrderIntent**
  - `src/portfolio/broker_executor.py` 본 PR scope 에서 **삭제**.
  - `OrderIntent` 는 `run_bar` 반환 타입이므로 유지 — 위치: `src/portfolio/order_intent.py` (dataclass only, no Protocol).
  - `BrokerExecutor` + `OrderAck` + `submit()` 는 #80 로 defer.

#### Pre-mortem (6 scenarios, deliberate)

| # | Scenario | Mitigation | Test |
|---|---|---|---|
| S1 | Lock 경합으로 run_bar p99 지연 폭증 | sync path lock-free (D1), async lock 은 refresh write 시만 획득 | `test_run_bar_latency_p99` (<50ms) |
| S2 | 전략 예외로 오케스트레이터 크래시 | D3 state machine + per-strategy try/except | `test_strategy_exception_isolated` |
| S3 | Timeout 한 번에 quarantine 오발동 | 3회 consecutive (성공 시 리셋) | `test_quarantine_resets_on_success` |
| S4 | Async/sync protocol 혼재로 engine 깨짐 | D2 Protocol 분리 + inspect 디스패치 | `test_mixed_sync_async_strategies` |
| **S5** (NEW) | **Class misuse** — 개발자가 `_SyncStrategyOrchestrator` 직접 생성 → signal 미발화 | D1 `_` prefix + `__init__.py` export 제어 (C1) | `test_sync_class_not_exported` |
| **S6** (NEW) | **Bar-clock off-by-one** — refresh 가 잘못된 bar 에서 발화 | 명세 핀 `(bar_idx+1) % n == 0` (C3) | `test_bar_clock_refresh_timing_exact` |

#### Verification plan (unit / integration / e2e / observability / bench)

**Unit** (tests/test_portfolio_orchestrator_async.py, tests/test_portfolio_sizing.py, tests/test_async_strategy_protocol.py):
- `test_run_bar_returns_order_intents_on_normal_path`
- `test_run_bar_filters_breached_strategies`
- `test_strategy_exception_isolated` (S2)
- `test_quarantine_after_three_failures` (D3)
- `test_quarantine_resets_on_success` (S3)
- `test_quarantine_counts_timeout_as_failure`
- `test_mixed_sync_async_strategies` (S4)
- `test_bar_clock_refresh_timing_exact` (S6, C2): 30 bars, `n=10`, refresh 발화 bar index 리스트 = `[9, 19, 29]`.
- `test_wallclock_refresh_task_cancellable`
- `test_sync_class_not_exported` (S5, C1): `from portfolio import __all__` → `{"AsyncStrategyOrchestrator", "OrderIntent"}`.
- `test_public_api_exposes_only_async` (C1).
- `test_resolve_size_respects_kelly_fraction`
- `test_resolve_size_none_returns_means_skip`

**Integration**:
- `test_end_to_end_bar_flow`: 3 strategies (2 sync + 1 async) × 50 bars, wall-clock refresh off, bar-clock n=10 → breach 시 OrderIntent 제거 확인 + snapshot 갱신 타이밍 확인.

**e2e**: N/A (live broker 는 #80).

**Observability**: structured log (`strategy_id`, `bar_idx`, `fail_count`, `quarantined`) + counter metric (`orchestrator.quarantine`, `orchestrator.bar_latency_ms`).

**Bench (A8)** — `tests/test_orchestrator_bench.py`:
```python
def test_run_bar_latency_p99():
    orch = AsyncStrategyOrchestrator(strategies=_ten_noop_strategies())
    for _ in range(10):  # warmup
        asyncio.run(orch.run_bar(...))
    samples = []
    for _ in range(1000):
        t0 = time.perf_counter()
        asyncio.run(orch.run_bar(...))
        samples.append((time.perf_counter() - t0) * 1000)  # ms
    assert numpy.percentile(samples, 99) < 50.0
```
No pytest-benchmark dep.

---

### B. ADR-lite (D1–D7)

| ID | Decision | Drivers | Alternatives considered | Why chosen | Consequences | Follow-ups |
|---|---|---|---|---|---|---|
| **D1** | 2-class composition (public async wraps private sync) | DR1, DR2, P6 | (a) single class with dual sync/async methods; (b) duck-typing lock-per-method | lock-free sync path; clear API surface; #70 regression-free | 파일 2개 유지; `__init__.py` 주의 | `test_sync_class_not_exported` |
| **D2** | Explicit `AsyncStrategy(Protocol)` + inspect dispatch | DR1, DR3 | (a) 단일 Protocol + sync/async duck-typing; (b) abstract base class | 타입체커 친화, engine.py 무변경, #76 backtest 영향 0 | inspect 호출 per-bar (무시 가능) | — |
| **D3** | 3-failure counter, reset on success, timeout=failure | DR2 | (a) percentile-based reliability gate; (b) circuit breaker with half-open | 단순 · 결정론적 · test 가능 | session-scoped state만; persistence는 #80 | quarantine persistence issue |
| **D4** | `src/portfolio/sizing.py::resolve_size` | DR3 | `src/backtest/sizing.py` (기각: `src/risk/sizing.py` Kelly 와 의미 충돌) | 도메인 경계 정합 (portfolio 가 sizing 결정) | engine.py import 1줄 추가 | — |
| **D5** | `refresh_every_n_bars` ctor param + 명시 핀 `(i+1)%n==0` | DR2 | (a) wall-clock only; (b) 매 bar | 백테스트/페이퍼 재현성, 실시간성 trade-off 조정 가능 | 명세 문서에 off-by-one 핀 | — |
| **D6** | reliability gate defer | DR3 | run_bar 에 gate 즉시 통합 | scope 축소 · #78 명확화 | `strategy_reliability_score` public 남김 | follow-up issue 오픈 |
| **D7** | BrokerExecutor defer, `OrderIntent` 유지 | DR3 | 본 PR 에서 BrokerExecutor 까지 구현 | 450 line spec cap 내 유지, #80 책임 분리 | `OrderIntent` 위치 이동 (order_intent.py) | #80 |

---

### C. Implementation Steps (7 phases, file-by-file)

#### Phase 1 — Spec (docs/specs/orchestrator-interface.md, ≤450 lines, A9)
1. §1 Scope · §2 불변식 #6 재확인 · §3 Protocol section (C3: sync/async variants **side-by-side**, 중복 금지).
2. §4 `AsyncStrategyOrchestrator` public API + `_SyncStrategyOrchestrator` private API (composition 관계 명시).
3. §5 `run_bar` semantics + `OrderIntent` schema.
4. §6 quarantine state machine (D3): 전이표 (NORMAL→NORMAL, NORMAL→FAIL+1, FAIL+n→QUARANTINED at n=3, NORMAL↶ on success).
5. §7 refresh mode (D5): wall-clock / bar-clock + 핀 `(bar_idx+1) % n == 0`.
6. §8 freeze surface for #79/#80 + Out-of-scope.

#### Phase 2 — data types & pure functions
7. **Create `src/portfolio/order_intent.py`** — `@dataclass(frozen=True) OrderIntent(strategy_id, symbol, side, qty, reason, meta)`.
8. **Create `src/portfolio/sizing.py`** — `resolve_size(signal: Signal, recent_returns: pd.Series | None) -> float` (D4). Kelly 경로는 기존 `src/risk/sizing.py` 재사용 (`from risk.sizing import kelly_fraction`).
9. **SKIP** — no BrokerExecutor this PR (D7/A7).
10. **Modify `src/backtest/protocol.py`** — `AsyncStrategy(Protocol)` 추가 (D2). 기존 `Strategy(Protocol)` 무변경.
11. **Modify `src/backtest/engine.py`** — import 변경 `from portfolio.sizing import resolve_size`, 내부 `_resolve_size = resolve_size` alias.

#### Phase 3 — orchestrator classes
12. **Rename class** in `src/portfolio/orchestrator.py`: `StrategyOrchestrator` → `_SyncStrategyOrchestrator`. 파일 경로 그대로 유지. 기존 sync 메서드 4개 그대로 (lock-free).
13. **Create `src/portfolio/_async_orchestrator.py`**:
    ```python
    class AsyncStrategyOrchestrator:
        def __init__(self, strategies, risk_rules, refresh_every_n_bars: int | None = None, refresh_interval_sec: float = 300.0):
            self._sync = _SyncStrategyOrchestrator(strategies, risk_rules)
            self._lock = asyncio.Lock()
            self._bar_count = 0
            self._refresh_every_n_bars = refresh_every_n_bars
            self._fail_count: dict[str, int] = defaultdict(int)
            self._quarantined: set[str] = set()

        async def run_bar(self, ts, market_snapshot) -> list[OrderIntent]: ...
        async def start_wallclock_refresh(self): ...
        async def stop_wallclock_refresh(self): ...

        # Sync delegation (lock-free)
        def register_strategy_returns(self, *a, **kw): return self._sync.register_strategy_returns(*a, **kw)
        def refresh_portfolio_risk(self, *a, **kw): return self._sync.refresh_portfolio_risk(*a, **kw)
        def evaluate_order(self, *a, **kw): return self._sync.evaluate_order(*a, **kw)
        def strategy_reliability_score(self, *a, **kw): return self._sync.strategy_reliability_score(*a, **kw)
    ```
14. **run_bar 로직**:
    - for each strategy in self._strategies:
      - if sid in `_quarantined`: continue.
      - dispatch: `inspect.iscoroutinefunction(strategy.on_bar)` → await, else call sync.
      - wrap in try/except (Exception, asyncio.TimeoutError): `_fail_count[sid] += 1`; if `>= 3` → add to `_quarantined`, log, metric; continue.
      - 성공 시 `_fail_count[sid] = 0` (D3 reset).
      - signal → `resolve_size` → `evaluate_order` (breach filter) → `OrderIntent`.
    - `_bar_count += 1`; bar-clock mode: if `self._refresh_every_n_bars and self._bar_count % self._refresh_every_n_bars == 0`: `async with self._lock: self._sync.refresh_portfolio_risk(...)` (D5, S6).
15. **Modify `src/portfolio/__init__.py`** (C1):
    ```python
    from .order_intent import OrderIntent
    from ._async_orchestrator import AsyncStrategyOrchestrator
    __all__ = ["AsyncStrategyOrchestrator", "OrderIntent"]
    ```
    `_SyncStrategyOrchestrator` 는 export 하지 않음.

#### Phase 4 — tests
16. `tests/test_async_strategy_protocol.py` — Protocol 분리 + inspect dispatch.
17. `tests/test_portfolio_sizing.py` — resolve_size 단위.
18. `tests/test_portfolio_orchestrator_async.py` — run_bar · quarantine state machine · bar-clock timing (S6) · export policy (S5) · public API 제약 (C1) · 예외 격리 (S2) · end-to-end (integration).
19. `tests/test_orchestrator_bench.py` — `test_run_bar_latency_p99` (A8, `time.perf_counter` + `numpy.percentile`).

#### Phase 5 — docs / .ai.md
20. Update `src/portfolio/.ai.md` — 2-class composition 구조 명시.
21. Update `src/backtest/.ai.md` — `AsyncStrategy` Protocol 언급.
22. Update `docs/background/09-system-components.md` — Phase 1 MVP orchestrator 엔트리 갱신.
23. Update `CLAUDE.md` 작업흐름 (불필요 시 skip).

#### Phase 6 — invariants / lint
24. Run `scripts/check_invariants.py --strict`.
25. `_check_llm_delegation` scan_dirs 확인 (A10): `src/portfolio/` 이미 포함 — 변경 없음.

#### Phase 7 — AC walk-through
26. AC 5개 각 항목 실제 테스트로 매핑 확인 (§D).

---

### D. AC Mapping

| AC / Freeze | Implementation step | Test |
|---|---|---|
| async `run_bar` tick driver | Step 13, 14 | `test_run_bar_returns_order_intents_on_normal_path` |
| 전략별 returns → `compute_portfolio_risk_from_df` → `Snapshot` | Step 14 refresh 경로 | `test_end_to_end_bar_flow` |
| Signal → PositionSizer 배선 | Step 8, 14 | `test_resolve_size_respects_kelly_fraction` |
| 리스크 breach → OrderIntent 제외 | Step 14 evaluate_order 루프 | `test_run_bar_filters_breached_strategies` |
| 단위+통합 (정상·breach·skip·예외 격리) | Phase 4 전부 | 상기 테스트 전체 |
| **Freeze for #79**: `AsyncStrategy` Protocol + `run_bar` 시그니처 | Step 10, 13 | `test_async_strategy_protocol_signature` |
| **Freeze for #80**: `OrderIntent` schema | Step 7 | `test_order_intent_schema_frozen` |

---

### E. Risks & Mitigations

| # | Risk | Severity | Mitigation | Owner |
|---|---|---|---|---|
| R1 | #70 sync 테스트 회귀 | HIGH | D1 delegation + 기존 테스트 재실행 pre-commit | Executor |
| R2 | run_bar p99 > 50ms | MED | A8 bench, lock-free sync path | Executor |
| R3 | Quarantine false-positive | MED | D3 reset-on-success + integration test | Executor |
| R4 | Bar-clock off-by-one | MED | S6 test + 명세 핀 | Executor |
| R5 | Class misuse (sync 직접 생성) | LOW | S5 test + `_` prefix + export 제어 | Reviewer |
| **NR1** | Async lock 누락으로 refresh/run_bar race | MED | `asyncio.Lock` refresh write 시 필수 획득, run_bar 는 sync delegation 만 | Executor |
| **NR2** | Protocol inspect dispatch 오판 | LOW | `test_mixed_sync_async_strategies` 로 혼재 검증 | Executor |
| **NR3** | spec drift between 문서/코드 | LOW | §3 single Protocol section (C3) + invariant check | Writer |

---

### F. Files created / modified

**Created**
- `src/portfolio/order_intent.py`
- `src/portfolio/sizing.py`
- `src/portfolio/_async_orchestrator.py`
- `docs/specs/orchestrator-interface.md`
- `tests/test_portfolio_orchestrator_async.py`
- `tests/test_portfolio_sizing.py`
- `tests/test_async_strategy_protocol.py`
- `tests/test_orchestrator_bench.py`

**Modified**
- `src/portfolio/orchestrator.py` (class rename `StrategyOrchestrator` → `_SyncStrategyOrchestrator`, API 무변경)
- `src/portfolio/__init__.py` (export control: `AsyncStrategyOrchestrator`, `OrderIntent` 만)
- `src/backtest/protocol.py` (add `AsyncStrategy(Protocol)`)
- `src/backtest/engine.py` (import from `portfolio.sizing`, `_resolve_size` alias 유지)
- `src/portfolio/.ai.md`
- `src/backtest/.ai.md`
- `docs/background/09-system-components.md`
- `CLAUDE.md` (필요 시만)

---

### G. Interface freezes (for #79 / #80)

**Frozen (this PR)**
- `AsyncStrategy(Protocol).on_bar(ctx: BarContext) -> Signal | None` (async).
- `AsyncStrategyOrchestrator.run_bar(ts: datetime, market_snapshot: MarketSnapshot) -> list[OrderIntent]`.
- `OrderIntent` dataclass schema: `strategy_id, symbol, side, qty, reason, meta`.
- `src/portfolio.__all__ == ["AsyncStrategyOrchestrator", "OrderIntent"]`.

**NOT frozen (deferred)**
- `BrokerExecutor` Protocol, `submit()`, `OrderAck` → #80.
- Reliability gate wiring → follow-up.
- Quarantine persistence → #80.

---

### H. Out-of-scope

- Reliability gate integration in `run_bar` (A6/D6).
- `BrokerExecutor` + `submit()` + `OrderAck` (→ #80, A7/D7).
- ENB weighting.
- KOSPI200 RankIC 계측.
- Native async broker (→ #73).
- Quarantine state persistence across sessions (→ #80).

---

### I. 승인

Critic verdict: **APPROVE_WITH_POLISH_BAKED**
13 amendments baked (5 Architect structural + 5 Architect polish + 3 Critic).
Ready for implementation via `/si 78` → `/fi 78` flow.
