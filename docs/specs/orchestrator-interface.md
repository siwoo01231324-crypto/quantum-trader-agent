---
type: spec-architecture
id: orchestrator-interface
name: "AsyncStrategyOrchestrator 인터페이스 명세"
owner: siwoo
status: draft
tags: [portfolio, orchestrator, async]
---

# AsyncStrategyOrchestrator 인터페이스 명세

> 이 문서는 #78 구현의 공식 인터페이스 계약이다.
> #79 (Signal Router), #80 (Broker Executor) 가 소비하는 **동결(frozen) 인터페이스**를 정의한다.

---

## 1. 스코프

**포함 (이 PR)**
- `AsyncStrategyOrchestrator.run_bar(ts, market_snapshot) -> list[OrderIntent]` tick driver
- `Strategy` + `AsyncStrategy` Protocol (side-by-side, 단일 섹션)
- `OrderIntent` 데이터클래스 스키마
- Quarantine 상태 기계 (D3)
- Bar-clock / Wall-clock refresh mode (D5)
- 관측 로그 스키마 + 카운터 메트릭 이름

**제외 (후속 이슈)**
- `BrokerExecutor` Protocol, `submit()`, `OrderAck` → #80
- Reliability gate wiring → follow-up
- Quarantine 상태 영속성 → #80
- 멀티 거래소 라우팅, RL 에이전트, ENB 가중치

---

## 2. 불변식 재확인

CLAUDE.md 불변식 #6: **주문 실행·리스크 결정을 LLM 에 위임 금지**.
`run_bar` 내부 모든 결정(사이즈 계산, 리스크 판정, quarantine 진입)은 결정론적 코드만 실행한다.

---

## 3. Protocol 정의 (sync + async 나란히)

### 3.1 Strategy (sync, 기존 — 변경 없음)

```python
# src/backtest/protocol.py
from typing import Protocol, runtime_checkable
import pandas as pd

@runtime_checkable
class Strategy(Protocol):
    def on_init(self, context: dict) -> None: ...
    def on_bar(self, bar: Bar, history: pd.DataFrame, context: dict) -> Signal: ...
```

- `engine.py` (백테스트) 은 이 Protocol 만 소비한다.
- `AsyncStrategyOrchestrator` 도 sync 전략을 지원한다 (`inspect.iscoroutinefunction` 분기).

### 3.2 AsyncStrategy (신규, #78 동결)

```python
# src/backtest/protocol.py
class AsyncStrategy(Protocol):
    async def on_bar(self, ctx: object) -> "Signal | None": ...
```

- `ctx` 는 현재 `object` (덕타이핑). #79 에서 `BarContext` 타입으로 좁힌다.
- `Signal | None`: `None` 반환 → 이번 bar 에 대한 signal 없음 (skip).
- 런타임 분기: `inspect.iscoroutinefunction(strategy.on_bar)` → `True` 이면 `await`, `False` 이면 sync 호출.
- `engine.py` 는 sync `Strategy` 만 계속 소비 — `AsyncStrategy` 는 무관.

---

## 4. 클래스 구조 (2-class composition, D1)

```
src/portfolio/
├── orchestrator.py          _SyncStrategyOrchestrator  (private, lock-free sync)
├── _async_orchestrator.py   AsyncStrategyOrchestrator  (public, asyncio)
├── order_intent.py          OrderIntent dataclass
└── sizing.py                resolve_size()
```

### 4.1 `_SyncStrategyOrchestrator` (private)

- 기존 `StrategyOrchestrator` (#70) 의 rename. 하위 호환 alias 유지:
  `StrategyOrchestrator = _SyncStrategyOrchestrator`
- 메서드 4개 (시그니처 동결, lock-free):
  - `register_strategy_returns(strategy_id: str, series: pd.Series) -> None`
  - `refresh_portfolio_risk(ts: datetime | None = None) -> PortfolioRiskReport | None`
  - `evaluate_order(intent: Order, equity_krw: float, **snap_extras) -> Decision`
  - `strategy_reliability_score(strategy_id: str) -> float`
- 직접 생성 금지: `_` prefix + `__init__.py` export 제어.

### 4.2 `AsyncStrategyOrchestrator` (public, #78 동결)

```python
class AsyncStrategyOrchestrator:
    def __init__(
        self,
        strategies: list,
        risk_rules: Policy,
        refresh_every_n_bars: int | None = None,
        refresh_interval_sec: float = 300.0,
    ) -> None: ...

    # Tick driver
    async def run_bar(
        self, ts: datetime, market_snapshot: object
    ) -> list[OrderIntent]: ...

    # Wall-clock refresh loop
    async def start_wallclock_refresh(self) -> None: ...
    async def stop_wallclock_refresh(self) -> None: ...

    # Sync delegation (lock-free, pass-through to _SyncStrategyOrchestrator)
    def register_strategy_returns(self, strategy_id: str, series: pd.Series) -> None: ...
    def refresh_portfolio_risk(self, ts: datetime | None = None) -> PortfolioRiskReport | None: ...
    def evaluate_order(self, intent: Order, equity_krw: float, **snap_extras) -> Decision: ...
    def strategy_reliability_score(self, strategy_id: str) -> float: ...
```

**공개 API (`src/portfolio.__all__`)**:
```python
__all__ = ["AsyncStrategyOrchestrator", "OrderIntent"]
```

---

## 5. `run_bar` 시맨틱

### 5.1 시그니처

```python
async def run_bar(self, ts: datetime, market_snapshot: object) -> list[OrderIntent]:
```

- `ts`: bar 의 타임스탬프 (UTC).
- `market_snapshot`: 현재는 `object` (덕타이핑). #79 에서 `MarketSnapshot` 타입으로 좁힌다.
- 반환값: 리스크 게이팅을 통과한 `OrderIntent` 목록. 빈 리스트 가능.

### 5.2 처리 순서 (per bar)

```
for each strategy s in self._strategies:
    1. sid in _quarantined → skip (continue)
    2. dispatch: iscoroutinefunction(s.on_bar) → await s.on_bar(ctx)
                                               else s.on_bar(ctx)
    3. try/except (Exception | asyncio.TimeoutError):
         _fail_count[sid] += 1
         if _fail_count[sid] >= 3 → quarantine(sid)
         continue
    4. 성공 → _fail_count[sid] = 0
    5. signal is None → skip
    6. signal.action == "buy" → resolve_size(signal, recent_returns)
    7. evaluate_order(OrderIntent(...)) → Decision
    8. Decision.allow → append to results
_bar_count += 1
bar-clock mode → trigger refresh if needed
return results
```

### 5.3 `OrderIntent` 스키마 (동결, #80 소비)

```python
@dataclass(frozen=True, slots=True)
class OrderIntent:
    strategy_id: str
    symbol: str
    side: Literal["buy", "sell"]
    qty: float
    reason: str
    meta: dict | None = None
```

- `frozen=True`: 불변. 생성 후 변경 불가.
- `slots=True`: 메모리 효율.
- `meta`: 임의 확장 키-값. #80 에서 idempotency-key 등 추가 예정.

---

## 6. Quarantine 상태 기계 (D3)

| 이벤트 | 현재 상태 | 다음 상태 | 부수 효과 |
|---|---|---|---|
| `on_bar` 정상 반환 | NORMAL (fail=0) | NORMAL (fail=0) | `_fail_count[sid] = 0` |
| `on_bar` 정상 반환 | FAILING (fail=1,2) | NORMAL (fail=0) | `_fail_count[sid] = 0` |
| Exception / TimeoutError | NORMAL (fail=0) | FAILING (fail=1) | `_fail_count[sid] += 1` |
| Exception / TimeoutError | FAILING (fail=1) | FAILING (fail=2) | `_fail_count[sid] += 1` |
| Exception / TimeoutError | FAILING (fail=2) | QUARANTINED | `_quarantined.add(sid)` + log.warning + metric |
| (quarantined) | QUARANTINED | QUARANTINED | `on_bar` 호출 자체 skip |

**규칙 요약**:
- 임계값(threshold) = 3 (3번째 실패 시 quarantine 진입).
- 성공 시 즉시 리셋: `_fail_count[sid] = 0`.
- Timeout = Exception (별도 상태 없음).
- Session-scoped: 프로세스 재시작 시 초기화. 영속성은 #80.
- Quarantine 된 전략은 `on_bar` 호출 자체를 skip (CPU 낭비 없음).

---

## 7. Refresh 모드 (D5)

### 7.1 Bar-clock mode (`refresh_every_n_bars: int`)

**핀(pin)**: "refresh fires after processing bar at index `i` where `(i+1) % n == 0`"
- 첫 refresh: bar index `n-1` (0-indexed).
- 예: `n=10` → refresh at bar index 9, 19, 29 ...
- **Bar index 0 이전에는 발화 없음** (never before bar 0).
- 구현: `_bar_count += 1` 후 `_bar_count % n == 0` 체크.

```python
self._bar_count += 1
if self._refresh_every_n_bars and self._bar_count % self._refresh_every_n_bars == 0:
    async with self._lock:
        self._sync.refresh_portfolio_risk(ts=ts)
```

### 7.2 Wall-clock mode (`refresh_every_n_bars=None`)

- `start_wallclock_refresh()` 호출 시 `asyncio.Task` 생성.
- 기본 주기: `refresh_interval_sec` (기본 300s) + jitter.
- `stop_wallclock_refresh()` 로 Task 취소.
- refresh write 시 `asyncio.Lock` 획득; `run_bar` 는 sync delegation 만 → lock 경합 없음.

---

## 8. 동결 인터페이스 (#79 / #80 소비)

### #79 (Signal Router) 가 소비하는 인터페이스

- `AsyncStrategy(Protocol).on_bar(ctx: BarContext) -> Signal | None` (async).
- `AsyncStrategyOrchestrator.run_bar(ts: datetime, market_snapshot) -> list[OrderIntent]`.

### #80 (Broker Executor) 가 소비하는 인터페이스

- `OrderIntent` 데이터클래스 스키마 (§5.3).
- `src/portfolio.__all__ == ["AsyncStrategyOrchestrator", "OrderIntent"]`.

**변경 금지 (without #79/#80 동의)**:
- `AsyncStrategy.on_bar` 시그니처
- `AsyncStrategyOrchestrator.run_bar` 시그니처
- `OrderIntent` 필드 (추가는 허용, 제거/rename 금지)

---

## 9. 관측 스키마

### 9.1 구조화 로그 (JSONL)

모든 이벤트는 `logging.getLogger("portfolio.orchestrator")` 를 통해 구조화 로그로 emit.

| 이벤트 | 레벨 | 필드 |
|---|---|---|
| strategy exception | WARNING | `strategy_id`, `bar_idx`, `fail_count`, `exc_type` |
| strategy quarantined | WARNING | `strategy_id`, `bar_idx`, `fail_count=3`, `quarantined=True` |
| refresh triggered | DEBUG | `bar_idx`, `mode` (`bar_clock`\|`wall_clock`) |
| run_bar complete | DEBUG | `bar_idx`, `ts`, `n_intents`, `latency_ms` |

### 9.2 카운터 메트릭 이름

| 메트릭 | 타입 | 설명 |
|---|---|---|
| `orchestrator.quarantine` | counter | quarantine 진입 횟수 (strategy_id 태그) |
| `orchestrator.bar_latency_ms` | histogram | `run_bar` 총 소요 시간 (ms) |
| `orchestrator.strategy_exception` | counter | on_bar 예외 발생 횟수 (strategy_id 태그) |
| `orchestrator.order_intents_emitted` | counter | 리스크 통과 OrderIntent 개수 |

---

## 관련 노트

- [[09-system-components]] §2 컴포넌트 테이블 + §5 Phase 1 MVP
- [[19-portfolio-risk]] §6 v2 delivered (#70)
- [[20-position-sizing]] §7.1 PositionSizer Protocol
- [[risk-rule-dsl]] §2.2 precedence
- [[signal-interface]] §3 Signal dataclass (#76)
- `src/portfolio/orchestrator.py` — #70 sync stub (_SyncStrategyOrchestrator)
- `src/portfolio/_async_orchestrator.py` — #78 async class (T2)
