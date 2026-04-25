# Plan — 브로커 어댑터 async 마이그레이션 (#73)

> 작성: 2026-04-25 (RALPLAN consensus, 3 iterations, APPROVE)
> Planner · Architect · Critic 합의본. Step 1 착수 전 이 파일을 1차 출처로 사용한다.

## Acceptance Criteria

- [ ] 브로커 어댑터 async 인터페이스 구현 + 기존 sync 테스트 통과
- [ ] 성능 벤치마크: 동시 심볼 50개 모니터링 시 latency·throughput 비교 (sync vs async)
- [ ] 호출부 마이그레이션 전략 문서화 (`docs/specs/broker-adapter-async.md`)

## 선행 조건

- [ ] #68 (sync 브로커 커넥터) 완료 확인 — merged (PR `994ea11` 이전)
- [ ] 기존 sync broker 단위·컴포넌트 테스트 384개 녹색 (`pytest tests/brokers -q`)

## 구현 계획

### 1. 설계 결정

#### 1.1 배경 및 동기
`src/brokers/base.py` 의 `BrokerAdapter` 는 전부 sync Protocol 이다. `src/brokers/binance/adapter.py`, `src/brokers/kis/adapter.py` 는 내부적으로 `requests.Session` (sync) + `threading.Thread` 로 WS 수신을 돌린다. #73 은 "비동기 I/O 로 전환해 REST throughput 을 높이고 WS 수신·재접속·keepalive 를 단일 asyncio 루프에서 관리" 하는 것이 목표이고, 상위 코드 (`src/portfolio/_async_orchestrator.py` #78) 는 이미 `asyncio` 기반이라 sync adapter 호출 시 `asyncio.to_thread` 래핑 오버헤드가 누적된다.

#### 1.2 Q&A (interview 결론)
- **Q1 병존/대체**: 병존. sync Protocol 은 변경 없이 유지하고 async Protocol 을 **별도 심볼** 로 추가 — backtest / 기존 툴링·테스트를 깨지 않기 위함.
- **Q2 HTTP 클라이언트**: Binance 는 `httpx.AsyncClient`, KIS 는 `httpx.AsyncClient`. `aiohttp` 미선택 — 이유: sync 경로의 `requests` 를 장기적으로 `httpx.Client` 로 수렴시킬 수 있어 요청/응답 객체·타임아웃·프록시 시맨틱을 공유하기 쉬움.
- **Q3 WS 라이브러리**: `websockets>=12` 사용. 자동 reconnect·backoff 는 adapter 레이어에서 구현 (라이브러리 기본 reconnect 는 쓰지 않음 — sequence/listenKey 복구 로직이 필요).
- **Q4 TaskGroup vs gather**: `asyncio.TaskGroup` (Python 3.11+, 본 레포 요구사항 `>=3.11`). fan-out 주문·ping·keepalive 는 전부 TaskGroup 으로 묶어 예외 전파를 명시화한다.
- **Q5 벤치 기준**: Scenario 1~4 (§2 Step 6) 에 **수치 pass/fail 기준** 명기.
  - Scenario 1 (REST throughput): 동시 50 심볼 × 100 place_order → async req/s ≥ sync × 3.0, async p95 latency ≤ sync × 1.1
  - Scenario 2 (fill stream 5분): fill 유실 = 0, mem ≤ sync × 1.2
  - Scenario 3 (WS 1006 storm 5/sec × 60s): fill 중복 = 0, reconnect 복귀 ≤ 10초
  - Scenario 4 (listenKey expiry 강제): fill gap = 0, keepalive task 실패 → main generator 로 `ListenKeyExpiredError` 전파 확인
- **Q6 대상 어댑터**: Binance 우선(fills/orderbook 모두 async), KIS 는 최소 surface (체결·시세 WS + REST place/cancel/query). KIS 내부 국내/해외 주식 분기는 그대로 유지.
- **Q7 스케줄러 통합**: `AsyncStrategyOrchestrator` (#78) 에 `AsyncBrokerAdapter` 주입 경로 추가. sync orchestrator 는 손대지 않는다.

#### 1.3 원칙 (Principles)
1. **병존 우선 (coexistence)** — sync Protocol 을 **삭제하지 않고** async 를 별도 심볼로 추가. 전환은 호출부가 점진적으로 수행.
2. **도메인 타입 재사용** — `OrderRequest`/`OrderAck`/`Position`/`Balance`/`HealthStatus`/`BrokerFill` 등 기존 타입을 async 시그니처에서도 그대로 쓴다. 별칭·새 dataclass 금지.
3. **한 루프 단일 소유** — adapter 내부 모든 async 자원(WS, keepalive task, httpx client)은 동일 `asyncio.AbstractEventLoop` 에서 생성·소비·close.
4. **검증 가능한 전환** — throughput / latency / fill-loss 3개 메트릭을 수치 gate 로 측정 후 머지. 감 (feeling) 으로 판단 금지.
5. **파일 표면 최소화** — async Protocol 은 `base.py` 에, 공통 타입은 `types.py` 에. 별도 `async_protocol.py` / `_shared.py` 생성 금지.

#### 1.4 결정 드라이버 (top 3)
1. 상위 `AsyncStrategyOrchestrator` 와의 자연스러운 통합 (to_thread 래핑 제거).
2. WS reconnect / keepalive 의 단일 루프 관리 (지금은 thread + lock 로 경쟁 조건 여지).
3. 테스트·backtest 호환성 유지 — sync 경로를 건드리면 회귀 폭발.

#### 1.5 Options
- **A. 별도 심볼 병존 (선택)**: `BrokerAdapter` (sync) + `AsyncBrokerAdapter` (async) 를 `base.py` 한 파일에 둔다. 호출부가 명시적으로 선택.
  - Pros: 회귀 폭 0, 타입 체커가 sync/async 오용을 잡음, 호출부 마이그레이션 속도 자유.
  - Cons: 일시적으로 두 구현을 유지 (Binance/KIS sync 어댑터 + async 어댑터).
- **B. 단일 Protocol + sync 래퍼**: Protocol 을 async 로만 정의하고 기존 sync 호출부는 `asyncio.run` 래퍼.
  - Invalidation: backtest 루프 (sync, 수백만 틱) 에 `asyncio.run` 비용이 크고, 일부 호출부는 이미 이벤트 루프 내부에서 호출되어 "cannot be called from a running event loop" 에러가 터진다.
- **C. sync 어댑터를 async 래퍼로 감싸기**: 기존 sync 어댑터를 `to_thread` 로 감싸 async 표면 제공.
  - Invalidation: WS 수신이 여전히 thread 기반이라 "keepalive/reconnect 를 async 루프가 소유" 라는 목표를 달성하지 못함. REST throughput 은 개선되지 않고 thread pool 상한에 묶인다.

#### 1.6 복잡도 / 리스크 추정
- 복잡도: MEDIUM (기존 타입 재사용 + 병존으로 surface 제한).
- 주요 리스크: WS reconnect 경합, KIS auth lazy refresh 의 concurrent 경쟁, Windows + asyncio selector 차이.

---

### 2. Task Flow (Step 1 ~ Step 8)

#### Step 1 — `AsyncBrokerAdapter` Protocol 을 `src/brokers/base.py` 에 추가
**파일**: `src/brokers/base.py` (edit only — sync Protocol 바로 아래 추가, sync 는 변경 금지)

구조:
```python
# --- sync Protocol ---
@runtime_checkable
class BrokerAdapter(Protocol):
    ...  # 기존 그대로 유지

# --- async Protocol ---
@runtime_checkable
class AsyncBrokerAdapter(Protocol):
    name: str
    paper: bool
    async def place_order(self, req: OrderRequest) -> OrderAck: ...
    async def cancel_order(self, *, broker_order_id: str | None = None,
                            client_order_id: str | None = None, symbol: str) -> None: ...
    async def get_order(self, *, broker_order_id: str | None = None,
                        client_order_id: str | None = None, symbol: str) -> OrderAck: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def get_balance(self) -> list[Balance]: ...
    def stream_fills(self) -> AsyncIterator[BrokerFill]: ...
    async def ensure_leverage(self, symbol: str, leverage: int) -> None: ...
    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None: ...
    async def ensure_position_mode(self, *, hedge: bool) -> None: ...
    async def health_check(self) -> HealthStatus: ...
    async def aclose(self) -> None: ...
```
- 메서드 인자/반환형이 sync `BrokerAdapter` 와 **완전 대칭**. 유일한 차이는 `async def` 와 `stream_fills` 의 반환형 (`Callable on_fill` 콜백 → `AsyncIterator[BrokerFill]`).
- `OrderRequest`, `OrderAck`, `Position`, `Balance`, `HealthStatus`, `BrokerFill`, `PositionSide`, `MarginType`, `OrderType` 는 모두 `src/brokers/base.py:36-76` + `src/brokers/types.py` 에 있는 **기존 심볼을 그대로 재사용** (신규 도메인 dataclass 금지).
- 별도 파일 (`async_protocol.py` 등) 생성 금지.

**Acceptance**:
- `python -c "from src.brokers.base import AsyncBrokerAdapter, BrokerAdapter; assert AsyncBrokerAdapter is not BrokerAdapter"` 성공.
- `mypy --strict src/brokers/base.py` 통과.
- `grep -r "class AsyncBrokerAdapter" src/brokers/` → `base.py` 1건만.

#### Step 2 — Async rate limiter / backoff / error taxonomy
**파일**:
- `src/brokers/async_rate_limiter.py` (신규, **async 전용**)
- `src/brokers/rate_limiter.py` (기존 sync, **수정 금지**)
- `src/brokers/async_backoff.py` (신규, `exponential_backoff(attempt, base, cap, jitter)` async 제너레이터)
- `src/brokers/errors.py` (edit: `BrokerClosedError`, `ListenKeyExpiredError`, `WSDisconnectedError` 추가)

**정책 (한 파일 혼재 절대 금지)**:
- `src/brokers/rate_limiter.py` — **sync, "초과 시 즉시 `RateLimitError` raise"** 시맨틱. 파일 상단 docstring 에 명시. 기존 호출부 동작 보존.
- `src/brokers/async_rate_limiter.py` — **async `AsyncTokenBucket`, "초과 시 `await` 으로 token 대기 후 acquire"** 시맨틱. 파일 상단 docstring 에 명시. 동시성 제어는 `asyncio.Lock` (기본 FIFO fairness). `asyncio.Semaphore` 는 토큰 비용 가변(weight=1/5/10) 때문에 부적합하여 기각.
- 두 시맨틱 차이를 `src/brokers/.ai.md` 에 "sync RateLimiter 와 AsyncTokenBucket 을 한 파일에 혼재 절대 금지" 로 명문화.
- 헤더 피드백(`on_response_headers`) 은 sync 시그니처 유지 — httpx response 훅에서 즉시 호출 가능.

**Acceptance**:
- `tests/brokers/test_async_rate_limiter.py`: 동시 200 coroutine 이 10 rps 버킷에 대해 **wait 로 직렬화** 되며, 초당 ≥9, ≤11 req/s 유지.
- `tests/brokers/test_async_backoff.py`: 최대 5회, cap 10s, jitter ± 20% 준수.

#### Step 3 — Binance async adapter
**파일**:
- `src/brokers/binance/async_adapter.py` (신규) — `AsyncBrokerAdapter` 구현.
- `src/brokers/binance/async_http.py` (신규) — `httpx.AsyncClient` signed/unsigned REST, timestamp, recvWindow, `AsyncTokenBucket` 통합.
- `src/brokers/binance/async_ws.py` (신규) — `websockets` 기반 user data stream + market stream.
- `src/brokers/binance/listen_key.py` (신규) — listenKey 발급 / keepalive / 삭제 helper.

핵심 구현 포인트:
- REST 에서 `ensure_leverage` / `ensure_margin_type` / `ensure_position_mode` 는 sync 어댑터와 동일한 "이미 설정되어 있으면 무시" 시맨틱.
- `stream_fills()` 는 **AsyncIterator**. 내부에서 `asyncio.TaskGroup` 으로:
  - user data WS 읽기 task
  - listenKey keepalive task (30분 주기, `httpx.AsyncClient.put`)
  - reconnect controller (`async_backoff`)
- 내부 `asyncio.Queue(maxsize=env BROKER_FILL_QUEUE_SIZE)` + overflow 정책 `{block, drop_oldest, raise}` (기본 `block`). 메트릭 `broker_fill_queue_overflow_total` 를 `src/observability/metrics.py` 에 등록.
- `place_order` 내부에서 `AsyncTokenBucket.acquire()` 를 `await` 후 REST 호출. `KillSwitch.assert_allow_order()` 게이트 진입 직후 수행 (불변식).
- 선택적 `subscribe_fills(on_fill, *, queue_size=None)` wrapper 제공 — 콜백형 기존 코드 호환.
- 모든 외부 자원은 `aclose()` 에서 한 번만 정리.

**Acceptance**:
- 단위 테스트 (respx mock) 8건: place / cancel / get_order / get_positions / get_balance / health_check / ensure_leverage / ensure_margin_type 각 성공·실패 분기.
- WS 테스트 4건 (fake ws server): 정상 fill 수신 / 1006 disconnect reconnect / listenKey expiry → `ListenKeyExpiredError` 전파 / `aclose` 시 graceful close.

#### Step 4 — KIS async adapter
**파일**:
- `src/brokers/kis/async_adapter.py` (신규)
- `src/brokers/kis/async_http.py` (신규)
- `src/brokers/kis/async_ws.py` (신규)
- `src/brokers/kis/auth.py` (edit: `asyncio.Lock` 으로 concurrent refresh 경합만 차단)

구현 포인트:
- 국내/해외 구분 (endpoint suffix) 은 sync adapter 와 동일 전략 (route-by-symbol).
- `stream_fills()` 는 KIS 체결 통보 WS 구독 (`H0STCNI0` 류) 파싱 후 `BrokerFill` 으로 변환.
- `approval_key` 는 sync 경로 `kis/ws.py:72-91` 패턴을 그대로 유지 — **WS 구독 직전 1회 발급**.

**Acceptance**:
- 단위 테스트 (respx) 6건: 국내 place / 해외 place / cancel / get_positions / get_balance / health_check.
- WS 테스트 2건: 체결 통보 파싱 / reconnect + 구독 복원.

#### Step 5 — KIS auth lazy refresh 유지 & aclose 공통 절차
**방침**:
> KIS auth 는 현행 lazy refresh 구조를 유지한다. `src/brokers/kis/auth.py:63-70` 의 `get_token()` 이 호출 시점에 `_should_renew()` 로 만료 검사 + 재발급. async 전환 시 `asyncio.Lock` 으로 concurrent refresh 경합만 차단한다 (**background task 도입 금지**). `approval_key` 는 현행처럼 WS 구독 직전 1회 발급 (`kis/ws.py:72-91` 패턴 유지).

**aclose 절차 (Binance/KIS 공통, 5단계)**:
1. 신규 주문 수용 중지 (`closing=True` → `place_order` 호출 시 `BrokerClosedError`).
2. WS 연결 close frame 송신 + `await ws.wait_closed()`.
3. Binance 전용: listenKey keepalive `asyncio.Task` cancel + await (**KIS 는 해당 없음 — 명시**).
4. inflight REST 의 `CancelledError` 전파 완료 대기 (`asyncio.gather(*inflight, return_exceptions=True)`).
5. `httpx.AsyncClient.aclose()` 호출.

**Acceptance (Binance 한정 3건 + KIS step3 no-op 확인 1건)**:
- `aclose` 중 새 `place_order` 는 `BrokerClosedError`.
- `aclose` 중 inflight REST 가 `CancelledError` 전파 후 종료.
- `aclose` 이후 listenKey keepalive task 가 cancelled 상태 확인.
- KIS adapter 의 aclose 가 step 3 를 건너뛰고 step 2→4→5 순서로 종료하는지 확인.

#### Step 6 — Async 경로 벤치마크 (sync baseline 포함)
**파일**:
- `tests/performance/broker_sync_baseline.py` (신규) — 현행 sync adapter 를 `ThreadPoolExecutor(max_workers=50)` 에 태워 baseline 수치 수집.
- `tests/performance/broker_async_bench.py` (신규) — 4 시나리오, 같은 mock 서버·같은 fixture 로 1:1 비교.
- `tests/performance/conftest.py` (신규) — mock REST (`aiohttp.web`) + mock WS (`websockets.serve`) fixture (`scope="session"`).
- `scripts/bench_async_broker.py` (신규, 선택) — 로컬 개발용 stdout JSON runner.
- `docs/work/active/000073-broker-async/02_bench.md` (신규) — baseline 표 선행 commit → async 비교표 append.

**시나리오 및 pass/fail 수치**:
- Scenario 1 (REST throughput): 동시 50 심볼 × 100 place_order → async req/s ≥ **sync × 3.0**, async p95 latency ≤ **sync × 1.1**.
- Scenario 2 (fill stream 5분): **fill 유실 = 0**, mem ≤ sync × 1.2.
- Scenario 3 (WS 1006 storm 5/sec × 60s): **fill 중복 = 0**, **reconnect 복귀 ≤ 10초** + reconciler dedup key `(broker_order_id, trade_id)` 순서 보존.
- Scenario 4 (listenKey expiry 강제): **fill gap = 0**, keepalive task 실패 → main generator 로 `ListenKeyExpiredError` 전파 확인.

**Acceptance**:
- `pytest tests/performance/broker_sync_baseline.py -m slow --benchmark-json=docs/work/active/000073-broker-async/bench_sync.json`
- `pytest tests/performance/broker_async_bench.py -m slow --benchmark-json=docs/work/active/000073-broker-async/bench_async.json`
- CI smoke: 1/10 규모로 ≤ 60s 안에 완료하고 임계 비례 조정값 충족.

#### Step 7 — Orchestrator 통합 & 전환 경계 테스트
**파일**:
- `src/portfolio/_async_orchestrator.py` (edit): `broker: AsyncBrokerAdapter` 주입 경로 추가 (기존 sync 주입 경로는 유지, 둘 중 하나 선택).
- `tests/portfolio/test_async_orchestrator_with_async_broker.py` (신규).
- `tests/integration/test_ws_close_storm.py` (신규, `@pytest.mark.integration`) — Scenario 3 실증.
- `tests/integration/test_listenkey_expiry.py` (신규) — Scenario 4 실증.

**Acceptance**:
- orchestrator 가 `place_order` → fill 수신 → 포지션 업데이트까지 end-to-end async 로 수행.
- sync orchestrator 테스트 384건 그대로 녹색.

#### Step 8 — 회귀 게이트 (머지 AND 6개)
1. sync 테스트 suite 녹색 (기존 384 + 신규 boundary 3 = **387+**).
2. async p95 REST latency ≤ sync × 1.1.
3. async throughput ≥ **sync × 3.0**.
4. coverage `src.brokers` ≥ **85%**.
5. `python scripts/check_invariants.py --strict` 통과.
6. `docs/specs/broker-adapter-async.md` 리뷰 체크리스트 6항목이 PR 본문에 복사되어 수동 승인 게이트 통과.

---

### 3. AC ↔ Step 매핑

| AC | 내용 | 커버 Step |
|---|---|---|
| AC1 | `AsyncBrokerAdapter` Protocol 이 sync 와 병존 (기존 타입 재사용) | Step 1 |
| AC2 | async rate limiter 가 "대기 후 acquire" 시맨틱으로 동작, sync 는 "즉시 raise" 유지 | Step 2 |
| AC3 | `docs/specs/broker-adapter-async.md` 리뷰 체크리스트 6항목 PR 본문 포함 (수동 승인 gate) | Step 8 + §8 |
| AC4 | Binance async 어댑터가 place/cancel/get/positions/balance/health/ensure_* 전 메서드 동작 | Step 3 |
| AC5 | Binance `stream_fills` 가 1006 disconnect 후 ≤10s 내 복귀, 중복 0 | Step 3 + Step 6 Scenario 3 |
| AC6 | listenKey expiry 시 keepalive task 실패 → main generator 로 `ListenKeyExpiredError` 전파 | Step 3 + Step 6 Scenario 4 |
| AC7 | KIS async 어댑터 REST 6메서드 + WS 체결 통보 파싱 + 구독 복원 | Step 4 |
| AC8 | KIS auth lazy refresh 유지, concurrent 경합은 `asyncio.Lock` 만으로 차단 (background task 금지) | Step 5 |
| AC9 | `aclose` 5단계가 새 주문 차단 / WS close / keepalive cancel (Binance) / inflight CancelledError / httpx aclose 순서 보장 | Step 5 |
| AC10 | 회귀 게이트 6개 AND 통과 | Step 8 |

---

### 4. 영향 파일 목록

**신규**:
- `src/brokers/async_rate_limiter.py`
- `src/brokers/async_backoff.py`
- `src/brokers/binance/async_adapter.py`
- `src/brokers/binance/async_http.py`
- `src/brokers/binance/async_ws.py`
- `src/brokers/binance/listen_key.py`
- `src/brokers/kis/async_adapter.py`
- `src/brokers/kis/async_http.py`
- `src/brokers/kis/async_ws.py`
- `tests/performance/__init__.py`
- `tests/performance/conftest.py`
- `tests/performance/broker_sync_baseline.py`
- `tests/performance/broker_async_bench.py`
- `scripts/bench_async_broker.py` (선택)
- `docs/specs/broker-adapter-async.md`
- `docs/work/active/000073-broker-async/02_bench.md`
- `docs/work/active/000073-broker-async/03_migration.md`
- `docs/work/active/000073-broker-async/04_verification.md`
- 테스트: `tests/brokers/test_async_rate_limiter.py`, `tests/brokers/test_async_backoff.py`, `tests/brokers/test_binance_async_adapter.py`, `tests/brokers/test_binance_async_ws.py`, `tests/brokers/test_kis_async_adapter.py`, `tests/brokers/test_kis_async_ws.py`, `tests/brokers/test_async_aclose.py`, `tests/brokers/test_protocol_boundary.py`, `tests/integration/test_ws_close_storm.py`, `tests/integration/test_listenkey_expiry.py`, `tests/portfolio/test_async_orchestrator_with_async_broker.py`

**수정**:
- `src/brokers/base.py` — `AsyncBrokerAdapter` 추가 (sync 변경 금지, 섹션 주석으로 구분)
- `src/brokers/errors.py` — 신규 예외 3종 (`BrokerClosedError`, `ListenKeyExpiredError`, `WSDisconnectedError`)
- `src/brokers/kis/auth.py` — `asyncio.Lock` 추가 (lazy refresh 구조 유지)
- `src/brokers/.ai.md` — sync/async rate limiter 분리 원칙 + async 레이어 섹션 명문화
- `src/brokers/binance/.ai.md` — async_adapter/async_http/async_ws/listen_key 역할 기술
- `src/brokers/kis/.ai.md` — async_adapter/async_http/async_ws 역할 + lazy refresh 정책 명시
- `src/observability/metrics.py` — `broker_fill_queue_overflow_total`, `broker_ws_reconnect_total`, `broker_keepalive_failure_total`, `broker_request_latency_seconds` Counter/Histogram 등록
- `src/portfolio/_async_orchestrator.py` — AsyncBrokerAdapter 주입 경로 추가
- `pyproject.toml` — `httpx>=0.27`, `websockets>=12` prod deps + `pytest-asyncio>=0.23`, `respx>=0.21` dev deps + `[tool.pytest.ini_options] asyncio_mode = "auto"`
- `tests/conftest.py` — OS 분기 event loop policy (Windows Selector 정책)
- `.github/workflows/ci.yml` — `runs-on: [windows-latest, ubuntu-latest]` matrix 추가
- `docs/work/active/000073-broker-async/00_issue.md` — 작업 내역 누적

**별도 파일 생성 금지** (Must NOT):
- `src/brokers/async_protocol.py` — 생성 금지. `AsyncBrokerAdapter` 는 `base.py` 에 통합.
- `src/brokers/_shared.py` — 생성 금지. 공통 타입은 `base.py` + `types.py` 2파일 원칙 유지.

---

### 5. 가드레일 (Must Have / Must NOT)

**Must Have**:
- `AsyncBrokerAdapter` 시그니처는 sync `BrokerAdapter` 와 완전 대칭 (인자명·반환형·예외 모두 동일 타입 재사용).
- `OrderRequest` / `OrderAck` / `Position` / `Balance` / `HealthStatus` / `BrokerFill` / `PositionSide` / `MarginType` / `OrderType` 는 기존 심볼을 그대로 재사용.
- async 자원은 모두 단일 event loop 에서 생성·close. cross-loop 공유 금지.
- `aclose()` 는 idempotent — 두 번 호출해도 예외 없음.
- `place_order` 진입 직후 `KillSwitch.assert_allow_order()` 게이트 (sync/async 공통 불변식).
- `httpx.AsyncClient(trust_env=False)` 명시 설정.
- PR 머지 시 §2 Step 8 회귀 게이트 6개 AND 전부 통과.
- `.ai.md` 최신화 (CLAUDE.md 규칙).

**Must NOT**:
- sync `BrokerAdapter` Protocol 및 sync 어댑터 파일 수정 (kis/auth.py 의 Lock 추가는 예외).
- `BrokerAdapter` 를 async 로 재정의.
- async adapter 내부에서 `time.sleep` / `requests` / `websocket-client` / `threading.Lock` / `httpx.Client` (sync) 호출.
- 한 파일에 sync `RateLimiter` 와 `AsyncTokenBucket` 을 혼재 배치 (**절대 금지**).
- `src/brokers/async_protocol.py` 또는 `src/brokers/_shared.py` 생성.
- KIS auth 에 OAuth token refresh background task / approval_key keepalive task 도입.
- async 함수 내 `asyncio.run()` 재호출 (이벤트 루프 중첩).
- 동일 파일에 `httpx.Client` (sync) + `httpx.AsyncClient` 혼용.
- async adapter 가 sync `BrokerAdapter` Protocol 상속 / 구현 (runtime_checkable 구조 매칭 false-positive 위험).
- `AsyncOrderRouter` 연동 (본 이슈 out-of-scope, Follow-up F2).
- `uvloop` 의존 (POSIX-only, Windows 호환 깨짐).
- LLM 에 주문 실행·리스크 결정 위임 (프로젝트 불변식).
- 자동 커밋 (드래프트 포함).

---

### 6. 핵심 인터페이스 요약

```python
# src/brokers/base.py  (sync 는 그대로, async 를 아래에 추가)

# --- sync Protocol ---
@runtime_checkable
class BrokerAdapter(Protocol):
    ...  # 기존 그대로

# --- async Protocol ---
@runtime_checkable
class AsyncBrokerAdapter(Protocol):
    name: str
    paper: bool
    async def place_order(self, req: OrderRequest) -> OrderAck: ...
    async def cancel_order(self, *, broker_order_id: str | None = None,
                            client_order_id: str | None = None, symbol: str) -> None: ...
    async def get_order(self, *, broker_order_id: str | None = None,
                        client_order_id: str | None = None, symbol: str) -> OrderAck: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def get_balance(self) -> list[Balance]: ...
    def stream_fills(self) -> AsyncIterator[BrokerFill]: ...
    async def ensure_leverage(self, symbol: str, leverage: int) -> None: ...
    async def ensure_margin_type(self, symbol: str, mode: MarginType) -> None: ...
    async def ensure_position_mode(self, *, hedge: bool) -> None: ...
    async def health_check(self) -> HealthStatus: ...
    async def aclose(self) -> None: ...
```

**Protocol boundary 회귀 방지 테스트** (`tests/brokers/test_protocol_boundary.py`):
```python
def test_async_adapter_is_not_sync_broker_adapter(async_adapter):
    assert not isinstance(async_adapter, BrokerAdapter)  # 구조 매칭 false-positive 차단
def test_sync_adapter_conforms(sync_adapter):
    assert isinstance(sync_adapter, BrokerAdapter)
def test_async_adapter_conforms(async_adapter):
    assert isinstance(async_adapter, AsyncBrokerAdapter)
```

---

### 7. 리스크 레지스터

| # | 리스크 | 영향 | 완화 |
|---|---|---|---|
| R1 | 단일 이벤트 루프 가정 위반 (cross-loop 자원 공유) | WS 멈춤 / RuntimeError | adapter 가 `self._loop = asyncio.get_running_loop()` 캐시, 다른 루프 호출 시 raise |
| R2 | Binance listenKey expiry 무감지 | fill 유실 | keepalive task + expiry 감지 시 `ListenKeyExpiredError` 전파 (Scenario 4 로 검증) |
| R3 | WS 1006 폭풍 시 backoff 과도 | fill 회복 지연 | exp backoff (base 1s, cap 10s, jitter ±20%), 복귀 시간 ≤10s gate |
| R4 | KIS lazy refresh concurrent 경합 | 토큰 2중 발급 / 429 | `asyncio.Lock` 로 refresh critical section 직렬화 |
| R5 | aclose 순서 오류로 listenKey leak | 다음 세션 오염 | 5단계 순서 테스트 (Step 5) |
| R6 | sync 경로 회귀 | 기존 384 테스트 깨짐 | sync 파일 수정 최소화 + Step 8 게이트 1 |
| R7 | async throughput 기대 미달 | 머지 보류 | Scenario 1 수치 gate + smoke CI |
| R8 | `httpx.AsyncClient` 풀 소진 | 요청 지연 | `httpx.Limits(max_connections=100, max_keepalive_connections=20)` 명시 |
| R9 | 테스트용 fake WS flakiness | CI 깜빡임 | respx + `pytest-asyncio` strict mode, timeout 고정 |
| R10 | 기존 타입 시그니처 오용 (`Position` 대신 dict 반환 등) | mypy 통과 + 런타임 버그 | Protocol `runtime_checkable` + mypy strict + Protocol boundary 3-line 테스트 |
| R11 | `httpx` `trust_env` 가 CI runner 프록시 상속 | 테스트 간헐 실패 | adapter 는 `trust_env=False` 고정 + proxy 기본 검증 테스트 |
| R12 | Windows 에서 `asyncio` selector vs proactor 차이 (본 워크트리 Windows 11) | WS 종료 시 hang | `tests/conftest.py` 에서 `WindowsSelectorEventLoopPolicy` 고정 + CI Windows matrix |
| R13 | uvloop 혼입 시 플랫폼 불일치 | Windows 미지원 | uvloop 미도입, 기본 asyncio 만 사용, 벤치 주석 "no uvloop" 태깅 |

---

### 8. Acceptance 검증 방식

- **AC1**: `python -c "from src.brokers.base import AsyncBrokerAdapter, BrokerAdapter; assert AsyncBrokerAdapter is not BrokerAdapter"` + `mypy --strict src/brokers/base.py`.
- **AC2**: `pytest tests/brokers/test_async_rate_limiter.py tests/brokers/test_rate_limiter.py -q` (두 시맨틱 분리 검증).
- **AC3** — `docs/specs/broker-adapter-async.md` 리뷰 체크리스트 6항목 (PR 템플릿에 복사되어 머지 gate):
  1. 병존 기간 정의 (예: 2 minor 릴리즈)
  2. 호출부 전환 순서 (`src/portfolio/` → 향후 execution/ops/backtest)
  3. deprecation 타임라인 (sync BrokerAdapter)
  4. 롤백 절차 (async 문제 발생 시 sync adapter 로 복귀 방법)
  5. sync/async 혼용 시 타입 캐스팅 규칙 (같은 타입 재사용이므로 원칙적으로 불필요하나 명시)
  6. CI 게이트 변경 이력 (Windows matrix 추가 등)
- **AC4**: `pytest tests/brokers/test_binance_async_adapter.py -q`.
- **AC5/AC6**: `pytest tests/brokers/test_binance_async_ws.py -q` + `pytest tests/integration/test_ws_close_storm.py tests/integration/test_listenkey_expiry.py -m integration -v`.
- **AC7**: `pytest tests/brokers/test_kis_async_adapter.py tests/brokers/test_kis_async_ws.py -q`.
- **AC8**: `pytest tests/brokers/test_kis_auth_async.py -q` (concurrent 2 coroutine 이 동일 토큰 공유, 1회만 refresh).
- **AC9**: `pytest tests/brokers/test_async_aclose.py -q` (5단계 순서 검증).
- **AC10**: CI 워크플로에서 6개 gate AND 조건으로 merge block.

**회귀 게이트 명령 (Step 8)**:
```
pytest tests/ -m "not slow and not integration" --cov=src.brokers --cov-fail-under=85
pytest tests/performance/broker_sync_baseline.py tests/performance/broker_async_bench.py -m slow --benchmark-json=docs/work/active/000073-broker-async/bench.json
pytest tests/integration -m integration
python scripts/check_invariants.py --strict
ruff check src/brokers tests/brokers && mypy src/brokers
```

---

### 9. 커밋 계획 (C1 ~ C9)

| # | 범위 | 내용 |
|---|---|---|
| C1 | protocol | `src/brokers/base.py` 에 `AsyncBrokerAdapter` 추가 (sync 변경 없음, 기존 타입 재사용) + `errors.py` 신규 예외 + `test_protocol_boundary.py` |
| C2 | primitives | `async_rate_limiter.py` + `async_backoff.py` + `src/brokers/.ai.md` 분리 원칙 명문화 + 단위 테스트 |
| C3 | binance-rest | `binance/async_http.py` + `binance/async_adapter.py` REST 부분 + 단위 테스트 |
| C4 | binance-ws | `binance/async_ws.py` + `binance/listen_key.py` + WS 테스트 4건 + fill 큐/overflow 메트릭 |
| C5 | kis-rest | `kis/async_http.py` + `kis/async_adapter.py` REST 6메서드 + 단위 테스트 |
| C6a | kis-ws | `kis/async_ws.py` + WS 테스트 2건 |
| C6b | kis-auth | `kis/auth.py` `asyncio.Lock` 도입 + 단위 테스트 |
| C7 | aclose | Binance/KIS `aclose` 5단계 통합 + Binance 한정 3건 테스트 + KIS step3 no-op 테스트 |
| C8 | bench+spec | `tests/performance/{broker_sync_baseline,broker_async_bench}.py` + `docs/specs/broker-adapter-async.md` (AC3 6항목 포함) + `02_bench.md` baseline→async 비교표 |
| C9 | integrate+ci | `src/portfolio/_async_orchestrator.py` async broker 주입 + `tests/integration/{test_ws_close_storm,test_listenkey_expiry}.py` + `pyproject.toml`/`requirements*.txt` 의존성 고정 + `.github/workflows/ci.yml` Windows matrix + `tests/conftest.py` event loop policy |

각 커밋은 자체로 green (`pytest -q` 통과). 커밋 전 사용자 승인 필수 (CLAUDE.md 행동 규칙).

---

### ADR (Architecture Decision Record)

- **Decision**: `AsyncBrokerAdapter` Protocol 을 기존 sync `BrokerAdapter` 와 **병존** 시켜 `src/brokers/base.py` 한 파일에 추가한다. Binance/KIS async 어댑터는 기존 도메인 타입 (`OrderRequest` / `OrderAck` / `Position` / `Balance` / `HealthStatus` / `BrokerFill` 등) 을 그대로 재사용한다.
- **Drivers**: (1) 상위 `AsyncStrategyOrchestrator` (#78) 와의 네이티브 async 통합으로 `to_thread` 오버헤드 제거, (2) WS reconnect / keepalive 를 단일 event loop 가 소유, (3) backtest 및 기존 384 sync 테스트 회귀 방지.
- **Alternatives considered**:
  - B. Protocol 을 async-only 로 교체 + sync 호출부는 `asyncio.run` 래퍼 — backtest 성능·이벤트 루프 중첩 이슈로 기각.
  - C. 기존 sync 어댑터를 `to_thread` 로 래핑한 async 표면만 제공 — WS 루프 소유권 목표 미달, throughput 개선도 제한적이어서 기각.
- **Why chosen**: (A) 가 회귀 폭 0, 마이그레이션 속도 자유, 타입 재사용으로 변환 레이어 불필요. 벤치마크에서 async ×3 throughput 을 강제해 실효를 검증 가능.
- **Consequences**: (+) 호출부가 점진적으로 async 로 옮길 수 있음. (-) 과도 기간 동안 Binance/KIS 에 sync/async 두 구현 병존. (+) 테스트 병렬 실행 친화적.
- **Follow-ups**:
  - (F1) sync `BrokerAdapter` deprecation 일정은 `docs/specs/broker-adapter-async.md` 체크리스트 #3 에 따라 별도 결정.
  - (F2) `AsyncOrderRouter` 신규 이슈 생성 (본 이슈 머지 후). execution/ops 레이어 async 전환은 후속.
  - (F3) uvloop 도입 벤치 (별도 이슈, POSIX 환경 한정).
  - (F4) `httpx` HTTP/2 도입 검토 (Binance/KIS 서버 지원 확인 후).

---

### RALPLAN-DR 요약

- **Mode**: SHORT (3 iterations 소요, 최종 APPROVE).
- **Principles (5)**: (1) 병존 우선, (2) 도메인 타입 재사용, (3) 한 루프 단일 소유, (4) 검증 가능한 전환, (5) 파일 표면 최소화.
- **Decision Drivers (top 3)**: (1) `AsyncStrategyOrchestrator` 네이티브 통합, (2) WS reconnect / keepalive 의 단일 루프 소유, (3) 기존 sync 테스트·backtest 호환성.
- **Options**:
  - A. 별도 심볼 병존 — **선택**. 회귀 0, 타입 체커가 오용 검출, 호출부 마이그레이션 자유.
  - B. async-only Protocol + sync 래퍼 — 기각. backtest 성능 + 이벤트 루프 중첩.
  - C. sync 어댑터를 async 래퍼로 감싸기 — 기각. WS 루프 소유권 미달성 + throughput 개선 미흡.
- **Open invalidation**: 해당 없음 — 최종 옵션 (A) 외 2개 모두 명시적 기각 사유 존재.

---

### Consensus 기록

- **Iteration 1**: Planner 초안 → Critic ITERATE (8 changes: sync baseline, protocol boundary test, stream_fills 이중 표면, aclose 순서, 리스크 R11-R13, Must NOT 확장, AsyncTokenBucket 구체화, 벤치 시나리오 확장).
- **Iteration 2**: Planner revision #2 → Architect amendments 11 건 PASS, 단 revision 과정에서 N1 Blocker (새 타입 이름) + N2/N3/N4 Major + N5-N8 Minor 신규 결함 발견 → Critic ITERATE.
- **Iteration 3**: Planner revision #3 (타입 재사용 + async_protocol.py/_shared.py 폐기 + KIS lazy refresh 유지 + aclose 5단계 + rate_limiter 2파일 분리 + 벤치 수치 기준 + AC3 6항목) → Architect APPROVE → **Critic APPROVE**.
