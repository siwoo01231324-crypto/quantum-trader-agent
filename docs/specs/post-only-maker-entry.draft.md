---
type: spec
id: post-only-maker-entry
title: Post-Only Maker 진입 — 수수료 60% 절감
status: draft
created: 2026-05-22
---

# Post-Only Maker 진입 (수수료 근본 절감)

## ⚑ 진행 현황 — 인수인계 (2026-05-22)

**다음 작업자: 이 파일이 단일 진실원. `git pull origin master` 먼저 (로컬 stale 가능).**

### 완료
- **1단계 — conversion LIMIT/GTX 지원** (PR #298 머지). `src/live/conversion.py::intent_to_order_request` 에 `price` / `tif` 파라미터 추가. 기본값은 MARKET 동작 bit-identical. `order_type=LIMIT` 시 price 필수(ValueError). 테스트 `tests/test_conversion.py` 11 passed.

### 남은 작업 (2~5단계 — 순서대로)
- **2단계 — limit price 계산.** `src/live/executor.py::execute_intents` 에서 post-only 모드일 때 reference price(`market_state.tick.last`) 로 limit 가격 산출: buy `ref×(1−0.0005)`, sell `ref×(1+0.0005)`. tick step quantize. `intent_to_order_request(..., order_type=OrderType.LIMIT, price=<calc>, tif=TimeInForce.GTX)` 호출.
- **3단계 — 미체결 fallback.** post-only 발주 후 `ack` 확인. EXPIRED(post-only 가 taker 라 거부) → 즉시 market 재발주. NEW(orderbook 대기) → `POST_ONLY_FALLBACK_SEC`(30~60s) 대기 후 주문 상태 GET → 여전히 NEW 면 `broker.cancel_order` → market 재발주. **중복 발주 방지 idempotency 필수.** broker 의 주문상태 조회 메서드는 `src/brokers/binance/async_http.py` 확인 (cancel_order 는 line ~220 에 존재).
- **4단계 — `_live_entered` 박제 회피 (중요).** `src/portfolio/_async_orchestrator.py` dispatch 가 BUY 신호 통과 시점에 `_live_entered.add` 함. post-only 미체결 동안 add 되면 미체결인데 "보유 중" 박제 → 그 종목 영구 진입차단 (PR #287 가 고친 버그 재현). 해결: add 를 체결 확인 후로 미루거나, 미체결+cancel 시 `orchestrator.sync_live_entered(sid, sym, 0)` 호출(PR #287 메서드 재사용).
- **5단계 — 전략별 적용.** production.yaml 또는 전략 kwarg 로 `entry_order_type: post_only|market` 토글. mean-reversion(`live_bb_lower_bounce`, `live_oversold_with_divergence`) 먼저 적용 → 미체결률 모니터 → breakout 확대.

### 주의
- 환경: Binance **testnet** (`binance-testnet-shadow`). 실거래 자금 아님 — testnet 에서 검증 후 mainnet.
- 비동기 주문 lifecycle — `execute_intents` 가 fallback 대기로 블록되면 호출 loop 지연. 별도 task 분리 검토.
- fallback 의 market 재발주도 기존 KillSwitch/sizing 게이트 통과 (리스크 우회 금지 — 동일 파이프라인 재사용).
- 이번 세션 누적 PR #287~#298 (자동매매 정지 fix ~ conversion). post-only 외 작업은 다 완료.

---


> `.draft.md` — 정식 승격은 구현 PR 머지 후 사람이 rename. invariant 검증 제외(#53).

## 배경

2026-05-22 Binance testnet income API (`/fapi/v1/income`) 실측:
- REALIZED_PNL +94.66 / COMMISSION −104.52 / NET **−9.86 USDT** (순손실)
- 타점은 플러스인데 Taker 시장가 145회 거래의 수수료가 수익 초과.

Maker 전환 시 왕복 수수료 0.10% → 0.036% (**60% 절감**). 일 −104 → 약 −37 USDT.
출처: Binance 공식 블로그 Maker vs Taker, `docs/work/done/000119-monthly-10pct-feasibility/02_research.md`.

## 현 구조 (정독 완료 — 2026-05-22)

- `src/live/conversion.py::intent_to_order_request` — OrderIntent → OrderRequest **단일 변환점**. `order_type` 파라미터 있음 (default `OrderType.MARKET`). 현재 `price=None`, `tif=TimeInForce.GTC` 하드코딩.
- `src/brokers/binance/async_http.py::place_order` (line ~195) — `req.price is not None` 이면 `params["price"]` + `params["timeInForce"]=req.tif.value` 전송. **LIMIT+GTX 이미 지원.**
- `OrderRequest` (`src/brokers/base.py`) — `order_type` / `price` / `tif` 필드 보유. `TimeInForce.GTX`(post-only) enum 존재 (`src/execution/base.py`).
- `execute_intents` (`src/live/executor.py`) — OrderIntent → conversion → `broker.place_order`. **구현 시 정독 필요** (미체결 fallback 삽입 위치).

## 구현 설계

### 1. conversion 확장
`intent_to_order_request` 에 `price: Decimal | None`, `tif` 파라미터 추가. `order_type=LIMIT` 이고 price 주어지면 OrderRequest 에 price/tif 채움. 기존 호출(order_type 미지정)은 MARKET/None/GTC 그대로 — **bit-identical 하위호환**.

### 2. limit price 계산 (maker 보장)
- buy: `reference_price × (1 − 0.0005)` (현재가보다 약간 아래 → 매수 maker)
- sell: `reference_price × (1 + 0.0005)` (위 → 매도 maker)
- reference price 출처: `market_state.tick.last` 또는 OrderIntent. tick step 으로 quantize.

### 3. 미체결 fallback (핵심 — 신중)
post-only LIMIT 발주 → `POST_ONLY_FALLBACK_SEC`(기본 30~60s) 대기 → 주문 상태 GET → `status == "NEW"`(미체결) 이면:
1. `cancel_order(symbol, broker_order_id=...)`
2. `order_type=MARKET` 으로 재발주 (idempotency key 는 새로 — 중복 발주 방지)
fallback 로직은 `execute_intents` 또는 별도 async 헬퍼. **중복 발주 방지** idempotency 필수.

### 4. `_live_entered` 상호작용 — 반드시 주의
현재 `_async_orchestrator.py` dispatch 가 진입 시점(BUY 신호 통과 시)에 `_live_entered.add`. post-only 미체결 동안 add 되면 → 미체결인데 "보유 중" 으로 박제 → 그 종목 영구 진입차단 (= PR #287 가 고친 버그의 재현).
**해결**: `_live_entered.add` 를 체결 확인 후(또는 fallback 완료 후)로 미루거나, 미체결+cancel 시 `orchestrator.sync_live_entered(sid, sym, 0)` 호출해 discard (PR #287 에서 만든 메서드 재사용).

### 5. 전략별 적용 순서
- **mean-reversion 먼저** — `live_bb_lower_bounce`, `live_oversold_with_divergence` (진입가 근처 횡보 → 미체결률 낮음)
- **breakout 나중** — `live_breakout_with_atr_stop` (빠른 추격 → 미체결률 높을 수 있음). mean-rev 미체결률 모니터링 후 확대.
- production.yaml 또는 전략 kwarg 로 `entry_order_type: post_only | market` 토글.

## 테스트 계획
- conversion: `order_type=LIMIT` + price → OrderRequest 에 price/tif 채워짐. 기존 MARKET 경로 bit-identical.
- 미체결 fallback: post-only NEW → cancel + market 재발주 (모킹).
- `_live_entered`: post-only 미체결 시 박제 안 됨 / fallback 후 정합.
- 회귀: `tests/live/` broker + executor 스위트.

## 리스크
- post-only 미체결 → 진입 지연 (수십 초). breakout 신호엔 기회손실 가능.
- fallback market 재발주 시 결국 Taker 수수료 — 미체결 잦으면 절감 효과 감소. 미체결률을 메트릭으로 노출 권장.
- 주문 lifecycle(cancel/재발주) 비동기 타이밍 — 중복 발주/경합 방지 idempotency 주의.
- BNB 잔액 보유 시 수수료 추가 10% 할인 (계정 설정, 코드 무관) — 병행 권장.

## 리스크 연동
미체결 fallback 의 market 재발주는 기존 `execute_intents` 의 KillSwitch / sizing 게이트를 그대로 통과해야 한다 — fallback 경로가 리스크 체크를 우회하지 않도록 동일 파이프라인 재사용.
