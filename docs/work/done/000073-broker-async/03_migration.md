---
id: 000073-broker-async-03-migration
type: work-done
name: Broker Async 호출부 마이그레이션 가이드
title: Broker Async — 호출부 마이그레이션 가이드
status: done
issue: "#73"
---

# Broker Async — 호출부 마이그레이션 가이드 (03_migration)

## 개요

`BrokerAdapter` (sync) → `AsyncBrokerAdapter` (async) 로 호출부를 마이그레이션하는 단계별 가이드.
기존 sync 어댑터는 삭제하지 않으며, async 어댑터를 새로 주입하는 방식으로 전환한다.

## 병존 전략 (Option A)

```
src/brokers/base.py
├── BrokerAdapter          # Protocol — sync (유지, backtest/ops 호환)
└── AsyncBrokerAdapter     # Protocol — async (신규, live 루프용)

src/brokers/binance/
├── adapter.py             # BinanceFuturesAdapter     — sync (유지)
└── async_adapter.py       # AsyncBinanceFuturesAdapter — async (신규)

src/brokers/kis/
├── adapter.py             # KisAdapter                — sync (유지)
└── async_adapter.py       # AsyncKisAdapter           — async (신규)
```

## 마이그레이션 대상 및 우선순위

| 모듈 | 현재 어댑터 | 전환 대상 | 우선순위 | 비고 |
|------|------------|-----------|---------|------|
| `src/execution/` | BrokerAdapter (sync) | AsyncBrokerAdapter | High | #80 BrokerExecutor |
| `src/ops/` | BrokerAdapter (sync) | AsyncBrokerAdapter | Medium | 헬스체크·모니터링 |
| `src/backtest/` | BrokerAdapter (sync) | **유지** | N/A | backtest는 sync로 충분 |
| `src/portfolio/` | (미사용) | AsyncBrokerAdapter | Follow-up | AsyncOrderRouter (#F2) |

## 단계별 전환 절차

### Step 1 — async 어댑터 인스턴스화

```python
# Before (sync)
from src.brokers.binance.adapter import BinanceFuturesAdapter
adapter = BinanceFuturesAdapter(
    api_key=..., secret=..., base_url=..., paper=True
)

# After (async)
from src.brokers.binance.async_adapter import AsyncBinanceFuturesAdapter
adapter = AsyncBinanceFuturesAdapter(
    api_key=..., secret=..., base_url=...,
    ws_base_url="wss://fstream.binance.com",
    paper=True,
    fill_queue_size=int(os.getenv("BROKER_FILL_QUEUE_SIZE", "1000")),
    overflow_policy=os.getenv("BROKER_FILL_QUEUE_POLICY", "block"),
)
```

### Step 2 — 호출부 async 전파

```python
# Before
def run_order(order):
    ack = adapter.place_order(order)
    return ack

# After
async def run_order(order):
    ack = await adapter.place_order(order)
    return ack
```

### Step 3 — aclose 등록

```python
# 컨텍스트 매니저 없이 사용 시
try:
    await run_live_loop(adapter)
finally:
    await adapter.aclose()

# 또는 contextlib.asynccontextmanager 로 래핑
from contextlib import asynccontextmanager

@asynccontextmanager
async def managed_adapter(**kwargs):
    adapter = AsyncBinanceFuturesAdapter(**kwargs)
    try:
        yield adapter
    finally:
        await adapter.aclose()
```

### Step 4 — Fill 스트림 소비

```python
# Iterator 방식
async for fill in adapter.stream_fills():
    await process_fill(fill)

# Callback 방식 (queue 기반)
async def on_fill(fill: BrokerFill) -> None:
    await db.record(fill)

await adapter.subscribe_fills(on_fill, queue_size=1000)
```

### Step 5 — 환경 변수 설정

```bash
# fill queue 크기 (기본 1000)
export BROKER_FILL_QUEUE_SIZE=2000

# overflow 정책: block | drop_oldest | raise (기본 block)
export BROKER_FILL_QUEUE_POLICY=drop_oldest
```

## 테스트 마이그레이션

### 기존 sync 테스트 — 그대로 유지

```python
# tests/brokers/test_binance_adapter.py — 변경 없음
# sync 어댑터는 backtest / regression gate 용도로 계속 필요
```

### 새 async 테스트 패턴

```python
import pytest
import respx

@pytest.mark.asyncio
async def test_place_order():
    with respx.mock(base_url="https://testnet.binancefuture.com") as router:
        router.post("/fapi/v1/order").respond(200, json=ORDER_RESP)
        router.get("/fapi/v1/time").respond(200, json=TIME_RESP)
        adapter = AsyncBinanceFuturesAdapter(
            api_key="key", secret="secret",
            base_url="https://testnet.binancefuture.com",
            paper=True,
        )
        ack = await adapter.place_order(make_order())
        assert ack.status == "FILLED"
        await adapter.aclose()
```

### 픽스처 — Windows asyncio 정책

`tests/conftest.py` (루트 레벨):

```python
import asyncio, sys
import pytest

@pytest.fixture(autouse=True)
def _win_selector_policy():
    if sys.platform == "win32":
        policy = asyncio.WindowsSelectorEventLoopPolicy()
        asyncio.set_event_loop_policy(policy)
    yield
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.DefaultEventLoopPolicy())
```

## 롤백 절차

async 전환 후 문제 발생 시:

1. 주입 지점에서 `AsyncBrokerAdapter` → `BrokerAdapter` 로 교체
2. 호출부 `await` 제거
3. `aclose()` → 동기 `close()` (또는 생략)
4. sync 어댑터는 삭제되지 않았으므로 즉시 롤백 가능

## 알려진 제약

- `asyncio.TaskGroup` — Python 3.11+ 필수
- `websockets>=12` — 하위 버전과 API 차이 있음 (`websockets.connect()` 반환 타입)
- Windows 환경: `asyncio.ProactorEventLoop` 는 `subprocess` 와 충돌할 수 있음.
  `WindowsSelectorEventLoopPolicy()` 를 `conftest.py` 에 픽스처로 고정.
- `httpx.AsyncClient(trust_env=False)` — 시스템 프록시 무시. CI 환경에서 proxy 오염 방지.

## Follow-up 이슈

- **#80 BrokerExecutor**: `list[OrderIntent]` 를 소비해 async 어댑터로 주문 실행
- **#F2 AsyncOrderRouter**: 멀티 심볼 동시 주문 라우팅 (본 이슈 out-of-scope)
