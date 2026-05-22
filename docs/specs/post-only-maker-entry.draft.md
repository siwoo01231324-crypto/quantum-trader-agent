---
type: spec
id: post-only-maker-entry
title: Post-Only Maker 진입 — 진입 수수료 60%↓ (왕복 약 30%↓)
status: draft
created: 2026-05-22
---

# Post-Only Maker 진입 (수수료 근본 절감)

## ⚑ 진행 현황 — 인수인계 (2026-05-22)

**다음 작업자(5단계): 이 파일이 단일 진실원. `git pull origin master` 먼저.**

### 완료
- **1단계 — conversion LIMIT/GTX 지원** (PR #298 머지). `src/live/conversion.py::intent_to_order_request` 에 `price` / `tif` 파라미터 추가. 기본값 MARKET 동작 bit-identical. `order_type=LIMIT` 시 price 필수(ValueError).
- **2~4단계 — limit price 계산 + 미체결 fallback(partial fill 포함) + `_live_entered` 박제 회피** (post-only Maker 2/3 PR). 핵심:
  - **reference price 는 `OrderIntent.ref_price`** — orchestrator 가 per-symbol `order_price` 를 stamp 한다. draft 초안의 `market_state.tick.last` 는 단일 tick 으로 만들어져 멀티심볼 universe-scan 배치에서 다른 심볼에 틀린 가격을 줬다(gap A) → 폐기. `OrderIntent` 에 `entry_order_type` / `ref_price` 필드 추가.
  - **fallback 은 `src/live/post_only_fallback.py` 신규 모듈.** EXPIRED → 즉시 시장가, NEW/PARTIALLY_FILLED → background task(`POST_ONLY_FALLBACK_SEC=30s`) 로 cancel→시장가. tick loop 비블로킹.
  - **partial fill** — cancel 후 `executedQty`(OrderAck.filled_qty / GetOrderResponse.executedQty 신설) 를 읽어 잔량(origQty − executedQty)만 재발주. dust 잔량은 adapter 의 minQty floor 가 자연 거부.
  - **cancel-race(gap D)** — cancel 실패 시 re-GET, FILLED 면 재발주 금지(중복 차단). cancel 실패 + 주문 활성이면 보수적 abort.
  - 시장가 재발주는 `execute_intents` 재사용 → KillSwitch/sizing 동일 파이프라인. 완전 미체결 시 `on_entry_unfilled` 콜백 → `orchestrator.sync_live_entered(sid, sym, 0)` 으로 `_live_entered` 해제.
  - limit price 의 tick-size 정렬은 adapter `_quantize_price_to_tick` 가 담당.

### 남은 작업 (5단계)
- **5단계 — 전략별 토글 적용.** strategy 에 `entry_order_type` 속성을 `cooldown_after_stop_sec` 와 동일 패턴(`LiveScannerMixin` ClassVar + `__init__` kwarg)으로 추가 → production.yaml `kwargs:` 로 override. **orchestrator 는 이미 `getattr(strat, "entry_order_type", "market")` 로 읽어 stamp** 하므로(2~4단계 PR 완료), 5단계는 strategy 측 속성 추가 + production.yaml 설정만 하면 된다.
  - mean-reversion(`live_bb_lower_bounce`, `live_oversold_with_divergence`) 먼저 → 미체결률(`post_only_fallback` WAL 이벤트의 `outcome` 필드) 모니터 → breakout 확대.

### 주의
- 환경: Binance **testnet** (`binance-testnet-shadow`). 실거래 자금 아님 — testnet 검증 후 mainnet.
- fallback 의 시장가 재발주도 기존 KillSwitch/sizing 게이트 통과 (리스크 우회 금지 — 동일 파이프라인 재사용).
- `post_only_fallback` WAL 이벤트 `outcome` ∈ {filled_maker, filled_during_cancel, resubmitted_market, total_miss, cancel_failed_abort} — 5단계 rollout 판단 근거.

---


> `.draft.md` — 정식 승격은 구현 PR 머지 후 사람이 rename. invariant 검증 제외(#53).

## 배경

2026-05-22 Binance testnet income API (`/fapi/v1/income`) 실측:
- REALIZED_PNL +94.66 / COMMISSION −104.52 / NET **−9.86 USDT** (순손실)
- 타점은 플러스인데 Taker 시장가 145회 거래의 수수료가 수익 초과.

이번 작업은 **진입(BUY)만 post-only** 로 전환한다. 청산(SELL)은 손절 즉시성이
수수료 절감보다 우선이라 시장가를 유지한다 (post-only 미체결로 손절이 지연되면
손실이 확대됨).

- 진입 수수료: 0.045%(Taker) → 0.018%(Maker) — **진입 한쪽 60% 절감**.
- 왕복 수수료: 0.090%(진입 0.045 + 청산 0.045) → 0.063%(진입 0.018 + 청산 0.045)
  — **전체 약 30% 절감**. 일 −104 → −70대 USDT (−37 아님 — 그 숫자는 청산까지
  Maker 일 때만 성립).

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
- **reference price 출처: `OrderIntent.ref_price`** (gap A — A안 채택). orchestrator
  가 `run_bar` 에서 per-symbol 로 계산한 `order_price` 를 `OrderIntent` 에 stamp
  한다. 초안의 `market_state.tick.last` 는 단일 tick 으로 생성돼 멀티심볼
  universe-scan 배치에서 다른 심볼에 틀린 가격을 주는 버그가 있어 폐기.
- tick-size 정렬은 broker adapter (`AsyncBinanceFuturesAdapter._quantize_price_to_tick`)
  가 담당 — executor 는 venue-agnostic 한 raw Decimal 만 산출.

### 3. 미체결 fallback (핵심 — 신중) — `src/live/post_only_fallback.py`
post-only LIMIT 발주 후 `ack.status` 분기:
- **EXPIRED** (taker 가 될 주문이라 거래소가 placement 시점 거부) → 즉시 전량 시장가 재발주 (대기 없음).
- **NEW** (호가창 maker 안착) → background task 로 `POST_ONLY_FALLBACK_SEC`(30s) 대기 후:
  1. `get_order` 로 상태·`executedQty` 조회.
  2. FILLED → maker 체결 성공 (order_filled 은 WS consumer 가 기록), 종료.
  3. NEW/PARTIALLY_FILLED → `cancel_order` → cancel 후 re-GET 으로 `executedQty` 확정.
  4. **partial fill** — 잔량 `origQty − executedQty` 만 시장가 재발주. dust 잔량은 adapter 의 minQty floor 가 자연 거부.
  5. **cancel-race(gap D)** — cancel 실패 시 re-GET. FILLED 면 재발주 금지(중복 차단). cancel 실패 + 주문 아직 활성이면 보수적으로 재발주 abort.
- 시장가 재발주는 `execute_intents` 를 그대로 재사용(KillSwitch/sizing/WAL 동일). 재발주 intent 는 `entry_order_type="market"` → 재귀 fallback 없음. idempotency key 는 `_make_key` 가 ts_ms 기반으로 새로 생성 → 중복 발주 방지.

### 4. `_live_entered` 상호작용 — 채택 해법
`_async_orchestrator.py` dispatch 는 진입 시점(BUY 신호 통과 시)에 `_live_entered.add` 한다. post-only 가 호가창에서 대기하는 30s 동안 이 add 가 유지되는 것은 **의도된 동작** — 그 사이 같은 종목 중복 진입을 막는다.
**채택 해법**: add 는 유지하되, fallback 이 *완전 미체결(체결 0) + 시장가 재발주도 REJECTED* 로 끝나면 `on_entry_unfilled` 콜백 → `orchestrator.sync_live_entered(sid, sym, 0)` 으로 discard (PR #287 메서드 재사용). 부분 체결분이 있으면 포지션이 존재하므로 해제하지 않는다.

**abort dangling (극단 edge — 코드 수정 불요)**: cancel 실패 + 주문 활성으로 abort 하면 그 LIMIT 은 호가창에 잔존한다. maker 로 결국 체결되면 WS fill consumer 가 정상 처리하고 `_live_entered` 도 유효하다. 단 **영구 미체결** 시 `_live_entered` 가 박제된 채 남는다 — `PositionReconciler` 는 *포지션만* 비교하고 미체결 주문은 보지 않으므로 현 reconcile 범위 밖이다. 극단 edge 라 본 PR 범위 밖 후속 과제 (필요 시 open-order 기반 정합 추가).

### 5. 전략별 적용 순서
- **mean-reversion 먼저** — `live_bb_lower_bounce`, `live_oversold_with_divergence` (진입가 근처 횡보 → 미체결률 낮음)
- **breakout 나중** — `live_breakout_with_atr_stop` (빠른 추격 → 미체결률 높을 수 있음). mean-rev 미체결률 모니터링 후 확대.
- production.yaml 또는 전략 kwarg 로 `entry_order_type: post_only | market` 토글.

## 후속 (5단계 이후 — 본 작업 범위 밖)
- **take-profit 청산 post-only 화.** 현재 청산(SELL)은 전부 시장가 — 손절은 즉시성이 필수라 그대로 유지해야 하지만, **take-profit 청산은 가격 목표 도달 후라 수십 초 지연 여유가 있다.** 손절(stop_loss/trailing)은 market 유지하고 TP 청산만 post-only 로 돌리면 왕복 수수료가 0.063% → 0.036% 수준까지 추가로 내려간다 (전체 ~60% 절감). 단 `LivePositionRiskManager` 가 청산 사유(stop vs TP)를 구분해 SELL intent 에 `entry_order_type` 를 실어야 하므로 별도 작업.
- **open-order 기반 reconcile.** §4 의 abort dangling 영구 미체결 edge 처리.

## 테스트 계획 / 결과 (2~4단계)
- `tests/test_conversion.py` — `order_type=LIMIT` + price → OrderRequest price/tif. MARKET 경로 bit-identical (1단계, 11 passed).
- `tests/portfolio/test_orchestrator_post_only_stamp.py` — orchestrator 가 `entry_order_type`/`ref_price` stamp. 미선언 strategy → market 강등.
- `tests/live/test_post_only_fallback.py` — NEW→cancel→전량 시장가 / **PARTIALLY_FILLED→잔량만 재발주** / EXPIRED→즉시 시장가 / **cancel-race→재발주 금지** / cancel 실패+활성→abort / 완전 미체결→`_live_entered` 해제 / 부분 체결→해제 안 함 / executor 통합(EXPIRED 즉시·NEW 백그라운드 예약).
- 회귀: `tests/test_executor.py` · `tests/brokers/` · `tests/portfolio/` · `tests/live/` 전 스위트 통과 (post-only 외 경로 byte-identical).

## 리스크
- post-only 미체결 → 진입 지연 (수십 초). breakout 신호엔 기회손실 가능.
- fallback market 재발주 시 결국 Taker 수수료 — 미체결 잦으면 절감 효과 감소. 미체결률을 메트릭으로 노출 권장.
- 주문 lifecycle(cancel/재발주) 비동기 타이밍 — 중복 발주/경합 방지 idempotency 주의.
- BNB 잔액 보유 시 수수료 추가 10% 할인 (계정 설정, 코드 무관) — 병행 권장.

## 리스크 연동
미체결 fallback 의 market 재발주는 기존 `execute_intents` 의 KillSwitch / sizing 게이트를 그대로 통과해야 한다 — fallback 경로가 리스크 체크를 우회하지 않도록 동일 파이프라인 재사용.
