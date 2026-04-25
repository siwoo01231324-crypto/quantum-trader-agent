---
id: broker-adapter-async
type: spec-architecture
title: Async Broker Adapter — 마이그레이션 명세
name: Async Broker Adapter 마이그레이션 명세
owner: siwoo
status: active
issue: "#73"
---

# Async Broker Adapter — 마이그레이션 명세

## 개요

이슈 #73 에서 확정된 Option A ("병존 Protocol") 를 기준으로, `AsyncBrokerAdapter` Protocol 과 두 구체 구현체(Binance, KIS)를 설명한다. 기존 sync `BrokerAdapter` 는 그대로 유지되며, async 구현은 별도로 추가된다.

## 아키텍처 결정 요약

| 결정 | 내용 |
|------|------|
| Protocol 배치 | `AsyncBrokerAdapter` → `src/brokers/base.py` (sync `BrokerAdapter` 바로 아래) |
| HTTP 클라이언트 | `httpx.AsyncClient(trust_env=False)` |
| WebSocket | `websockets>=12` |
| 동시성 | `asyncio.TaskGroup` (Python 3.11+) |
| Rate Limiter | `src/brokers/async_rate_limiter.py` — wait-after-acquire 시맨틱 |
| Retry | `src/brokers/async_backoff.py` — exponential backoff |
| KIS Auth | lazy refresh + `asyncio.Lock` (background task 금지) |
| Fill stream | `AsyncIterator[BrokerFill]` + `subscribe_fills()` wrapper |

## AC3 체크리스트 (PR 게이트 — 6항목 모두 수동 확인 필수)

> 이 체크리스트는 플랜 §8 AC3 정의("호출부 마이그레이션 전략 문서화")에 따른 수동 승인 게이트다.
> PR 머지 전에 리뷰어가 직접 체크해야 한다.

- [ ] **AC3-1 병존 기간 정의**: sync `BrokerAdapter` 와 async `AsyncBrokerAdapter` 는 **2 minor 릴리즈 동안 병존**한다. 현재 v0.1.x 기준으로 v0.3.0 이전까지 두 Protocol 을 동시 유지한다. 호출부는 이 기간 내에 점진적으로 전환한다.
- [ ] **AC3-2 호출부 전환 순서**: 전환 우선순위는 다음 순서를 따른다: ① `src/portfolio/` (AsyncStrategyOrchestrator — 본 이슈 #73 에서 `AsyncBrokerAdapter` 주입 경로 추가 완료) → ② `src/execution/` (BrokerExecutor 비동기화 — Follow-up F2: AsyncOrderRouter #80) → ③ `src/ops/` (운영 도구) → ④ `src/backtest/` (backtest 루프는 sync 유지 — asyncio.run 오버헤드 이슈로 마지막 전환).
- [ ] **AC3-3 deprecation 타임라인**: sync `BrokerAdapter` 는 v0.3.0 에서 `@deprecated` 마킹되고, v0.5.0 에서 완전 제거된다. 제거 전 최소 1 minor 릴리즈 이상 deprecation 경고를 유지한다. 제거 이슈는 별도 backlog 등록 필요.
- [ ] **AC3-4 롤백 절차**: async 어댑터 운영 중 문제 발생 시 sync 어댑터로 복귀하는 절차: ① `AsyncStrategyOrchestrator` 생성자에서 `broker=None` (기본값) 으로 되돌리거나 `broker=SyncBrokerAdapter(...)` 로 교체 → ② `BROKER_ADAPTER_MODE=sync` 환경변수로 팩토리 분기 (구현 시 추가) → ③ `aclose()` 호출 후 기존 sync adapter 의 `close()` 전환. sync 어댑터 코드는 본 이슈에서 **수정하지 않았으므로** 즉시 복귀 가능.
- [ ] **AC3-5 sync/async 혼용 시 타입 캐스팅 규칙**: `OrderRequest`, `OrderAck`, `Position`, `Balance`, `HealthStatus`, `BrokerFill`, `MarginType`, `PositionSide`, `OrderType` 는 sync/async 공통 타입 — 캐스팅 불필요. 예외 상황: sync adapter 반환 `OrderAck` 를 async 호출부에서 직접 소비하는 경우 타입 자체는 동일하나 `await` 필요 없음 (동기 반환값이므로). mypy `--strict` 로 오용 자동 검출.
- [ ] **AC3-6 CI 게이트 변경 이력**: 본 이슈 #73 에서 추가된 CI 변경사항: ① `.github/workflows/ci.yml` 에 `runs-on: [ubuntu-latest, windows-latest]` matrix 추가 (Windows asyncio selector 호환성 검증) ② `tests/conftest.py` 에 `asyncio.WindowsSelectorEventLoopPolicy` 분기 ③ `pyproject.toml` 에 `asyncio_mode = "auto"` 추가 ④ prod deps: `httpx>=0.27`, `websockets>=12` ⑤ dev deps: `pytest-asyncio>=0.23`, `respx>=0.21`.

## 기술 구현 검증 체크리스트

> 아래는 CI 자동 검증 항목이다 (수동 확인 불필요).

- [ ] Protocol 경계: `tests/brokers/test_protocol_boundary.py` — `AsyncBrokerAdapter is not BrokerAdapter`, instanceof 분리 확인
- [ ] aclose 5단계 계약: `tests/brokers/test_async_aclose.py` — 14개 테스트 (Binance 9 + KIS 5)
- [ ] Fill 무결성: `tests/brokers/test_binance_async_ws.py` — dedup + overflow 3 policies 검증
- [ ] 성능 게이트: `tests/performance/broker_async_bench.py` — 4 시나리오, PASS 결과 `02_bench.md` 참조
- [ ] KIS auth 동시성: `tests/brokers/kis/test_kis_auth_async.py` — concurrent refresh 1회 보장
- [ ] Windows 호환: CI `windows-latest` matrix + `WindowsSelectorEventLoopPolicy` fixture

## 파일 구조

```
src/brokers/
├── base.py                    # BrokerAdapter (sync) + AsyncBrokerAdapter (async) Protocol
├── types.py                   # BrokerFill, PositionSide, MarginType, OrderType
├── errors.py                  # BrokerClosedError, ListenKeyExpiredError, WSDisconnectedError
├── rate_limiter.py            # sync RateLimiter — 즉시 raise 시맨틱
├── async_rate_limiter.py      # AsyncTokenBucket + AsyncBinanceRateLimiter — wait 시맨틱
├── async_backoff.py           # exponential_backoff(), backoff_sequence()
├── binance/
│   ├── adapter.py             # BinanceFuturesAdapter (sync, 유지)
│   ├── async_adapter.py       # AsyncBinanceFuturesAdapter (신규)
│   ├── async_http.py          # AsyncBinanceFuturesClient (httpx)
│   ├── async_ws.py            # AsyncBinanceUserDataStream (websockets)
│   └── listen_key.py          # ListenKeyManager (keepalive task)
└── kis/
    ├── adapter.py             # KisAdapter (sync, 유지)
    ├── async_adapter.py       # AsyncKisAdapter (신규)
    ├── async_http.py          # AsyncKisClient (httpx)
    ├── async_ws.py            # AsyncKisUserDataStream (websockets)
    └── auth.py                # KisAuth — lazy refresh + asyncio.Lock
```

## AsyncBrokerAdapter Protocol

```python
@runtime_checkable
class AsyncBrokerAdapter(Protocol):
    name: str
    paper: bool

    async def place_order(self, order: OrderRequest) -> OrderAck: ...
    async def cancel_order(self, broker_order_id: str, symbol: str) -> bool: ...
    async def get_order(self, broker_order_id: str, symbol: str) -> OrderAck | None: ...
    async def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    async def get_balance(self) -> Balance: ...
    async def health_check(self) -> HealthStatus: ...
    def stream_fills(self) -> AsyncIterator[BrokerFill]: ...
    async def aclose(self) -> None: ...
```

## aclose 5단계 계약

1. `self._closing = True` → 이후 `place_order()` 즉시 `BrokerClosedError` raise
2. WS close frame 전송 + `wait_closed()` 대기
3. Binance 한정: `_listenkey_task.cancel()` + `await` (KIS 해당 없음)
4. `_inflight` task 목록 전체 `cancel()` + `gather(return_exceptions=True)`
5. `self._client.aclose()` (httpx.AsyncClient 닫기)

idempotent: 두 번 호출해도 안전.

## Fill Stream 이중 표면

```python
# Iterator 직접 소비
async for fill in adapter.stream_fills():
    process(fill)

# Callback wrapper (queue 기반)
async def on_fill(fill: BrokerFill) -> None:
    await record(fill)

await adapter.subscribe_fills(on_fill, queue_size=1000)
```

overflow 정책 (`BROKER_FILL_QUEUE_POLICY` 환경변수):
- `block` (기본): `queue.put()` 대기
- `drop_oldest`: queue 앞에서 하나 제거 후 삽입
- `raise`: `WSDisconnectedError` raise

## 성능 벤치마크 결과

벤치마크 실행 방법:

```bash
# Step 1: sync baseline
pytest tests/performance/broker_sync_baseline.py -v

# Step 2: async bench (reads baseline from results_sync.json)
pytest tests/performance/broker_async_bench.py -v
```

결과 파일: `tests/performance/results_sync.json`, `tests/performance/results_async.json`

자세한 수치는 `docs/work/active/000073-broker-async/02_bench.md` 참조.

## 호출부 마이그레이션 전략

`docs/work/active/000073-broker-async/03_migration.md` 참조.

## 관련 이슈

- #68 — sync 브로커 커넥터 (기준선)
- #73 — 본 async 마이그레이션
- #80 — BrokerExecutor live 루프 (async 어댑터 소비자, F2)
