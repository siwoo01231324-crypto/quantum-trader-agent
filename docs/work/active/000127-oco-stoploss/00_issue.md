---
type: work-done
id: 00_issue
name: "Issue #127 — Binance OCO + KIS 조건부 주문 사전 등록 (PC 다운 포지션 보호)"
status: active
---

# feat: Binance OCO + KIS 조건부 주문 사전 등록 (PC 다운 포지션 보호) (#127)

## 사용자 관점 목표
단일 PC 절전·네트워크 단절 시 매매 중단 → 보유 포지션 노출. 거래소 측에 손절·익절을 사전 등록해 PC 다운 시 자동 청산되게.

## 산출물 (본 PR — v1)

### `src/brokers/protective_orders.py` 신규 — `ProtectiveOrderManager`

**핵심 기능**:
- `register_protection(symbol, entry_side, qty, entry_price, config)` — 진입 후 SL+TP 한 쌍 자동 등록
- `cancel_protection(symbol)` — 청산 시 한 쌍 자동 취소
- `sync_from_broker(symbol=None)` — PC 재기동 시 거래소 측 살아있는 보호 주문 vs 매니저 상태 비교, orphan 식별

**가격 계산** (LONG 진입 기준):
- SL = entry × (1 - stop_loss_pct)
- TP = entry × (1 + take_profit_pct)
- close_side = SELL (LONG 청산)
- SHORT 진입은 부호 반전

**WAL 감사 로그** (선택, WAL 주입 시):
- `protective_registered` — 등록 성공
- `protective_cancelled` — 명시 취소
- `protective_orphaned` — sync 시 발견된 알 수 없는 보호 주문

### Binance Futures Adapter 확장 (`src/brokers/binance/adapter.py`)

3 신규 메소드:
- `place_protective_order(symbol, side, qty, stop_price, kind)` — REST `POST /fapi/v1/order` 직접 호출 (`STOP_MARKET` / `TAKE_PROFIT_MARKET` + `reduceOnly=true` + `workingType=MARK_PRICE`)
- `cancel_protective_order(symbol, broker_order_id)` — 기존 `cancel_order` wrap
- `list_open_protective_orders(symbol=None)` — `GET /fapi/v1/openOrders` 결과에서 `STOP_MARKET` / `TAKE_PROFIT_MARKET` type 만 필터

OrderType enum / OrderRequest schema 변경 0 — schema 확장 ripple 효과 회피.

### KIS Adapter — interface only

KIS 조건부 주문 (예약매도/매수) 은 본 PR 에서 interface 만 노출:

```python
def place_protective_order(...): raise NotImplementedError(
    "KIS protective order (조건부 주문) endpoint integration pending. "
    "Phase 3 (실자금) 진입 전에 별도 PR 로 통합 — KIS 모의계좌 도큐 "
    "(예약매도 tr_id) 검증 후 구현."
)
```

→ ProtectiveOrderManager 는 KIS adapter 와 같은 인터페이스를 호출. KIS 통합 후속 PR 에서 NotImplementedError 만 제거하면 즉시 작동.

## 완료 기준 (AC 진행 상태)

- [x] **Binance OCO 사전 등록** — `STOP_MARKET` + `TAKE_PROFIT_MARKET` 양 등록 ✅
- [ ] **KIS 조건부 주문 사전 등록** — interface only, 후속 PR (#127 v2)
- [ ] **포지션 진입 시 자동 보호 주문 등록** — Manager API 제공, live_run.py hook 통합 후속
- [ ] **포지션 청산 시 보호 주문 자동 취소** — Manager API 제공, live_run.py hook 통합 후속
- [x] **PC 재기동 시 보호 주문 동기화** — `sync_from_broker` 구현 ✅
- [ ] **모의계좌 통합 테스트** — Binance unit 완료, KIS 통합 후속

→ 본 PR 머지 시 **AC 6 중 2 ✅ + 3 인터페이스 제공** 상태. KIS / live loop 통합은 후속 PR.

## 변경 파일

| 파일 | 변경 |
|---|---|
| `src/brokers/protective_orders.py` (신규) | ProtectiveOrderManager + Config + ProtectivePair + Protocol |
| `src/brokers/binance/adapter.py` | 3 메소드 추가 (place / cancel / list_open) |
| `src/brokers/kis/adapter.py` | 3 메소드 stub (NotImplementedError + TODO) |
| `tests/test_protective_orders.py` (신규) | 17 케이스 — config / 가격계산 / register / cancel / sync / WAL |
| `tests/test_binance_protective.py` (신규) | 9 케이스 — Binance REST payload 검증 (mock client) |

## 검증

- [x] `pytest tests/test_protective_orders.py tests/test_binance_protective.py -q` — **26/26 green**
- [x] `check_invariants --strict` — 175 노트 통과

## 후속 (#127 v2 — 별도 PR, KRX 장 시간 검증 후)

1. KIS 조건부 주문 endpoint 통합 (`tr_id` 검증, 예약매도/매수 분기)
2. `live_run.py` 의 entry/exit 핸들러 hook 통합 — 진입 체결 시 `mgr.register_protection()` 자동 호출
3. PC 재기동 시 `mgr.sync_from_broker()` 자동 호출 (live_run 시작 시)
4. 모의계좌 통합 테스트 (BTCUSDT testnet + KIS 모의계좌 005930)

## 의존성·참고
- 선행: #105 (Phase 2 KIS 모의) ✅, #115 (Binance Futures REST) ✅
- 후행: #107 Phase 3 Live Pilot (실자금) 의 안전망 — v2 머지 후 진입

## 위험·주의
- Binance reduceOnly=true 정책: 포지션 사이즈 초과 시 거래소가 자동 거부 → 안전
- workingType=MARK_PRICE: wick noise 면역. last_price 사용하면 단일 거래로 stop trigger 가능.
- Manager state 휘발성: PC 재기동 시 `sync_from_broker` 필수 호출. live_run.py 통합 시 자동화.
