# [#80] 라이브 실행 프레임워크 (PaperBroker + Phase 1 Shadow Paper) — 작업 계획

> 작성: 2026-04-25 (ralplan v3 컨센서스 통과 — Planner → Architect (ITERATE) → Planner v2 → Critic v2 (ITERATE) → Planner v3 → Critic v3 (APPROVE))

## 완료 기준 (Acceptance Criteria)

### 구현 산출물
- [ ] `src/live/loop.py` 구현 (asyncio 이벤트 루프: tick → orchestrator.run_bar → 결과 처리)
- [ ] `src/live/reconnect.py` 구현 (WS heartbeat + 지수 backoff + REST 스냅샷 보간)
- [x] `src/execution/paper_broker.py` 구현 (호가창 스냅샷 기반, Phase 1 즉시 100% 체결)
- [x] `src/execution/mock_matching.py` 구현 (`MockMatchingEngine` — execution-algorithms §5 스펙)
- [x] `src/live/.ai.md` 신규
- [x] `src/execution/.ai.md` 갱신
- [x] `src/ops/.ai.md` 갱신 (kill-switch trigger 3종 강화)
- [x] `src/observability/.ai.md` 갱신 (paper 메트릭 8종)
- [x] `scripts/.ai.md` 갱신 (shadow_run.py, shadow_report.py)
- [x] `src/live/loop.py` 구현 (asyncio + Windows SelectorEventLoop + production.yaml fallback + tick 큐 backpressure)
- [x] `src/live/reconnect.py` 구현 (지수 backoff + jitter + max_attempts)
- [x] `src/live/feed.py` 구현 (BinancePublicFeed aggTrade WS — 키 불요)
- [x] `src/live/executor.py` 구현 (execute_intents seam 함수, Phase 2 전환 지점)

### 운영·관측
- [ ] **2+ 전략 (#79 머지 후 3전략) 으로 최소 10 거래일 shadow 운영 로그** — 실 운영 (PR 머지 후 #94 머지 + production.yaml 활성화 필요)
- [ ] **주문 건수 N ≥ 30 기록** — 실 운영
- [ ] [[observability]] 메트릭 8종 송출 경로 연결
  - [x] `qta_paper_fills_total`, `qta_paper_pnl_usdt`, `qta_paper_position_qty`, `qta_paper_equity_usdt`
  - [x] `qta_paper_order_ack_latency_ms`, `qta_paper_drawdown_ratio`, `qta_paper_fee_usdt_total`
  - [x] `qta_wal_write_error_total` (WAL 실패 정책)
  - [ ] 기존 메트릭 활용: `qta_ws_disconnect_total`, `qta_ws_lag_ms`, `qta_tick_gap_total`, `qta_risk_breach_total{rule_id}` (실 라이브 운영 시)
- [x] [[kill-switch-dr]] 자동 트리거 3종 테스트 통과
  - [x] DrawdownTrigger: -3%, peak tracking, realized+unrealized USDT equity
  - [x] ApiErrorRateTrigger: 5분 sliding window, error/total > 5% & total ≥ 20
  - [x] AnomalyFillTrigger: 1초 sliding window, 동일 심볼 5건 + 로그 덤프

### Exit Criteria (Phase 1 승격 — [[29-paper-to-live-protocol]] §3.3)
- [ ] WebSocket 단절 자동 재연결 정상 (≥ 1회 검증)
- [ ] 시세 lag > 500ms 발생률 < 5%
- [ ] 모든 체결이 PaperBroker 로그에 남음 (누락 0)
- [ ] 백테스트 Sharpe vs Shadow Paper Sharpe 차이 ≤ 0.3 (비용 포함, 동일 데이터 소스)
- [ ] kill-switch 자동 트리거 3종 테스트 통과

### 롤백 트리거 검증 (§3.4)
- [x] WebSocket 재연결 실패 2회 이상 시나리오 수동 injection 테스트 (`tests/test_rollback_injection.py::test_ws_reconnect_failure_triggers_kill_switch`)
- [x] 체결 누락 1건 이상 시나리오 수동 injection 테스트 (`tests/test_rollback_injection.py::test_fill_missing_detected_by_replay`)
- [x] Sharpe 괴리 > 0.5 시나리오 수동 injection 테스트 (`tests/test_rollback_injection.py::test_sharpe_divergence_fails_report`)

### 문서·리포트
- [x] `scripts/shadow_run.py` (CLI 진입점) + `scripts/shadow_report.py` (WAL → Sharpe 비교 + Exit Criteria 자동 검증)
- [ ] **Exit Criteria 5개 항목 문서화된 증거 (logs + 리포트)** — 실 운영 10거래일 후 생성 (PR 머지 후)
- [ ] **`docs/work/active/000080-paper-broker/02_implementation.md` 에 Shadow 운영 리포트** — 실 운영 후
- [ ] 본 작업 종료 시 work folder → `docs/work/done/000080-paper-broker/` 이동 (PR 머지 후)

### 특허 차용 보강 (out of scope, 별도 이슈로 분리)
- [ ] VWAP 볼륨 프로파일 실시간 blend (`src/execution/vwap.py`) — 후속 이슈
- [ ] OrderRouter 비용 기반 동적 라우팅 (`src/brokers/router.py`) — 후속 이슈
- [ ] TWAP 볼라틸리티 레짐 적응 + KRX VI 게이트 (`src/execution/twap.py`) — 후속 이슈
- [ ] Implementation Shortfall 사전 추정 + TCA 메트릭 (`src/brokers/router.py`) — 후속 이슈

## 의존성 검증

| # | 이슈 | 상태 | 비고 |
|---|------|------|------|
| 73 | 브로커 어댑터 async | ✅ Merged | 6dca351 — `AsyncBrokerAdapter` Protocol 제공 |
| 78 | 멀티 전략 async 오케스트레이터 | ✅ Merged | 994ea11 — `run_bar` → `list[OrderIntent]` |
| 70 | 포트폴리오 리스크 | ✅ Merged | `Snapshot.portfolio_risk` 갱신 경로 |
| 69 | 포지션 사이징 | ✅ Merged | `resolve_size()` 직접 사용 가능 (dummy sizer 불요) |
| 79 | 전략 카탈로그 확장 | ✅ Merged | 6386c5f — 3전략 (momo_btc_v2, meanrev_eth, breakout_donchian) |

모든 blocking 의존성 해소됨. Phase E 착수 전 추가 의존성: **Binance Futures historical data loader** (`src/data/binance_futures_loader.py` 부재 시 후속 이슈로 선행 구현 필요 — Phase A-D 는 차단되지 않음).

### Phase C 진입 전 추가 의존성 — #94 (메타 라벨러 production 활성화)

**#94 가 시공한 것** (master 머지 후 본 worktree 가 rebase 로 흡수):
- `configs/orchestrator/production.yaml` — daemon entry-point YAML, `momo-btc-v2` + `momo-btc-v2-meta` 두 블록 등록
- `src/portfolio/config_loader.py::load_orchestrator_from_yaml(path, *, policy)` — YAML → AsyncStrategyOrchestrator 빌더

**#80 의 Phase C 책임**: `src/live/loop.py` 가 daemon 부팅 시 다음 한 줄을 **반드시 호출**해야 한다. 누락 시 `momo-btc-v2-meta` 가 등록되지 않아 리스크 관리가 무력화된다 (`docs/specs/strategies/momo-btc-v2.md` "프로덕션 구성" 명시).

```python
from pathlib import Path
from src.portfolio.config_loader import load_orchestrator_from_yaml

orch = load_orchestrator_from_yaml(
    Path("configs/orchestrator/production.yaml"),
    policy=...,  # 본 이슈가 결정 — sentry/risk 정책
)
# 이후 이벤트 루프에서 매 tick 마다 await orch.run_bar(ctx) 호출
```

**스스로 새 orchestrator 인스턴스 만들지 말 것** — production.yaml 우회 시 두 전략 등록 누락.

**관련 안내문 위치** (#94 가 4군데 박아둠):
- `configs/orchestrator/.ai.md` — "이 yaml 은 프로덕션 daemon 의 entry-point" 명시
- `src/portfolio/.ai.md` — `config_loader` 사용처 명시
- `src/backtest/strategies/.ai.md` — "리스크 연동" 항목
- `docs/specs/strategies/momo-btc-v2.md` — "프로덕션 구성" + Shadow Paper(#80) 책임 명시

## 불변식 점검

- [ ] LLM 이 라이브 결정에 직접 개입 금지 (CLAUDE.md #6)
- [ ] Idempotency-key 경로 PaperBroker 에서도 실제 브로커와 동일
- [ ] Write-ahead log: 주문/체결 이벤트는 append-only JSONL 에 먼저 쓰고 메모리 반영
- [ ] WAL 쓰기 실패 시 주문 거부 + kill-switch trip + `qta_wal_write_error_total` 증가
- [ ] Single-process lock — 중복 실행 방지 (FMEA F9)
- [ ] Decimal 변환은 `src/live/conversion.py::intent_to_order_request` 단일 지점
- [ ] Phase 1 메트릭 단위는 USDT (KRW 아님 — KIS 모의계좌는 Phase 2)
- [ ] 본 이슈는 가상 체결만 — Phase 2 (KIS 모의계좌 실제 API) 는 별도

## 다음 단계

1. ~~`/plan 80` 으로 구체적 구현 계획 (`01_plan.md`) 확장~~ ✅ 완료 (ralplan v3)
2. `src/live/`, `src/execution/`, `src/ops/`, `src/observability/`, `scripts/` 디렉토리의 `.ai.md` 사전 검토
3. [[29-paper-to-live-protocol]] §3 / [[09-system-components]] §5 / [[execution-algorithms]] §5 정독
4. Phase A 부터 순차 진행 — 테스트 우선 (Red → Green → Refactor)

---

## 구현 계획

> 출처: ralplan v3 컨센서스 통과 (Planner v3 / Architect ITERATE v1 12개 패치 + Critic ITERATE v2 5개 패치 + minor 모두 반영 / Critic APPROVE v3)

### RALPLAN-DR 요약

#### Principles (5)
1. **Decimal 경계 단일 지점**: float→Decimal 변환은 `conversion.py::intent_to_order_request` 한 곳에서만 발생. 상류(orchestrator)는 float, 하류(PaperBroker/WAL/metrics)는 Decimal 강제.
2. **WAL-first 불변식**: 모든 주문/체결 이벤트는 append-only JSONL에 먼저 쓴다. WAL 쓰기 실패 시 주문 거부 + kill switch trip.
3. **Kill-switch 보수적 기본값**: 자동 트리거 3종의 임계값은 보수적으로 설정하되 config 가능. Phase 1에서는 false-positive 허용, false-negative 금지.
4. **Phase 1 단순화 우선**: 즉시 100% 체결, 슬리피지 0, partial fill 미지원. 복잡성은 Phase 2+로 이연.
5. **비교 가능한 Sharpe**: shadow vs backtest 비교 시 동일 데이터 소스(Binance Futures), 동일 슬리피지 모델, 동일 수수료, 동일 사이징 4조건 강제.

#### Decision Drivers (Top 3)
1. **안전성**: WAL 실패→주문 거부, kill-switch 자동 trip, Decimal 정밀도 보장
2. **검증 가능성**: shadow-backtest Sharpe 비교의 전제 조건 4종 명시적 검증
3. **점진적 복잡성**: Phase 1은 최소 복잡도 (0-슬립, 즉시 체결), Phase 2+에서 점진 확장

#### Viable Options + 결정

| 선택지 | 설명 | 채택 |
|--------|------|------|
| A. PaperBroker 내장 매칭 | PaperBroker 클래스 내부에 체결 로직 포함 | ❌ |
| **B. PaperBroker + MockMatchingEngine 분리** | 체결 정책을 별도 엔진으로 분리, PaperBroker 는 어댑터만 | ✅ **(채택)** |
| C. 기존 backtest engine 재사용 | bar-replay 전용 설계라 실시간 tick 처리 불가 | ❌ |

**Option A 기각 사유**: 체결 정책 교체 불가 (Phase 2 슬리피지 모델 주입 시 PaperBroker 전체 수정 필요).
**Option C 기각 사유**: bar-replay 전용 설계, 실시간 asyncio tick 처리 부재.
**Option B 채택**: MockMatchingEngine 을 교체/확장 가능한 별도 컴포넌트로 분리하여 Phase 2+ 확장성 확보.

---

### Phase A: 핵심 타입 + WAL + Decimal 변환

**목표**: 상태 기록/복구 인프라 구축. 이후 모든 Phase 가 의존하는 기반 레이어.

#### A1. 타입 정의 — `src/live/types.py`

```python
class OrderStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
```

#### A2. Decimal 변환 경계 — `src/live/conversion.py`

> `OrderIntent.qty: float` → `OrderRequest.qty: Decimal` 변환은 **`src/live/conversion.py::intent_to_order_request` 단일 지점에서만** 발생한다.
> - 변환 이전 (orchestrator, sizing): `float` 유지 (CPU/메모리 효율)
> - 변환 이후 (PaperBroker, WAL, metrics, BrokerFill): **`Decimal` 강제** (`BrokerFill.__post_init__` 가 float 거부)
> - 변환 함수: `Decimal(str(intent.qty)).quantize(symbol_step_size, rounding=ROUND_DOWN)`
> - `symbol_step_size`: Binance USDT-M 심볼별 stepSize. `src/live/conversion.py::SYMBOL_STEP_SIZES` 상수 dict 로 관리.
> - Phase 1 지원 심볼: `BTCUSDT = Decimal("0.001")`, `ETHUSDT = Decimal("0.001")`, `SOLUSDT = Decimal("1")`
> - `ROUND_DOWN`: stepSize 미만 수량은 절사 (시장가 주문에서 잔량 발생 방지)

```python
def intent_to_order_request(
    intent: OrderIntent,
    *,
    idempotency_key: str,
    order_type: OrderType = OrderType.MARKET,
) -> OrderRequest: ...
```

**단위 테스트** (`tests/test_conversion.py`):
- `qty=0.0099, step=Decimal("0.001")` → `Decimal("0.009")` (절사)
- `qty=0.001, step=Decimal("0.001")` → `Decimal("0.001")` (정확)
- `qty=1/3 (float), step=Decimal("0.001")` → `Decimal("0.333")` (오차 절사)
- 미등록 심볼 → `ValueError`

#### A3. WAL — `src/live/wal.py`

WAL JSONL 스키마:
```python
@dataclass(frozen=True)
class WALEvent:
    ts: str             # 시스템 UTC ISO 8601 (예: "2026-04-25T09:00:00.123456+00:00")
    event_type: str     # order_submitted | order_filled | order_rejected | order_cancelled | fill_anomaly
    schema_version: int # 1 (향후 마이그레이션 위해)
    payload: dict       # event_type 별 스키마

@dataclass(frozen=True)
class WALCorruption:
    line_no: int
    raw: str
    error: str
```

**payload 스키마 (event_type 별)**:
- `order_submitted`: `{client_order_id, symbol, side, qty (str), price_intent (str|None), order_type, server_ts (str|None), strategy_id}`
- `order_filled`: `{client_order_id, broker_order_id, symbol, side, qty (str), fill_price (str), fill_qty (str), fees (str), fee_asset, ack_latency_ms (float), trade_id, server_ts}`
- `order_rejected`: `{client_order_id, symbol, reject_reason, error_message}`
- `order_cancelled`: `{client_order_id, broker_order_id, symbol}`
- `fill_anomaly`: `{trigger_type, fills_in_window (list of trade_id), trip_reason}`

**필수 규칙**:
- 모든 가격/수량 필드는 `str(Decimal_value)` 로 직렬화 (JSON float 정밀도 손실 방지)
- 역직렬화 시 `Decimal(payload["qty"])` 등으로 복원
- `ts` / `server_ts` 는 ISO 8601 UTC (`datetime.now(timezone.utc)`)
- `schema_version` 변경 시 `replay()` 가 마이그레이션 처리

```python
class WAL:
    def __init__(self, path: Path) -> None: ...
    def write(self, event: WALEvent) -> None: ...  # append-only, fsync 즉시

class WALWriteFailed(Exception): ...

def replay(path: Path) -> tuple[list[WALEvent], list[WALCorruption]]:
    """정상 이벤트 + 손상 이벤트 메타 동시 반환."""
```

**Graceful 복구 정의**: 손상 행 skip + warning 로그 + 앞선 정상 행만 반환.
**손상 시뮬 4종**: truncate, 잘못된 JSON, 빈 행, BOM.

#### A4. WAL 쓰기 실패 정책

WAL `write()` 가 `IOError` / `OSError` / `FileNotFoundError` raise 시:
1. `metrics.wal_write_error_total.inc(labels={"error_type": type(err).__name__})`
2. `kill_switch.trip(reason="wal_write_failed", source="auto:wal_fail")`
3. 호출자 (`executor.py`) 가 `WALWriteFailed` 예외 catch → `OrderAck(status=OrderStatus.REJECTED, reject_reason="WAL_WRITE_FAIL")` 반환
4. 신규 주문 차단 (kill switch trip 으로 자동 보장)
5. `logger.critical("WAL write failed: %s", err, extra={"event": "wal_fail"})`

**`OrderAck` 확장**: `src/brokers/base.py::OrderAck` 에 `reject_reason: str | None = None` 신규 필드 추가 (기본값 `None` 으로 하위 호환 유지).

#### A5. 단일 프로세스 락 — `src/live/process_lock.py`
- `fcntl.flock` (POSIX) / `msvcrt.locking` (Windows) 또는 `filelock` (PyPI) 기반 파일 락
- 중복 실행 방지 (FMEA F9)
- `pyproject.toml` 에 `filelock>=3.13` 의존성 추가

**Phase A AC**:
- [ ] WALEvent → JSON → WALEvent round-trip (Decimal 무손실)
- [ ] WAL 손상 4종 복구 테스트 통과
- [ ] WAL write 실패 → kill switch trip + OrderAck REJECTED
- [ ] Decimal 변환 3종 edge case + 미등록 심볼 ValueError
- [ ] 프로세스 락 중복 실행 차단

---

### Phase B: PaperBroker + MockMatchingEngine

**목표**: 가상 체결 엔진 + AsyncBrokerAdapter 어댑터 구현. WAL replay 로 상태 복원.

#### B1. MockMatchingEngine — `src/execution/mock_matching.py`

```python
class MockMatchingEngine:
    def __init__(
        self,
        slippage_model: SlippageModel | None = None,  # Phase 1: None (0-슬립)
        seed: int | None = None,
        partial_fill_enabled: bool = False,  # Phase 1: False (즉시 100% 체결)
    ) -> None: ...

    def match(
        self,
        order: OrderRequest,
        market_state: MarketState,  # 호가창 스냅샷
    ) -> list[BrokerFill]: ...
```

**Phase 1 체결 정책 결정**:
- **즉시 100% 체결** (partial fill 미지원)
- **슬리피지 0** (실측 가능한 backtest 비교를 위해)
- **수수료**: Binance USDT-M maker 0.02% / taker 0.05% (모든 시장가는 taker)
- `cancel_order` 는 항상 no-op (미체결 주문 없음). 단, `AsyncBrokerAdapter` Protocol 준수 위해 메서드 시그니처는 유지.

#### B2. PaperBroker — `src/execution/paper_broker.py`

`AsyncBrokerAdapter` Protocol 구현 (`src/brokers/base.py:113-139` 의 11개 메서드):
- `name = "paper"`, `paper = True`
- `place_order`, `cancel_order`, `get_order`, `get_positions`, `get_balance`, `stream_fills`, `health_check`, `aclose`
- `ensure_leverage`, `ensure_margin_type`, `ensure_position_mode` → no-op (paper 모드, 로깅만)
- 초기화 시 WAL replay 로 포지션/잔고/미체결 주문 복원 (FMEA F8 — PaperBroker 는 외부 REST 가 없으므로 WAL 이 유일한 복구 소스)
- `place_order(req) -> OrderAck`:
  1. `kill_switch.assert_allow_order(liquidation=req.emergency_exit)`
  2. WAL write (`order_submitted`) — 실패 시 `WALWriteFailed`
  3. `MockMatchingEngine.match(req, market_state)` → fills
  4. fills 각각 WAL write (`order_filled`)
  5. 포지션/잔고 업데이트
  6. 메트릭 송출
  7. `OrderAck` 반환

**Phase B AC**:
- [ ] `AsyncBrokerAdapter` Protocol 런타임 체크 통과
- [ ] place_order → fill → WAL → 포지션 업데이트 통합 테스트
- [ ] WAL replay 로 포지션/잔고 복원 테스트
- [ ] kill switch trip 시 신규 주문 차단, 청산(`emergency_exit=True`) 허용
- [ ] BrokerFill float 거부 정책 통과

---

### Phase C: 이벤트 루프 + 재연결 + Executor

**목표**: 실시간 데이터 수신 → 전략 실행 → Paper 체결 end-to-end 루프.

#### C1. 이벤트 루프 — `src/live/loop.py`

**Windows SelectorEventLoop 정책** (파일 최상단):
```python
import sys, asyncio
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
```
이유: 본 프로젝트는 Windows 11 환경, `websockets` 라이브러리는 SelectorEventLoop 에서만 안정적.

**`async def run_shadow_loop(config: ShadowConfig) -> None`**:
1. Lock 획득 (단일 인스턴스)
2. WAL 초기화 + replay
3. PaperBroker 생성
4. **`load_orchestrator_from_yaml(Path("configs/orchestrator/production.yaml"), policy=...)`** 로 AsyncStrategyOrchestrator 부트 (#94 산출물 사용 — 직접 인스턴스 생성 금지). `momo-btc-v2` + `momo-btc-v2-meta` 두 블록이 자동 등록되어 메타 라벨러 리스크 관리가 활성화된다.
5. `BinancePublicFeed` 연결
6. Tick 수신 루프: feed → tick 큐 → `run_bar()` → `execute_intents()` → fill
7. Graceful shutdown: SIGINT/SIGTERM → asyncio.Event set → 루프 종료 → WAL flush → lock 해제

**Tick 큐 backpressure 정책**:
- `asyncio.Queue(maxsize=1)` — **latest-only**
- 새 tick 도착 시: 큐가 차있으면 `get_nowait()` 로 이전 tick 제거 후 `put_nowait()`
- drop 발생 시 `qta_tick_gap_total` 증가
- 이유: Phase 1 momentum/meanrev 전략은 latest tick 만 의미 있음, 백로그 누적 = latency 폭주 위험.

**Time source 규약**:
- WAL `ts`: `datetime.now(timezone.utc).isoformat()` (시스템 UTC)
- 메트릭 ts: 시스템 UTC (동일 소스)
- WS 서버 시간: 별도 `server_ts` 필드로 WAL 기록
- lag 계산: `system_ts_at_receive - server_ts`

**idempotency-key 생성**: `f"{strategy_id}:{symbol}:{ts_epoch_ms}"` — PaperBroker 에서도 실제 브로커와 동일 경로.

#### C2. Executor — `src/live/executor.py`

**Phase 2 전환 seam 포인트**:
```python
async def execute_intents(
    intents: list[OrderIntent],
    *,
    broker: AsyncBrokerAdapter,
    kill_switch: KillSwitch,
    wal: WAL,
    metrics: Metrics,
) -> list[OrderAck]:
    """
    Phase 2 전환: broker 인자만 paper_broker → async_router 로 교체하면 실거래 전환.
    """
```

각 intent 처리 흐름:
1. `kill_switch.assert_allow_order()` — tripped 시 `KillSwitchTripped` 예외 (이미 `src/ops/kill_switch.py:23` 에 정의됨)
2. `intent_to_order_request(intent, idempotency_key=...)` 변환 (Decimal 경계 P1)
3. WAL write (`order_submitted`) — 실패 시 `WALWriteFailed` catch → REJECTED 반환 (P2)
4. `broker.place_order(req)` → ack
5. 메트릭 수집 (`qta_orders_total`, `qta_paper_order_ack_latency_ms`)

#### C3. WS 재연결 — `src/live/reconnect.py`
- heartbeat 주기: 30초
- 재연결 전략: 지수 backoff (`src/brokers/async_backoff.py::backoff_sequence` 재사용, 1s→2s→4s…→max 60s + jitter 0~25%)
- 재연결 후: REST 스냅샷으로 마지막 틱 보간 (gap 복구)
- 재연결 실패 ≥ 2회 시 kill-switch trip
- 메트릭: `qta_ws_disconnect_total`, `qta_ws_lag_ms`, `qta_tick_gap_total`

#### C4. Binance Public WS — `src/live/feed.py` (신규 구현)

**중요**: 기존 `src/brokers/binance/async_ws.py` 는 **user-data stream** (listenKey 기반, API 키 필요). Phase 1 Shadow 는 **public stream** (aggTrade) 이 필요하며 키 불요. 신규 구현이다.

```python
class MarketDataFeed(Protocol):
    async def connect(self) -> None: ...
    async def subscribe(self, symbols: list[str]) -> None: ...
    def __aiter__(self) -> AsyncIterator[Tick]: ...
    async def aclose(self) -> None: ...

class BinancePublicFeed(MarketDataFeed):
    """wss://fstream.binance.com/ws/{symbol}@aggTrade"""
```

`Tick.server_ts` = Binance `E` 필드 (ms → UTC ISO8601), `Tick.ts` = 수신 시각 (시스템 UTC).

**Phase C AC**:
- [ ] WS 단절 → 재연결 → tick 재개 통합 테스트
- [ ] 시세 lag > 500ms 발생률 < 5% (10분 스트림 기준)
- [ ] executor WAL write 실패 시 REJECTED 반환 + kill switch trip
- [ ] SIGINT graceful shutdown 테스트
- [ ] tick 큐 backpressure (10 tick → 9 drop, 큐 사이즈 1) 테스트
- [ ] Windows SelectorEventLoop 정책 적용 (`pytest.mark.skipif(sys.platform != "win32")`)
- [ ] **`load_orchestrator_from_yaml("configs/orchestrator/production.yaml")` 부트 + `momo-btc-v2` / `momo-btc-v2-meta` 두 strategy_id 등록 검증 통합 테스트** — `tests/live/test_daemon_boot.py`. 자동 회귀 가드: 누락 시 즉시 fail. (#94 머지 후 활성화)

---

### Phase D: Kill-switch 트리거 강화 + 메트릭 8종

**목표**: 운영 가시성 + 자동 보호. USDT 단위 메트릭.

#### D1. Trigger 3종 임계값 + 계산 방법

기존 `src/ops/triggers.py` 를 확장. 기존 테스트 (`tests/test_kill_switch.py`) 도 동시 업데이트.

**1. DrawdownTrigger** (기존 코드 수정 — peak tracking 추가):
- 임계값: **`-3%`** (config 가능)
- equity 기준: **realized + unrealized PnL 포함한 USDT equity** (Binance USDT-M 모방)
- 변경: `equity_peak` 를 매 update 시 `max(equity_peak, current_equity)` 로 갱신
- 계산식: `equity_current / equity_peak - 1 < -0.03`
- 호출 빈도: 매 fill 후
- trip 후 동작: `kill_switch.trip(reason="drawdown_breach", source="auto:dd")`. 청산(`emergency_exit=True`) 허용.

**2. ApiErrorRateTrigger** (신규 — 기존 `ApiErrorTrigger` 를 rate 기반으로 교체):
- 윈도우: **5분 sliding window** (`deque[datetime]` + `cutoff = now - timedelta(minutes=5)`)
- 임계값: **`5%`** (전체 요청 중 오류 비율)
- 최소 표본 수: **20건** (그 미만이면 무시 — 노이즈 방지)
- 계산식: `error_count / total_count > 0.05 and total_count >= 20`
- trip 후 동작: `kill_switch.trip(reason="api_error_rate", source="auto:api")`

**3. AnomalyFillTrigger** (기존 코드 확장 — 로그 덤프 추가):
- 윈도우: **1초 sliding window** (기존 유지)
- 임계값: 동일 심볼 5건 이상
- trip 후 추가: 전수 fill 로그 덤프 (`logs/shadow/{run_id}/anomaly_dump_{ts}.jsonl`)

**단위 테스트**:
- DrawdownTrigger: equity 1억 → 9700만 → trip / 1억 → 1.1억 → 1.067억 (peak 대비 -3%) → trip
- ApiErrorRateTrigger: 100건 중 6건 오류 → trip / 19건 중 2건 오류 → trip 미발생 (최소 표본 미달) / 100건 중 5건 오류 (정확히 5%) → trip 미발생 (`>` 조건)
- AnomalyFillTrigger: 0.8초 내 5건 fill → trip / 1.2초 내 5건 fill → trip 미발생

#### D2. Paper 메트릭 8종 (USDT 단위)

`src/observability/metrics.py::Metrics` 클래스 추가:

| # | 이름 | 타입 | 라벨 | 단위 |
|---|------|------|------|------|
| 1 | `qta_paper_fills_total` | Counter | strategy, symbol, side | - |
| 2 | `qta_paper_pnl_usdt` | Gauge | strategy | USDT |
| 3 | `qta_paper_position_qty` | Gauge | strategy, symbol | - |
| 4 | `qta_paper_equity_usdt` | Gauge | - | USDT |
| 5 | `qta_paper_order_ack_latency_ms` | Histogram | - | ms |
| 6 | `qta_paper_drawdown_ratio` | Gauge | - | ratio |
| 7 | `qta_paper_fee_usdt_total` | Counter | symbol, fee_type | USDT |
| 8 | `qta_wal_write_error_total` | Counter | error_type | - |

**통화 단위**: Phase 1 은 Binance USDT-M 시뮬 → USDT 통일. KRW 변환은 Phase 2 (KIS 모의계좌) 에서 처리.

**Phase D AC**:
- [ ] 8종 메트릭 모두 `Metrics` 등록 + `METRIC_NAMES` 추가
- [ ] PaperBroker fill 시 메트릭 송출 검증
- [ ] Trigger 3종 임계값 + 최소 표본 단위 테스트 통과
- [ ] WAL ts 가 UTC ISO 8601 포맷, server_ts 별도 필드 보존 검증

---

### Phase E: Shadow 운영 + 리포트 + Injection 테스트

**목표**: 10일 shadow 운영으로 시스템 신뢰도 검증.

#### E1. Shadow 실행 — `scripts/shadow_run.py`

CLI: `python scripts/shadow_run.py --strategies momo_btc_v2,meanrev_eth,breakout_donchian --duration 10d`

내부: `src/live/loop.py::run_shadow_loop()` 호출.
출력: `logs/shadow/{run_id}/wal.jsonl`, `metrics.json`, 일별 rotated 로그.

#### E2. Shadow 리포트 + Sharpe 비교 — `scripts/shadow_report.py`

**비교 대상 백테스트 자동 재실행** (29-paper-to-live-protocol §7.1 4조건 강제):
1. shadow 운영 기간 입력 (`--shadow-start 2026-04-30 --shadow-end 2026-05-13`)
2. 동일 기간 백테스트 자동 재실행:
   - **동일 데이터 소스**: Binance Futures USDT-M public data (`src/data/binance_futures_loader.py` 또는 동등). **KIS 일봉 데이터 사용 금지** (asset class 불일치)
   - **동일 슬리피지 모델**: Phase 1 = 0-슬립
   - **동일 수수료**: Binance USDT-M taker 0.05%
   - **동일 사이징**: `resolve_size()` 동일 호출
3. 백테스트 / shadow 결과 → daily return → Sharpe 계산
4. `|sharpe_backtest - sharpe_shadow| ≤ 0.3` 검증
5. 결과를 `02_implementation.md` 표로 기록

**4조건 불일치 시 fail 처리** (warning 후 결과 제출 거부).

**Strategy returns export**: WAL → 일별 PnL → daily return → `orchestrator.register_strategy_returns(strategy_id, series)` 호출 (CLAUDE.md "새 전략 추가 시 필수" 불변식 충족).

**Phase E 진입 전 게이트**: `src/data/binance_futures_loader.py` 존재 여부 assert. 부재 시 후속 이슈 #9 선행 머지 필요.

#### E3. 롤백 Injection 테스트 3종 — `tests/test_rollback_injection.py`

| 시나리오 | 주입 | 기대 |
|---------|------|------|
| WS 재연결 실패 2회 | feed 강제 disconnect | kill-switch trip + 루프 종료 |
| 체결 누락 1건 | mock matching 에서 fill 삭제 | reconciler 불일치 감지 |
| Sharpe 괴리 > 0.5 | shadow_report 에 조작된 WAL 입력 | 리포트 fail + 경고 |

#### E4. Exit Criteria 자동 검증 — `scripts/shadow_report.py --verify-exit`

- WS 단절 재연결 ≥ 1회
- 시세 lag > 500ms 빈도 < 5%
- 체결 누락 0건
- Sharpe 괴리 ≤ 0.3
- kill-switch 3종 테스트 통과

**Phase E AC**:
- [ ] 1일 shadow 정상 동작 + WAL 파싱
- [ ] shadow_report.py 가 4조건 불일치 시 fail
- [ ] 비교 테이블 (`02_implementation.md`) 자동 생성
- [ ] 롤백 injection 3종 통과
- [ ] 10거래일 shadow 후 Sharpe 괴리 ≤ 0.3
- [ ] Exit Criteria 5개 자동 검증 통과

---

### Guardrails

#### Must Have
1. LLM 이 라이브 결정에 직접 개입 금지 (CLAUDE.md #6)
2. Idempotency-key 경로 PaperBroker 에서도 실제 브로커와 동일
3. WAL-first: 주문/체결 이벤트는 append-only JSONL 에 먼저 쓰고 메모리 반영
4. **WAL 쓰기 실패 시 주문 거부 + kill-switch trip + `qta_wal_write_error_total` 증가** [P2]
5. **WAL replay 복구**: 프로세스 재시작 시 포지션/잔고/미체결 주문 복원 [P0]
6. Single-process lock — 중복 실행 방지 (FMEA F9)
7. **Decimal 변환은 `conversion.py::intent_to_order_request` 단일 지점** [P1]
8. **`Decimal(str(qty)).quantize(step, ROUND_DOWN)` 패턴 강제** [P1]
9. **Windows `WindowsSelectorEventLoopPolicy` 설정** [P0]
10. **Binance public WS (aggTrade) 신규 구현** — user-data stream 과 별개 [P0]
11. **`execute_intents()` seam 함수** — Phase 2 전환 지점 [P1]
12. **Time source 단일화** — WAL ts/메트릭 ts = 시스템 UTC, server_ts 별도 [P1]
13. **latest-only tick 큐** (`asyncio.Queue(maxsize=1)`) [P1]
14. PaperBroker 는 `AsyncBrokerAdapter` Protocol 구현
15. kill-switch trip 후 신규 주문 즉시 차단 (청산 whitelist 만 허용)
16. **Kill-switch 트리거 3종 임계값 명시 + config 가능** [P4]
17. **Shadow-backtest 비교 4조건 (데이터/슬리피지/수수료/사이징) 일치 강제** [P3]
18. **WAL JSONL 내 모든 가격/수량 `str(Decimal)` 직렬화** [P5]
19. **Paper 메트릭 단위는 USDT (KRW 아님)** [P2]
20. **메트릭 총 8종** (WAL 실패 메트릭 포함) [P2]
21. **#94 production.yaml 부트** — `src/live/loop.py` 가 `load_orchestrator_from_yaml(Path("configs/orchestrator/production.yaml"), policy=...)` 한 줄로 orchestrator 를 빌드해야 한다. `momo-btc-v2-meta` 등록 누락 = 리스크 관리 무력화. `tests/live/test_daemon_boot.py` 가 두 strategy_id 등록을 회귀 가드한다.

#### Must NOT
1. 실자금 투입 (Phase 1 은 가상 체결만)
2. **`Decimal(float)` 직접 호출** — 부동소수점 오염 [P1]
3. **WS 서버시간을 시스템 시간과 혼동** [P1]
4. **KIS 일봉 데이터로 Sharpe 비교** (asset class 불일치) [P3]
5. **WAL 쓰기 실패를 무시하고 주문 진행** [P2]
6. WAL 없이 메모리만 갱신
7. Partial fill 구현 (Phase 1 scope 외)
8. 슬리피지 모델 활성화 (Phase 1 = 0-슬립)
9. **Windows 에서 ProactorEventLoop 사용** [P0]
10. **기존 `KillSwitch` 의 `threading.Lock` 변경** (후속 이슈로 분리) [P2]
11. 기존 sync `OrderRouter` 변경 (Phase 2 에서 AsyncOrderRouter 로 대체)
12. 자동 커밋 (드래프트도 리뷰 후 수동 커밋)
13. 특허 차용 4건 (out of scope)
14. **`src/live/loop.py` 에서 `AsyncStrategyOrchestrator` 직접 인스턴스 생성** — 반드시 `load_orchestrator_from_yaml` 경유. production.yaml 우회 시 `momo-btc-v2-meta` 등록 누락.

---

### 테스트 전략

#### 단위 테스트 (Phase A-D)

| 테스트 파일 | 대상 | 핵심 케이스 |
|------------|------|------------|
| `tests/test_conversion.py` | `intent_to_order_request` | Decimal 3종 edge case + 미등록 심볼 |
| `tests/test_wal.py` | WAL write/replay | round-trip, 손상 4종 (truncate/JSON 오류/빈 행/BOM), schema_version 미래, write 실패 → WALWriteFailed |
| `tests/test_process_lock.py` | 프로세스 락 | 중복 실행 차단 |
| `tests/test_mock_matching.py` | MockMatchingEngine | market 즉시 체결, limit 조건부, 수수료 (taker 0.05%), Decimal 정밀도 |
| `tests/test_paper_broker.py` | PaperBroker | Protocol 준수, place_order→fill→WAL→상태, kill switch 차단/청산, WAL replay 복원 |
| `tests/test_kill_switch.py` | Trigger 3종 (확장) | DrawdownTrigger peak tracking, ApiErrorRateTrigger 5분/5%/20표본, AnomalyFillTrigger 1초/5건/로그덤프 |
| `tests/test_executor.py` | execute_intents | 정상 fill, kill switch 차단, WAL 실패 → REJECTED, 메트릭 |
| `tests/test_feed.py` | BinancePublicFeed | 메시지 파싱, server_ts 보존, heartbeat, 재연결 |
| `tests/test_loop.py` | run_shadow_loop | tick 큐 backpressure (10 tick → 9 drop), Windows event loop 정책 (skipif) |

#### 통합 테스트 (Phase C-D)

| 테스트 파일 | 시나리오 |
|------------|----------|
| `tests/test_shadow_loop_integration.py` | fake feed → loop → PaperBroker → fill → 메트릭 end-to-end |
| `tests/test_executor_wal_fail.py` | WAL IOError injection → kill switch trip → REJECTED |
| `tests/test_reconnect_integration.py` | 단절 시뮬 → backoff → 재연결 → tick 재개 |
| `tests/test_paper_broker_replay.py` | place_order 3건 → 재시작 → WAL replay → 상태 일치 |

#### E2E + Shadow (Phase E)

| 테스트 | 검증 |
|--------|------|
| `tests/test_shadow_report_e2e.py` | 4조건 검증, Sharpe 비교, 조건 불일치 시 fail |
| `tests/test_rollback_injection.py` | WS 실패 / 체결 누락 / Sharpe 괴리 3종 |
| 10일 shadow 운영 | 실제 Binance public WS, Exit Criteria 5종 |

---

### 마일스톤

| ID | 마일스톤 | Phase | 게이트 |
|----|---------|-------|--------|
| **M1** | 타입 + WAL + Decimal 변환 + filelock | A | WALEvent round-trip, 손상 복구 4종, WAL fail 정책, Decimal 변환 3종 |
| **M2** | PaperBroker + MockMatchingEngine | B | Protocol 준수, place_order→fill 통합, WAL replay 복원, 수수료 정확 |
| **M3** | 이벤트 루프 + 재연결 + Executor | C | tick→fill end-to-end, WS 재연결, executor WAL fail, Windows event loop |
| **M4** | Trigger 강화 + 메트릭 8종 | D | Trigger 3종 임계값, 메트릭 8종 등록 + 송출 |
| **M5** | Shadow 운영 + Sharpe 비교 + Injection | E | 10거래일 shadow, Sharpe 괴리 ≤ 0.3, Exit Criteria 5종, Injection 3종 |

---

### 신규 모듈 목록 (13개 + .ai.md 갱신)

| 경로 | 역할 | Phase |
|------|------|-------|
| `src/live/__init__.py` | 패키지 | A |
| `src/live/types.py` | OrderStatus enum, WALEvent, WALCorruption, Tick | A |
| `src/live/conversion.py` | `intent_to_order_request` + `SYMBOL_STEP_SIZES` | A |
| `src/live/wal.py` | WAL write/replay + WALWriteFailed | A |
| `src/live/process_lock.py` | 단일 프로세스 락 | A |
| `src/live/executor.py` | `execute_intents` (WAL + broker 호출 조율) | C |
| `src/live/loop.py` | `run_shadow_loop` (asyncio 이벤트 루프) | C |
| `src/live/reconnect.py` | WS heartbeat + 지수 backoff + REST 보간 | C |
| `src/live/feed.py` | MarketDataFeed Protocol + BinancePublicFeed | C |
| `src/live/.ai.md` | 디렉토리 문서 (신규) | A |
| `src/execution/paper_broker.py` | PaperBroker (AsyncBrokerAdapter) | B |
| `src/execution/mock_matching.py` | MockMatchingEngine | B |
| `src/execution/.ai.md` | 갱신 (paper_broker, mock_matching 추가) | B |
| `src/ops/.ai.md` | 갱신 (trigger 3종 강화) | D |
| `src/observability/.ai.md` | 갱신 (paper 메트릭 8종) | D |
| `scripts/shadow_run.py` | Shadow 실행 CLI | E |
| `scripts/shadow_report.py` | 리포트 + Sharpe 비교 + Strategy returns export | E |
| `scripts/.ai.md` | 갱신 (shadow_run, shadow_report) | E |

---

### 후속 이슈로 분리 (본 이슈 변경 금지)

1. **Phase 2 KIS 모의계좌 + AsyncOrderRouter** — 실거래 전환, KRW 메트릭 추가, 4주 실측
2. **Phase 3 Live Pilot** (실자금 5%) — 8주 실측 + 승인 2인
3. **Phase 4 Full Production** (M1~M5 스케일업)
4. **슬리피지 모델 활성화** (`SquareRootImpact`) — Phase 2+ MockMatchingEngine 확장
5. **Partial fill 지원** — `partial_fill_enabled=True`
6. **VWAP 볼륨 프로파일 실시간 blend** (특허 차용 #84-1)
7. **OrderRouter 비용 기반 동적 라우팅** (특허 차용 #84-2)
8. **TWAP 볼라틸리티 레짐 적응** (특허 차용 #84-3)
9. **IS 사전 추정 + TCA 메트릭** (특허 차용 #84-4)
10. **Binance Futures historical data loader** (`src/data/binance_futures_loader.py`) — Phase E 진입 전 필요 (없으면 선행 머지)
11. **KillSwitch `threading.Lock` → `asyncio.Lock` 전환** — Phase 3+ 멀티스레드 검토
12. **DCC-GARCH 시변 상관** — `risk` 모듈 v3
13. **IncrementalFactorSpec** — 라이브 스트리밍 팩터 계산

---

### 기술 부채 기록

| ID | 내용 | 위치 | 해소 시점 |
|----|------|------|----------|
| TD-1 | `OrderAck.status` 가 `str` (Enum 아님) | `src/brokers/base.py` | `OrderStatus` Enum 통일 시 |
| TD-2 | 기존 `DrawdownTrigger` equity 기준이 `starting_equity` 고정 | `src/ops/triggers.py` | Phase D 에서 peak tracking 으로 해소 |
| TD-3 | 기존 `ApiErrorTrigger` 가 consecutive 기반 | `src/ops/triggers.py` | Phase D 에서 ApiErrorRateTrigger 로 교체 |
| TD-4 | `SYMBOL_STEP_SIZES` 하드코딩 (3종) | `src/live/conversion.py` | Phase 2+: Binance REST exchange info 동적 조회 |
| TD-5 | 0-슬립 체결은 실제 시장과 괴리 | `MockMatchingEngine` | Phase 2+: SquareRootImpact 활성화 |
| TD-6 | `qta_paper_pnl_usdt` 단일 통화 | metrics | 다중 자산 확장 시 라벨 추가 |
| TD-7 | `KillSwitch` 가 `threading.Lock` 사용 | `src/ops/kill_switch.py:32` | Phase 3+ 멀티스레드 검토 |

---

### ADR (Architecture Decision Record)

**Decision**: PaperBroker 를 `AsyncBrokerAdapter` Protocol 구현체로, MockMatchingEngine 을 분리된 체결 엔진으로 구현. `execute_intents()` 단일 seam 함수를 통해 Phase 2 실거래 전환 지원. float→Decimal 변환은 `conversion.py` 단일 지점. WAL-first 정책으로 모든 이벤트를 JSONL 에 선기록 (실패 시 주문 거부 + kill-switch trip).

**Drivers**:
1. **Phase 2 전환 비용 최소화** — `execute_intents()` 의 `broker` 인자만 swap 하면 실거래 전환
2. **Windows 11 안정성** — `WindowsSelectorEventLoopPolicy` 필수
3. **Shadow 신뢰도** — WAL-first + 4조건 일치 Sharpe 비교 + 10일 검증

**Alternatives Considered**:
- Option A (PaperBroker 내장 매칭): Phase 2 슬리피지 모델 주입 시 전체 수정 필요. **기각**
- Option B (PaperBroker + MockMatchingEngine 분리): 채택. 관심사 분리 + 테스트 용이성 + Phase 2 확장성
- Option C (기존 backtest engine 재사용): bar-replay 전용, 실시간 tick 처리 불가. **기각**

**Why Chosen**: Option B 는 MockMatchingEngine 을 독립 컴포넌트로 분리하여 Phase 1 의 단순 정책 (0-슬립, 즉시 체결) 과 Phase 2+ 의 복잡 정책 (SquareRootImpact, partial fill) 을 동일 인터페이스로 교체 가능. conversion 레이어는 float→Decimal 경계를 명시적으로 관리하여 타입 안전성 보장. `execute_intents()` seam 은 Phase 2 교체 지점을 단일 함수로 한정.

**Consequences**:
- **긍정**: 체결 정책 교체 용이, WAL 로그로 사후 분석 가능, kill-switch 자동 보호, Phase 2 전환 시 ~50줄 수정으로 충분
- **부정**: 모듈 수 증가 (src/live/ 9파일), Phase 1 0-슬립은 실제 시장과 괴리 (TD-5)
- **리스크**: Binance Futures historical data loader 부재 시 Phase E Sharpe 비교 불가 → 후속 이슈 #10 선행 머지 필요

**Follow-ups**:
1. Phase 2: 슬리피지 모델 활성화 + partial fill (TD-5)
2. Phase 2: `SYMBOL_STEP_SIZES` 동적 조회 (TD-4)
3. Phase 2: `OrderAck.status` 를 `OrderStatus` Enum 통일 (TD-1)
4. Phase 2: AsyncOrderRouter 도입 + KIS 모의계좌 배선
5. Phase E 착수 전: Binance Futures historical data loader 존재 검증
6. Phase 3+: KillSwitch `threading.Lock` 검토 (TD-7)
