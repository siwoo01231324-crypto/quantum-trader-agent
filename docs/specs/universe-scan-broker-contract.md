---
type: spec-architecture
id: universe-scan-broker-contract
name: Universe-Scan Broker Contract — KIS/Binance Dynamic Universe
status: proposed
owner: siwoo
created: 2026-05-08
last_updated: 2026-05-08
tags:
- contract
- broker
- universe-scan
- kis
- binance
---

# Universe-Scan Broker Contract — Dynamic Universe Support

universe-scan AsyncStrategy 가 매주 리밸 시 종목 풀이 동적으로 바뀌는 환경에서 **KIS / Binance / paper broker 가 구현해야 할 추가 기능 + 호환성 규칙**.

본 문서는 **계약 (contract)** 이고, 실제 broker 코드 수정은 후속 PR.

## 현재 상태 (2026-05-08, master db8c5fd 기준)

| Broker | 동적 universe 지원 | 비고 |
|---|---|---|
| KIS (`src/brokers/kis/`) | ❌ 사전 등록 instruments 만 quote/order | #133 운영 중, momo_kis_v1 005930+035720+000660 |
| Binance (`src/brokers/binance/`) | 부분 (REST 단건) | momo_btc_v2 BTC 단독 |
| paper (`src/execution/paper_broker.py`) | ❌ MockMatchingEngine 가 사전 인스 가정 | shadow_run_swing.py R4/R6 |

## 추가 요구 (universe-scan 활성화 시)

### 1. Quote / OHLCV fetch — 매주 리밸 시 top-N 일괄 조회

KIS:
- **새 메서드**: `async def fetch_universe_snapshot(codes: list[str]) -> dict[str, Quote]`
- 350 종목 호가 / 1주일 일봉 1회 호출. Rate-limit: 분당 20 호출 한도 내 (#212 backoff 활용).
- 구현 옵션: (a) 멀티-symbol REST endpoint `FHKST03010100` 일괄, (b) per-symbol async with semaphore.
- 응답 캐싱: 1분 TTL — 같은 리밸 사이클 내 다중 strategy 가 호출 시 재사용.

Binance:
- 이미 `https://api.binance.com/api/v3/ticker/24hr` 일괄 + klines per-symbol REST 로 가능.
- **새 메서드**: `async def fetch_universe_klines(symbols: list[str], interval: str, limit: int) -> dict[str, pd.DataFrame]`
- Rate-limit: weight 기반, 350 종목 = 350 weight ≤ 1200/min 한도.

### 2. Order — basket 단위 발주 + 단주 반올림 + 잔여 현금

본 이슈에서 **`src/portfolio/weights_to_orders.py`** 가 weights → list[OrderIntent] 변환 책임.

Broker 측 추가 요구:
- **순차 발주 with throttle** — 동시 350 종목 주문 시 KIS rate-limit (#212/#213) spike → backoff. 인위적 지연 (예: 100ms) 추가 옵션.
- **부분 체결 처리** — 일부 종목 미체결 시 리밸 후 cash 가 weights 합 ≠ 1 — 다음 봉에 retry 또는 cash 비중 조정.
- **failed orders 보고** — orchestrator 에 list[OrderFailure] 반환 → daily_check 가 모니터링.

### 3. paper broker — 동적 instrument

`PaperBroker(initial_balance, balance_asset)` 가 현재 단일 자산 전제.

- **변경**: matched orders 가 새 종목을 처음 체결할 때 자동으로 Position 인스턴스 생성.
- **MockMatchingEngine** — `set_market_state(symbol, MarketState)` 가 동적 universe 의 모든 종목에 호가 시뮬레이션 가능해야.
- 기존 single-symbol 운영과 호환 (legacy R4/R6 path 변경 불필요).

### 4. 잔고 / 포지션 — basket-level 조회

`get_positions()` 이 현재 dict[symbol → Position] 반환 — 그대로 호환.

추가:
- **basket aggregation 헬퍼** — `get_basket_positions(strategy_id) -> dict[symbol → Position]` (per-strategy 종목 필터링).
- 근거: position_provider (#192) 가 strategy_id 기반 추적 — universe-scan 다종목 모두 같은 strategy_id 로 묶임.

## OrderIntent 흐름 — universe-scan 표준

```
1. AsyncStrategyOrchestrator.run_bar 시각 (KRX 15:30 KST)
2. CrossSectionalAsyncStrategy.on_bar(ctx) → Signal(buy/sell, size=exposure)
   + strategy.latest_weights : pd.Series[symbol → weight]
3. Orchestrator → portfolio.weights_to_orders(strategy_id, latest_weights, ...)
   → list[OrderIntent]
4. BrokerExecutor (#80) → sequential broker.submit_order(intent)
5. WAL fill 이벤트 → position_provider 갱신 (#192)
6. pnl_aggregator 종목별 + basket 단위 PnL 기록 (#194/#210)
7. Telegram digest = list[OrderIntent] 합산 1건 ("매수 X / 매도 Y / 유지 Z")
```

## 구현 우선순위 (제안)

| 우선순위 | 작업 | 예상 시간 |
|---|---|---|
| P0 | paper broker 동적 instrument 지원 | 0.5일 |
| P0 | weights_to_orders ↔ orchestrator 통합 (#218 본 PR 1차) | 0.5일 |
| P1 | KIS broker fetch_universe_snapshot + rate-limit 검증 | 1일 |
| P1 | KIS broker 순차 발주 throttle + failed orders 보고 | 0.5일 |
| P2 | Binance broker fetch_universe_klines (이미 일부 있음) | 0.3일 |
| P2 | basket aggregation 헬퍼 | 0.3일 |
| P3 | 1주일 paper 통합 시뮬 + R4/R6 와 비교 | 1일 |

총 4-5일 broker 작업 분량. 본 #218 PR 의 broker 부분은 P0 (paper broker + orchestrator 통합) 까지로 한정 — 라이브 (KIS) 통합은 별도 후속 이슈로 분리 가능.

## 호환성

- 기존 single-ticker 전략 (`momo_kis_v1`, `momo_btc_v2`, R4/R6) 의 broker 호출 시그니처는 변경 없음 — 추가 메서드만 추가.
- production.yaml 에 universe-scan strategy 등록 안 하면 전체 시스템은 기존 path 로 동작 (zero impact).
- 토글 OFF default 로 시작 → 사용자가 dashboard 에서 ON 시키면 broker 가 universe quote fetch 시작.

## 관련 노트

- [[universe-scan-strategy-pattern]] — 본 contract 의 호출자 (전략) 측 spec
- [[cs-tsmom-kr-daily]] — 본 contract 검증 baseline 전략
- `src/portfolio/weights_to_orders.py` — 변환 모듈 (본 #218 에서 추가)
- `src/backtest/strategies/cs_async_wrapper.py` — AsyncStrategy wrap (본 #218 에서 추가)
- `src/brokers/.ai.md` (있다면) — broker 공개 API
- 이슈 #133 (KIS 운영), #143 (R4 paper), #199 (R6 paper), #212/#213 (KIS rate-limit), #218 (universe-scan 전환)
