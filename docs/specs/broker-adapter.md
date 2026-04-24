---
id: broker-adapter
type: spec-architecture
title: 브로커 어댑터 공통 인터페이스 명세
name: 브로커 어댑터 공통 인터페이스 명세
owner: siwoo
status: active
---

# 브로커 어댑터 공통 인터페이스 명세

관련 노트: [[10-broker-api-comparison]], [[execution-algorithms]], [[kill-switch-dr]]

## 1. 목적

KIS(한국투자증권)와 Binance Futures 브로커를 단일 `BrokerAdapter` Protocol 로 추상화한다.
상위 레이어(전략·리스크·실행)는 브로커 구현체를 교체해도 코드 수정 없이 동작해야 한다.

## 2. 공통 인터페이스 (BrokerAdapter Protocol)

```python
class BrokerAdapter(Protocol):
    name: str
    paper: bool

    def place_order(self, req: OrderRequest) -> OrderAck: ...
    def cancel_order(self, *, broker_order_id=None, client_order_id=None, symbol: str) -> None: ...
    def get_order(self, *, broker_order_id=None, client_order_id=None, symbol: str) -> OrderAck: ...
    def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    def get_balance(self) -> list[Balance]: ...
    def stream_fills(self, on_fill: Callable[[BrokerFill], None]) -> Closeable: ...

    def ensure_leverage(self, symbol: str, leverage: int) -> None: ...
    def ensure_margin_type(self, symbol: str, mode: MarginType) -> None: ...
    def ensure_position_mode(self, *, hedge: bool) -> None: ...
    def health_check(self) -> HealthStatus: ...
```

- `place_order` 진입: `KillSwitch.assert_allow_order(liquidation=req.emergency_exit)` 게이트 필수
- `ensure_*`: idempotent — 현재 상태 조회 후 일치하면 no-op
- KIS: `position_side`, `reduce_only`, `close_position` 무시 (지원 안 함 — `BrokerError` 아닌 warning)

## 3. 자료형

### BrokerFill (Decimal 기반 — C1)

```python
@dataclass(frozen=True)
class BrokerFill:
    parent_id: str
    broker_order_id: str
    client_order_id: str
    trade_id: str           # (broker_order_id, trade_id) 조합으로 dedup
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str          # "USDT" / "KRW"
    ts: datetime
    is_maker: bool
```

`__post_init__` 에서 qty/price/fee 가 `Decimal` 인지 검증 — float 전달 시 `TypeError`.

### OrderRequest

```python
@dataclass
class OrderRequest:
    client_order_id: str
    symbol: str
    side: Side
    qty: Decimal
    order_type: OrderType
    price: Decimal | None
    tif: TimeInForce
    position_side: PositionSide = PositionSide.BOTH
    reduce_only: bool = False
    close_position: bool = False
    emergency_exit: bool = False   # KillSwitch liquidation whitelist
```

### OrderAck

```python
@dataclass
class OrderAck:
    broker_order_id: str
    client_order_id: str
    symbol: str
    status: str            # "NEW" | "FILLED" | "CANCELED" | ...
    ts: datetime
    qty: Decimal
    price: Decimal | None
```

### Position, Balance

```python
@dataclass
class Position:
    symbol: str
    side: PositionSide
    qty: Decimal
    entry_price: Decimal
    liquidation_price: Decimal | None = None

@dataclass
class Balance:
    asset: str
    free: Decimal
    locked: Decimal
```

## 4. 상태 머신 다이어그램

```
                    ┌──────────────┐
            ───────►│     NEW      │
                    └──────┬───────┘
                           │ 부분 체결 수신 (BrokerFill)
                           ▼
                    ┌──────────────┐
                    │PARTIALLY_    │──── 추가 체결 ────┐
                    │FILLED        │◄──────────────────┘
                    └──────┬───────┘
                           │ 잔량 = 0
                           ▼
                    ┌──────────────┐
                    │   FILLED     │  (종단)
                    └──────────────┘

    NEW/PARTIALLY_FILLED → CANCELED  (cancel_order 성공)
    NEW                  → REJECTED  (place_order 거부, -2010 등)
    NEW/PARTIALLY_FILLED → EXPIRED   (TIF/TTL 만료)
```

- `PARTIALLY_FILLED`: 부분 체결 누적 중. `ReconnectReconciler` 가 `(broker_order_id, trade_id)` dedup 보장.
- `FILLED`: 전량 체결 완료. `BrokerFill.qty` 합산 = `OrderRequest.qty`.
- `CANCELED`: 취소 완료 (미체결 잔량 소멸). `cancel_order` 성공 응답 기준.
- `REJECTED`: 브로커 거부 (즉시 종단). `place_order` 에서 `InvalidOrderError` raise.
- `EXPIRED`: TIF=IOC/FOK 미체결 또는 GTD 만료. Binance `-2020` (FOK Unable to fill) → `InvalidOrderError`.

## 5. 에러 매트릭스

### Binance Futures (`src/brokers/binance/error_map.py`)

| 코드 | 의미 | BrokerError 하위 |
|------|------|-----------------|
| -1021 | 서버 시각 불일치 (timestamp out of recv_window) | `TimestampError` → 자동 resync + 1회 재시도 |
| -1102 | 필수 파라미터 누락/형식 오류 | `InvalidOrderError` |
| -1111 | 가격/수량 소수점 정밀도 오류 | `InvalidOrderError` |
| -2010 | 주문 거부 (중복 client_order_id 등) | `InvalidOrderError` |
| -2011 | 취소 거부 (이미 체결/취소됨) | `InvalidOrderError` |
| -2013 | 주문 없음 (orderId/clientOrderId 불일치) | `InvalidOrderError` |
| -2019 | 마진 부족 | `InsufficientFundsError` |
| -2020 | FOK 미체결 | `InvalidOrderError` |
| -4061 | positionSide mismatch | `ValidationError` |
| -4164 | minNotional 미달 | `InvalidOrderError` |
| 기타 | 매핑 없음 | `UnknownError` |

### KIS (`src/brokers/kis/error_map.py`)

| 조건 | 의미 | BrokerError 하위 |
|------|------|-----------------|
| `rt_cd != "0"` | API 오류 응답 | `BrokerError` (msg_cd 포함) |
| `msg_cd = "EGW00201"` | Rate limit 초과 | `RateLimitError` |
| `msg_cd = "APBK0013"` | 잔고 부족 | `InsufficientFundsError` |
| `msg_cd = "APBK1670"` | 주문 거부 | `InvalidOrderError` |
| OAuth HTTP 4xx | 토큰 발급 실패 | `AuthError` |

## 6. Idempotency 계약

- `client_order_id` 는 호출자(전략)가 SHA-256 기반으로 결정론적 생성 (`src/brokers/client_id.py`).
  - 입력: `strategy:symbol:side:ts_ms` → hexdigest[:36]
  - Binance regex `^[\.A-Z\:/a-z0-9_-]{1,36}$` 통과 보장
- 네트워크 타임아웃 시 `get_order(client_order_id=...)` 로 상태 조회 후 재처리 결정:
  - 상태 `NEW`/`PARTIALLY_FILLED` → 주문 존재, 중복 발주 금지
  - `InvalidOrderError` (-2013) → 주문 없음, 재발주 가능
- WS 재연결 후 체결 dedup: `ReconnectReconciler` 가 `(broker_order_id, trade_id)` 세트로 중복 방지.
- KIS: 동일 `client_order_id` 재전송 시 브로커가 신규 주문으로 처리할 수 있음 → 타임아웃 후 잔고/포지션 먼저 조회.

## 7. 레이트리밋 모델 (named bucket)

`src/brokers/rate_limiter.py` — 토큰 버킷, named bucket 다중 모델.  
응답 헤더 `X-MBX-USED-WEIGHT-1M` / `X-MBX-ORDER-COUNT-1M` / `X-MBX-ORDER-COUNT-10S` 수신 시 버킷 동기화.

### Binance Futures (실측, 2026-04)

| 버킷 | 한도 |
|------|------|
| weight | 6000 / min |
| orders_1m | 1200 / min |
| orders_10s | 300 / 10s |

### KIS

| 버킷 | 한도 |
|------|------|
| orders_sec | 2 / sec (모의) · 20 / sec (실전) |
| oauth_1m | 1 / min (토큰 발급) |

## 8. 시크릿·키 순환 SOP

- 모든 시크릿은 env 전용 (`python-dotenv`)
- `SecretMaskingFilter` 로 HTTP/WS 로그에서 자동 마스킹
- 마스킹 대상: `api_key`, `secret`, `signature`, `authorization`, `appkey`, `appsecret`, `hashkey`, `cano`, `approval_key`, `access_token`, Bearer 토큰
- 키 순환 시: 새 env 적용 → 프로세스 재시작 → `health_check` 확인

## 9. Runbook 링크

운영 절차 상세: [[broker-runbook]] (`docs/onboarding/broker-runbook.md`)

## 10. KIS 재무지표 조회 (두 엔드포인트 통합)

밸류에이션·스크리닝에 필요한 데이터는 **두 개의 read-only 엔드포인트** 에 나뉘어 있다. 모두 paper/live 변형 없음 (주문 TR-ID 비대칭 의도적).

| 소스 | 엔드포인트 | TR-ID | 제공 지표 | 응답 형태 |
|------|-----------|-------|----------|----------|
| **FinancialRatio** | `/uapi/domestic-stock/v1/finance/financial-ratio` | `FHKST66430300` | EPS, BPS, SPS, ROE, 매출성장률, 영업이익률, 순이익률, 부채비율, 유보율 | `output: list` (분기별, 최신이 [0]) |
| **MarketMultiples** | `/uapi/domestic-stock/v1/quotations/inquire-price` | `FHKST01010100` | PER, PBR, EPS, BPS | `output: dict` (point-in-time) |

### 왜 두 엔드포인트인가

**PER·PBR 은 market multiple 이라 현재가 기반** → `inquire-price` (시세 API) 에만 존재. `financial-ratio` 는 **공시된 분기 재무제표** 기반 지표만 반환 (per-share 값·성장률·마진·부채비율). 2026-04-24 라이브 검증으로 확정.

### 요청 파라미터

#### financial-ratio (FHKST66430300)

| 파라미터 | 필수 | 설명 |
|---------|------|------|
| `FID_COND_MRKT_DIV_CODE` | ✅ | `"J"` (주식) |
| `FID_INPUT_ISCD` | ✅ | 종목코드, 예: `"005930"` |
| `FID_DIV_CLS_CODE` | ✅ | `"1"` (별도) / `"0"` (연결). **누락 시 `OPSQ2001 ERROR INPUT FIELD NOT FOUND` 반환** |

#### inquire-price (FHKST01010100)

| 파라미터 | 필수 | 설명 |
|---------|------|------|
| `FID_COND_MRKT_DIV_CODE` | ✅ | `"J"` |
| `FID_INPUT_ISCD` | ✅ | 종목코드 |

### 응답 필드 (검증 완료 2026-04-24, 삼성전자 005930 기준)

#### financial-ratio `output[i]`

| 필드 | 설명 | 예시값 (2025-12) |
|------|------|-----------------|
| `stac_yymm` | 결산년월 YYYYMM | `"202512"` |
| `eps` | 주당 순이익 (원) | `"6564.00"` |
| `bps` | 주당 순자산 (원) | `"63997.00"` |
| `sps` | 주당 매출액 (원) | `"49471"` |
| `roe_val` | ROE (%) | `"10.85"` |
| `grs` | 매출 성장률 (%) | `"10.8800"` |
| `bsop_prfi_inrt` | 영업이익률 (%) | `"33.2300"` |
| `ntin_inrt` | 순이익률 (%) | `"31.2200"` |
| `lblt_rate` | 부채비율 (%) | `"29.9400"` |
| `rsrv_rate` | 유보율 (%) | `"45296.1700"` |

#### inquire-price `output` (밸류에이션 관련 키만)

| 필드 | 설명 | 예시값 |
|------|------|-------|
| `per` | PER (배) | `"33.44"` |
| `pbr` | PBR (배) | `"3.43"` |
| `eps` | 최근 EPS (원) | `"6564.00"` |
| `bps` | 최근 BPS (원) | `"63997.00"` |

(inquire-price 는 실제로 100+ 필드 반환 — 그 중 밸류에이션 관련 4개만 `MarketMultiples` 스키마에 저장. 나머지는 시세 조회 전용.)

### Pydantic 모델 (`src/brokers/kis/schemas.py`)

- **`FinancialRatio`**: `symbol`, `fiscal_date`, `eps`, `bps`, `sps`, `roe_val`, `grs`, `bsop_prfi_inrt`, `ntin_inrt`, `lblt_rate`, `rsrv_rate` — all `Optional[Decimal]`
- **`MarketMultiples`**: `symbol`, `per`, `pbr`, `eps`, `bps` — all `Optional[Decimal]`

### 클라이언트 함수 (`src/brokers/kis/fundamentals_client.py`)

- `fetch_financial_ratio_series(symbol, ...) -> list[FinancialRatio]` — 분기 시계열, 최신이 `[0]`
- `fetch_market_multiples(symbol, ...) -> MarketMultiples` — point-in-time

### 정규화 (`src/data_lake/fundamentals_store.py`)

`to_fundamentals_frame()` 은 두 타입 모두 받아 `FUNDAMENTALS_PIT_SCHEMA` 에 맞춘 DataFrame 반환:
- **FinancialRatio** → `source="kis_fin_ratio_v1"`, `fiscal_period="YYYYMM"`, `period_end=last-day-of-month(Asia/Seoul)`
- **MarketMultiples** → `source="kis_market_mult_v1"`, `fiscal_period="pit"`, `period_end=ingested_at(UTC)`

단위 매핑:
- `krw`: eps, bps, sps
- `pct`: roe, revenue_growth, operating_profit_margin, net_income_margin, debt_ratio, retained_earnings_rate
- `ratio`: per, pbr

### 픽스처

- `tests/fixtures/kis/financial_ratio_sample.json` — 005930 최근 3 분기 실 응답 (list output)
- `tests/fixtures/kis/inquire_price_sample.json` — 005930 실 응답 (valuation 4 필드만 trimmed)

두 픽스처 모두 `_meta: { source, captured, tr_id, path, symbol, name, params, note }` provenance 헤더 포함 (검증 `test_fixture_has_meta_provenance`).

### 라이브 검증 기록

- **검증일**: 2026-04-24
- **계정**: 모의투자 (paper, openapivts.koreainvestment.com:29443)
- **호출한 종목**: 삼성전자(005930), NAVER(035420), 현대차(005380), 카카오(035720)
- **결과**: 모두 200 OK; 30 분기 재무비율 + 현재 PER/PBR 반환
- **smoke 스크립트**: `docs/work/active/000074-valuation-analysis/live_kis_smoke.py`

### 버그 히스토리 (initial stub 결함 — 2026-04-24 수정)

1. **TR-ID 오매핑**: `FHKST66430100` 은 대차대조표 엔드포인트지 재무비율 아님. → `FHKST66430300` 으로 교정.
2. **`FID_DIV_CLS_CODE` 누락**: 필수 파라미터인데 빠져 있어 `OPSQ2001` 반환. → `"1"` 기본값 + 선택 가능.
3. **`output` 리스트 처리**: 분기별 다건 반환인데 dict 로 처리. → list 처리 + 최신 분기가 `[0]`.
4. **PER/PBR 소스 착각**: financial-ratio 에 있다고 가정했으나 실제로는 inquire-price (시세) 에만 존재. → 별도 엔드포인트로 분리.
