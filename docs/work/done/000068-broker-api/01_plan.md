# Plan: #68 브로커 API 커넥터 (KIS / Binance Futures) — **v2**

> v2 근거: plan-reviewer Critical 5 · Important 12 · Pre-mortem +5 · 범위 조정 전면 반영. Binance testnet 실측 + KIS/Binance 공식 문서 교차 검증 (`.omc/research/{kis,binance-futures}-api-facts.md`).
>
> **우선순위 재정렬**: 주식(KIS)이 주 목적, Binance Futures 는 패턴 검증 + 보조 채널. Step 깊이·테스트 투자를 KIS 쪽에 가중.

## AC 체크리스트

- [ ] **AC1**: Binance Futures testnet 으로 주문 → 체결 확인 (E2E)
- [ ] **AC2**: KIS 모의투자 연동 — 주문 → 체결 → 포지션 E2E (**축소 없음**, 통합 테스트 포함)
- [ ] **AC3**: 공통 `BrokerAdapter` 인터페이스 + 테스트 (단위·컴포넌트·통합)

## 0. 확정된 결정사항 (재논의 금지)

| # | 결정 | 근거 |
|---|------|------|
| Q1 | Binance testnet 사용 (실측 완료) | 사용자 키 발급, 실측 성공 |
| Q2 | KIS 모의투자 사용 (통합 테스트까지 풀스펙) | 사용자 키 발급, 주 목적 |
| Q3 | sync (requests + websocket-client) | async 는 **후행 이슈 #73** 분리 |
| Q4 | Futures 만 (Spot 제외) | Spot 은 후행 이슈 분리 |
| Q5 | Step 단위 묶음 커밋 (9개 그룹) | 사용자 결정 |

## 1. 실대조 요약 (플랜 근거 · 허구 없음)

### Binance USDS-M Futures (testnet 실측 + 공식 문서)

| 항목 | 실측/문서 값 | 플랜 반영 |
|------|-------------|-----------|
| 서버 시각 | 정상 응답 (drift ≤ 100ms) | NTP 보정 + `recv_window` 5s |
| Rate limit | weight **6000/min**, orders **1200/min · 300/10s** | 다중 버킷 rate limiter |
| BTCUSDT 필터 (2026-04 실측) | tick 0.10, step 0.0001, minNotional 100, PERCENT_PRICE ±5% | `symbol_filters.py` 캐시 + quantize — 값은 **런타임 `exchangeInfo` 에서 재조회** (하드코드 금지) |
| ETHUSDT 필터 (2026-04 실측) | tick 0.01, step 0.001, minNotional 20 | 심볼별 상이 + 시점별 변동 → 매 기동 시 `load_exchange_info()` 재조회 |
| orderTypes | LIMIT, MARKET, STOP, STOP_MARKET, TP, TP_MARKET, TRAILING_STOP_MARKET | 본 이슈는 LIMIT/MARKET, STOP 계열은 **Q6 로 후행 분리** |
| timeInForce | GTC, IOC, FOK, **GTX (post-only)**, **GTD** | `TimeInForce` enum 확장 (기존 DAY/IOC/FOK 에 GTX/GTD 추가) |
| newClientOrderId | regex `^[\.A-Z\:/a-z0-9_-]{1,36}$` | `client_id.py` 에 길이·패턴 테스트 |
| listenKey | 60분 수명, 24h 강제 disconnect | 30분 keepalive, 24h 사전 재접속 |
| error codes | -1021 (시각), -2010 (주문 거부), -4061 (positionSide mismatch), -4164 (minNotional), -2019 (margin 부족) | 에러 매트릭스 문서화 |
| testnet REST | **`https://testnet.binancefuture.com` (확정, 사용자 키 발급 포털 기준)** | env `BINANCE_BASE_URL` — 신 포털 `demo-fapi.binance.com` 은 후행 이슈에서 검증 |
| testnet WS | `wss://fstream.binancefuture.com/ws/<listenKey>` | env `BINANCE_WS_URL` |

### KIS (공식 GitHub + 공식 포털 교차 확인)

| 항목 | 값 |
|------|---|
| OAuth2 토큰 | `POST /oauth2/tokenP`, 수명 24h, **재발급 1분 1회** |
| 실전 base | `https://openapi.koreainvestment.com:9443` |
| 모의 base | `https://openapivts.koreainvestment.com:29443` |
| 주식 매수 TR_ID | 실전 `TTTC0802U` / 모의 `VTTC0802U` |
| 주식 매도 TR_ID | 실전 `TTTC0801U` / 모의 `VTTC0801U` |
| 정정/취소 TR_ID | 실전 `TTTC0803U` / 모의 `VTTC0803U` (Body `RVSE_CNCL_DVSN_CD`: 01=정정, 02=취소) |
| 잔고조회 TR_ID | 실전 `TTTC8434R` / 모의 `VTTC8434R` |
| 매수가능조회 | 실전 `TTTC8908R` / 모의 `VTTC8908R` |
| 주문 엔드포인트 | `POST /uapi/domestic-stock/v1/trading/order-cash` |
| 주문 Body 필수 | `CANO`, `ACNT_PRDT_CD`, `PDNO`, `ORD_DVSN`, `ORD_QTY`, `ORD_UNPR` |
| 주문 헤더 필수 | `authorization`(Bearer), `appkey`, `appsecret`, `tr_id`, `custtype`(P/B) |
| `hashkey` | 선택 (`POST /uapi/hashkey` 로 생성, 미전달도 접수됨) |
| ORD_DVSN | 00=지정가, 01=시장가, 02=조건부지정가, 03=최유리, 04=최우선, 05=장전시간외 |
| 응답 | `rt_cd` 0=성공, 1=실패, `msg_cd` 에러코드, `output.ODNO` 주문번호 |
| Rate limit | 실전 초당 20건 / **모의 초당 2건**, 초과 시 `EGW00201` |
| WS paper URL | `ws://ops.koreainvestment.com:31000` (체결통보) |
| WS live URL | `ws://ops.koreainvestment.com:21000` |
| WS 체결통보 TR_ID | 실전 `H0STCNI0` / 모의 `H0STCNI9` |
| WS tr_key | HTS ID (종목코드 아님) |
| WS 암호화 | **AES-256-CBC + PKCS7**, key/iv 는 구독 응답 `body.output.{key,iv}`, Base64 |
| WS 페이로드 | `^` 구분자, 23 필드 |
| WS 한도 | 세션당 종목 41개 + 체결통보 1건 |

**확인 실패 항목** (구현 시 공식 포털 Excel 재확인):
- ORD_DVSN 06/07 정확한 값
- KIS rate limit 잔량 헤더 존재 여부
- KIS WS wss:// 지원 여부 (현재는 ws:// 만 확인)
- Binance testnet 잔고 리셋 주기

## 2. 아키텍처 결정 (v2)

| 항목 | 결정 | 근거 |
|------|------|------|
| 동기성 | **sync** (requests + websocket-client) | Q3 |
| 정밀도 | **Decimal 전용** (가격·수량) — C1 대응 | 틱/스텝 라운딩 정확성 |
| `Fill` 타입 경계 | **`src/brokers/types.BrokerFill(Decimal)` 신규**, `execution` 으로 넘길 때만 기존 `Fill(float)` 변환 | C1: 기존 `src/execution/base.Fill` 이 float 기반이라 재사용 불가 |
| `TimeInForce` enum | **`src/execution/base.py::TimeInForce` 를 수정** — DAY/IOC/FOK 유지 + GTC 별칭(=DAY) + **GTX(post-only), GTD** 추가. 단일 enum 으로 유지 | I1: Broker 경계마다 별도 enum 이면 매핑 오염. 기존 execution 레이어는 DAY/IOC/FOK 만 사용하므로 backward-compat |
| `ensure_*` 호출 빈도 | **기동 시 1회 + 수동 `refresh_account_state()`**. 주문마다 재조회 안 함 | I-Imp-6: weight 소모 최소화 |
| Secret masking 범위 | 전역 `logging.root` 에 `SecretMaskingFilter` 부착 (기동 시 1회) | I-Imp-10 |
| Binance testnet URL | `https://testnet.binancefuture.com` (사용자 키 발급 포털 확정) | C4 |
| 스키마 검증 | **pydantic v2** + `json.loads(..., parse_float=Decimal)` | N1 |
| WS 라이브러리 | `websocket-client>=1.7,<2.0` + `cryptography>=42,<45` (KIS AES) | I5, I11 |
| 시크릿 | env + `python-dotenv`, **`SecretMaskingFilter`** 로그 필터 | I6 |
| 설정 | `config/broker.yml` | 운영 토글 분리 |
| 테스트 mock | `responses` (HTTP), in-process fake WS, `pytest.ini_options.markers` 등록 | I7 |
| 통합 테스트 | `pytest -m integration`, **기본 skip** (`addopts = "-m 'not integration'"`) | I7 |

## 3. 디렉토리 구조 (v2)

```
src/brokers/                               # 신규 모듈 루트
  __init__.py
  .ai.md                                   # Q3·Q4 결정 근거, Decimal 강제 이유, 단일가 핸들러 책임 경계
  types.py                                 # BrokerFill (Decimal) — C1 핵심
  base.py                                  # BrokerAdapter Protocol + 자료형
  errors.py                                # BrokerError 계층
  rate_limiter.py                          # named bucket 다중 모델 (I1)
  client_id.py                             # Binance 용 client_order_id 생성 (regex 검증)
  logging_filter.py                        # SecretMaskingFilter (I6)
  router.py                                # OrderRouter + active_broker 스왑 안전 프로토콜 (I8)
  config.py                                # env loader — 누락 시 명확한 오류 (I12)
  binance/
    __init__.py
    .ai.md
    rest.py                                # BinanceFuturesClient (HMAC, recv_window, time drift 보정)
    ws.py                                  # user data stream + ReconnectReconciler (C4)
    adapter.py                             # BinanceFuturesAdapter
    schemas.py                             # pydantic 응답 모델
    symbol_filters.py                      # exchangeInfo 캐시 + quantize (C3)
    error_map.py                           # -1021/-2010/-4061/-4164 → BrokerError 하위
  kis/                                     # **주 모듈 — 투자 가중**
    __init__.py
    .ai.md
    auth.py                                # OAuth2 (1분 1회 제한 rate limit, 디스크 캐시)
    rest.py                                # KISClient
    tr_ids.py                              # C5: PAPER_TR_IDS / LIVE_TR_IDS 상수 테이블
    ws.py                                  # 체결통보 WS
    crypto.py                              # AES-256-CBC + PKCS7 복호화 (I5)
    adapter.py                             # KISAdapter
    schemas.py
    error_map.py                           # rt_cd/msg_cd → BrokerError
config/
  broker.yml                               # active_broker, endpoints, rate_limits
.env.example                               # 사용자 네이밍 (HANTOO_*, BINANCE_*)
tests/
  test_broker_types.py                     # BrokerFill Decimal 정밀도
  test_broker_base.py                      # Protocol, errors, client_id, rate_limiter, logging_filter
  test_broker_router.py                    # config, active_broker 스왑, env 누락 오류
  test_broker_binance_rest.py              # 서명, 파라미터, error_map, symbol_filters, quantize
  test_broker_binance_ws.py                # WS fake → dispatch, reconnect, reconcile
  test_broker_binance_partial_fills.py     # I10: partial fill 시퀀스 (50/75/90/100%)
  test_broker_binance_idempotency.py       # retry, -2010 dup 매핑
  test_broker_kis_auth.py                  # 토큰 캐시·만료·재발급 1분 rate limit
  test_broker_kis_rest.py                  # paper/live TR_ID 분기, ORD_DVSN 매핑, error_map
  test_broker_kis_ws.py                    # 구독 응답 key/iv → AES256 복호화 → dispatch
  test_broker_kis_single_auction.py        # KRX 단일가 시각 fixture × policy
  integration/
    __init__.py
    conftest.py                            # integration marker, 시크릿 감지 fixture
    test_binance_testnet.py                # @pytest.mark.integration
    test_kis_paper.py                      # @pytest.mark.integration  **AC2 증거**
docs/specs/
  broker-adapter.md                        # 인터페이스 · 상태 머신 · 에러 매트릭스 · Idempotency 계약 · 시크릿 SOP
docs/onboarding/
  broker-runbook.md                        # 키 순환 · 장애 대응 · testnet↔live 전환 체크리스트
```

## 4. 공통 Protocol 설계 (v2, Critical 반영)

```python
# src/brokers/types.py — C1 핵심: execution 과 분리된 Decimal 기반 타입
from decimal import Decimal

@dataclass(frozen=True)
class BrokerFill:
    parent_id: str
    broker_order_id: str
    client_order_id: str
    trade_id: str                # (broker_order_id, trade_id) 조합으로 dedup
    qty: Decimal
    price: Decimal
    fee: Decimal
    fee_asset: str               # "USDT" / "KRW"
    ts: datetime
    is_maker: bool

# src/brokers/base.py
class OrderType(str, Enum):
    MARKET = "MARKET"
    LIMIT  = "LIMIT"
    # STOP / TP 계열은 본 이슈 범위 외 (Q6)

class PositionSide(str, Enum):   # C2
    BOTH  = "BOTH"               # one-way
    LONG  = "LONG"               # hedge
    SHORT = "SHORT"              # hedge

class MarginType(str, Enum):     # C2
    ISOLATED = "ISOLATED"
    CROSSED  = "CROSSED"

@dataclass
class OrderRequest:
    client_order_id: str         # 호출자(전략) 결정론적 부여
    symbol: str
    side: Side                   # src.execution.base.Side 재사용 (enum 만 공유)
    qty: Decimal
    order_type: OrderType
    price: Decimal | None        # MARKET 이면 None
    tif: TimeInForce             # 확장된 enum: DAY/IOC/FOK/GTX/GTD
    position_side: PositionSide = PositionSide.BOTH    # C2
    reduce_only: bool = False                          # C2 (Futures 전용; 헤지모드에서 금지)
    close_position: bool = False                       # C2
    emergency_exit: bool = False                       # I9: kill switch 우회 (reduceOnly 와 의미 분리)

class BrokerAdapter(Protocol):
    name: str
    paper: bool

    def place_order(self, req: OrderRequest) -> OrderAck: ...
    def cancel_order(self, *, broker_order_id=None, client_order_id=None, symbol: str) -> None: ...
    def get_order(self, *, broker_order_id=None, client_order_id=None, symbol: str) -> OrderAck: ...
    def get_positions(self, symbol: str | None = None) -> list[Position]: ...
    def get_balance(self) -> list[Balance]: ...
    def stream_fills(self, on_fill: Callable[[BrokerFill], None]) -> Closeable: ...

    # C2: 계정 상태 사전 설정 (주문 전 요구 상태로 이동)
    def ensure_leverage(self, symbol: str, leverage: int) -> None: ...
    def ensure_margin_type(self, symbol: str, mode: MarginType) -> None: ...
    def ensure_position_mode(self, *, hedge: bool) -> None: ...     # P12 방어

    # P11: 브로커 헬스체크
    def health_check(self) -> HealthStatus: ...
```

- `place_order` 진입에 `KillSwitch.assert_allow_order(liquidation=req.emergency_exit)` 게이트
- KIS 에서는 `position_side`, `reduce_only`, `close_position` 은 무시 (지원 안 함 — `BrokerError` 대신 warning)
- `ensure_*` 는 idempotent: 현재 상태 조회 → 일치하면 no-op, 불일치 시 변경. P12 에서 운영자 개입 필요하면 `BrokerStartupError` raise

## 5. 단계별 실행 순서 (v2, TDD)

### Step 0 — 부트스트랩
- `docs/specs/broker-adapter.md` v1 작성 (상태 머신, 에러 매트릭스, Idempotency 계약, 레이트리밋 모델)
- `docs/onboarding/broker-runbook.md` v1 (운영자 관점)
- `src/brokers/.ai.md`, `binance/.ai.md`, `kis/.ai.md` 초안
- `pyproject.toml` 의존성: `websocket-client>=1.7,<2.0`, `cryptography>=42,<45`
- `pyproject.toml` `[tool.pytest.ini_options]`: `markers = ["integration: requires network/secrets"]`, `addopts = "-m 'not integration'"` (I7)
- `.env.example` — **사용자 네이밍 그대로** (E 섹션)

### Step 1 — 공통 타입·Protocol·infra (AC3 진전)
**Red → Green → Refactor**
1. `test_broker_types.py` — BrokerFill Decimal 정밀도, `execution.Fill` 변환 경계
2. `test_broker_base.py` — Protocol 메서드 명세, BrokerError 계층, kill switch 게이팅
3. `test_broker_base.py::rate_limiter_*` — named bucket 다중 (I1): `acquire("weight", cost=5)`, `acquire("orders_1m", cost=1)`
4. `test_broker_base.py::client_id_*` — Binance regex `^[\.A-Z\:/a-z0-9_-]{1,36}$` 통과·실패 케이스, 길이 36 경계
5. `test_broker_base.py::secret_masking_*` (I6) — api_key/secret/signature/authorization/appkey/appsecret/hashkey/cano/approval_key/access_token/Bearer 토큰 마스킹
6. `config.py` env loader — 누락 시 명확한 오류 (I12)

### Step 2 — Binance REST adapter (AC1 진전)
1. `test_broker_binance_rest.py::test_signature_matches_official_example` — 공식 문서 예제 fixture 로 HMAC SHA256 검증
2. `test_broker_binance_rest.py::test_time_drift_recovery` — fake 시계 +3s → 자동 `GET /fapi/v1/time` offset 보정 (I2, Pre-mortem P1)
3. `place_order`/`cancel_order`/`get_order` LIMIT/MARKET + `positionSide`, `reduceOnly`, `closePosition` 파라미터 매핑
4. `ensure_leverage`, `ensure_margin_type`, `ensure_position_mode` (C2, P12)
5. `error_map.py` — -1021/-2010/-2011/-2013/-2019/-4061/-4164 → `BrokerError` 하위 매핑 + 단위 테스트

### Step 2.5 — Symbol filters (C3 Critical)
1. `test_broker_binance_rest.py::test_load_exchange_info_caches_filters` — `GET /fapi/v1/exchangeInfo` 응답 fixture 를 test-time 에 캡처, **하드코드 금지**
2. `test_broker_binance_rest.py::test_quantize_respects_tick_and_step` — 테스트 파라미터는 fixture 의 filters 에서 주입 (`tick`, `step` 동적). BTCUSDT/ETHUSDT 의 2026-04 실측값은 참고 주석
3. `test_broker_binance_rest.py::test_reject_below_min_notional` — 경계는 fixture 의 `MIN_NOTIONAL.notional` 기준 `notional ± step*price` 으로 계산 (런타임). 하드코드 100/99.99 금지
4. `test_broker_binance_rest.py::test_reject_price_outside_percent_price` — PERCENT_PRICE `multiplierUp/Down` 을 fixture 에서 읽어 ±경계 검증
5. `symbol_filters.py` 구현 — 기동 시 `GET /fapi/v1/exchangeInfo` 호출, TTL 1h 캐시, `quantize_price(symbol, price) → Decimal`, `quantize_qty(symbol, qty) → Decimal`, `min_notional(symbol) → Decimal`

### Step 3 — Binance WS + Reconciler (AC1 진전, C4 Critical)
1. `test_broker_binance_ws.py::test_listen_key_lifecycle` — 60분 수명, 30분 keepalive, 실패 시 재발급
2. `test_broker_binance_ws.py::test_order_trade_update_dispatches_fill` — fixture: `ORDER_TRADE_UPDATE{x=TRADE, X=PARTIALLY_FILLED}`
3. `test_broker_binance_ws.py::test_rest_ack_before_ws_new_is_source_of_truth` — C4: 순서 비결정성
4. `test_broker_binance_ws.py::test_reconnect_reconciles_open_orders` — C4: 재연결 직후 `get_order(open since last_event_ts - 10s)` 로 덮어씀
5. `test_broker_binance_partial_fills.py` (I10) — 50% → 75% → 90% → 100% 순차 fill, 누적 qty·avg_price·수수료 집계 정확성
6. `ws.py` + `reconciler.py` 구현: (broker_order_id, trade_id) dedup, 24h 사전 재접속

### Step 4 — Idempotency · 메트릭 · kill switch (AC1, AC3)
1. `test_broker_binance_idempotency.py::test_duplicate_client_order_id_returns_existing`
2. `test_broker_binance_idempotency.py::test_network_timeout_retry_does_not_duplicate` — `get_order(client_order_id)` 로 상태 조회 후 결정
3. `test_broker_base.py::test_kill_switch_blocks_normal_but_allows_emergency_exit`
4. `Metrics` 주입 (`qta_orders_total{broker="binance_futures"}`, `order_latency_seconds`, `risk_breach_total{rule="kill_switch_blocked"}`, `risk_breach_total{rule="margin_danger"}` — P13)

### Step 4.5 — 관측성 강화 (I6, P11)
1. `test_broker_base.py::test_secret_masking_in_http_logs` — URL query string (signature=), headers, body, KIS access_token·hashkey
2. `test_broker_base.py::test_health_check_trips_kill_switch_on_unhealthy` (P11)
3. `Metrics.qta_risk_breach_total{rule="broker_unhealthy"}` 추가

### Step 5 — OrderRouter (AC3)
1. `test_broker_router.py::test_loads_active_broker_from_yml`
2. `test_broker_router.py::test_swap_active_requires_cancel_all_and_position_snapshot` (I8)
3. `router.py` 구현 — `swap_active()` 에서 (1) `cancel_all_open()`, (2) `get_positions()` 스냅샷 비교, (3) feature flag `BROKER_ROUTER_ENABLED` 로 점진 롤아웃 (P15)
4. `test_broker_router.py::test_missing_secrets_raises_specific_error` (I12)

### Step 6 — **KIS Adapter (주 모듈, AC2 진전, C5 Critical)**
v2 는 KIS 에 가장 많은 투자. Binance 에 없는 KIS 전용 리스크 모두 커버.
1. `test_broker_kis_auth.py`:
   - 토큰 발급 24h 캐시 (디스크, 재시작 시 재사용)
   - 만료 5분 전 사전 갱신
   - **1분 1회 제한** rate limiter (P4 방어)
   - 일일 한도 초과 시 명확한 오류
2. `test_broker_kis_rest.py::test_tr_id_paper_vs_live_mapping` (C5 Critical):
   - `KISAdapter(paper=True)` → `VTTC0802U` (매수), `VTTC0801U` (매도), `VTTC8434R` (잔고)
   - `paper=False` → `TTTC*` 계열
   - 잘못된 조합 호출 시 `ConfigurationError`
3. `test_broker_kis_rest.py::test_order_params_schema`:
   - **`HANTOO_CREDIT_NUMBER` 파서 (C3 확정)**: regex `^[0-9]{8}-[0-9]{2}$` 검증, 하이픈 split → `CANO = parts[0]`, `ACNT_PRDT_CD = parts[1]`
   - 예: `"12345678-01"` → `CANO="12345678"`, `ACNT_PRDT_CD="01"`
   - 포맷 위반(하이픈 없음, 자리수 부족, 숫자 외 문자) → `ConfigurationError` 명확한 메시지
   - ORD_DVSN 매핑: MARKET→"01", LIMIT→"00", 조건부지정가→"02", 최유리→"03", 최우선→"04" (공식 포털 Excel 재확인 — Q10 과 연계)
   - 헤더 `custtype="P"` 개인
4. `test_broker_kis_rest.py::test_hashkey_optional` — 비필수지만 생성 경로 검증
5. `test_broker_kis_rest.py::test_error_map` — `rt_cd="1"` + `msg_cd` → `BrokerError` 매핑 테이블:
   - `EGW00201` → `RateLimitError`
   - 잔고 부족·장외시간·호가단위 위반 등
6. `test_broker_kis_rest.py::test_krx_tick_size_quantize`:
   - 가격대별 호가단위 테이블 (1원/5원/10원/50원/100원/500원/1000원)
   - 호가단위 미준수 시 사전 거부

### Step 6.5 — KIS WS + AES 복호화 (I5 Critical)
1. `test_broker_kis_ws.py::test_subscribe_returns_aes_key_iv`:
   - 구독 응답 `body.output.{key,iv}` 추출
2. `test_broker_kis_ws.py::test_decrypt_payload_with_aes256_cbc_pkcs7` (I5):
   - fixture: 공식 샘플 암호화 페이로드 → 평문 검증
   - `cryptography.hazmat.primitives.ciphers` 로 AES-256-CBC + PKCS7 unpad
3. `test_broker_kis_ws.py::test_parses_caret_delimited_23_fields`:
   - 실전 페이로드 fixture (23 필드 `^` 구분)
   - `체결통보 스키마` → `BrokerFill` 변환
4. `test_broker_kis_ws.py::test_hts_id_as_tr_key`:
   - `tr_key` 필드에 HTS ID 전달 (종목코드 아님 — 함정)
5. `test_broker_kis_ws.py::test_paper_vs_live_url_and_tr_id`:
   - paper: `ws://ops.koreainvestment.com:31000` + `H0STCNI9`
   - live: `ws://ops.koreainvestment.com:21000` + `H0STCNI0`
6. `test_broker_kis_ws.py::test_session_limit_warning`:
   - 세션당 종목 41개 + 체결통보 1건 한도 초과 시 warning
7. `test_broker_kis_single_auction.py` (N4 수용):
   - 단일가 게이팅은 **execution 레이어** 에서 호출 (KISAdapter 직접 `KRXSingleAuctionHandler` 호출 안 함)
   - 09:00 전·종가 단일가·halt fixture × WAIT/PARTICIPATE/CANCEL 정책

### Step 7 — 통합 테스트 (AC1 + AC2 증거)
1. `tests/integration/test_binance_testnet.py`:
   - env `BINANCE_DEMO_API_KEY`, `BINANCE_DEMO_SECRET_API_KEY` 존재 시만 실행 (`integration/conftest.py::binance_creds` fixture)
   - **주문 qty 런타임 계산**: `mark_price = get_mark_price(symbol)`, `min_notional = symbol_filters.min_notional(symbol)`, `step = symbol_filters.lot_step(symbol)`, `qty = max(ceil(min_notional / mark_price / step) * step, symbol_filters.min_qty(symbol))` — BTCUSDT 가격이 높아 `0.001 BTC` 하드코드 시 minNotional 미달 가능성 있어 금지
   - **주문 가격 런타임 계산**: `price = quantize_price(symbol, mark_price * 0.999)` (마크가 -0.1%, tick 정렬), PERCENT_PRICE ±5% 범위 내 보장
   - LIMIT 주문 → cancel → `stream_fills` 5초 내 `ORDER_TRADE_UPDATE{X=CANCELED}` 수신
   - 포지션·잔고 snapshot 비교, testnet 잔고 리셋 시 skip + 경고 (`insufficient_margin` 응답 감지)
2. `tests/integration/test_kis_paper.py` (**AC2 증거 — 축소 없음**):
   - env `HANTOO_FAKE_API_KEY`, `HANTOO_FAKE_SECRET_API_KEY`, `HANTOO_CREDIT_NUMBER` 존재 시만 실행 (`integration/conftest.py::kis_paper_creds` fixture)
   - **KRX 장시간 skipif**: `@pytest.mark.skipif(not _is_krx_open(), reason="KRX closed")` — KIS 모의투자 체결이 실전 시세에 연동되므로 장외 시 fill 테스트 불가
   - `_is_krx_open()` helper (`integration/conftest.py`): `zoneinfo("Asia/Seoul")` 기준 평일 09:00~15:30 (공휴일 판정은 본 이슈 범위 외 — KRX 휴장일 테이블은 후행 이슈)
   - OAuth 토큰 발급 (재발급 1분 룰 감안 — fixture `scope="session"` + 디스크 캐시 재사용)
   - 삼성전자(005930) 지정가 매수 → 주문번호 수신 → 정정/취소
   - 잔고조회 · 매수가능조회
   - WS 체결통보 구독 → AES 복호화 → fill 1건 이상 수신 (타임아웃 30초, 미수신 시 xfail 표시 — 장중에도 체결 지연 가능)
   - CI nightly 는 **KST 09:30 (UTC 00:30 월~금) cron** 으로 조정 필요 (후속 CI 작업, 본 이슈 코드 범위 외)

### Step 8 — 메트릭·문서 최종화
1. `src/observability/metrics.py` 에 `qta_open_orders{broker,symbol}` gauge 추가 검토 (N8)
2. `docs/specs/observability.md` 에 `broker` 라벨 값 표준화 (N9): `binance_futures_paper`, `binance_futures_live`, `kis_paper`, `kis_live`
3. `docs/specs/broker-adapter.md` 최종화 — 상태 머신 다이어그램 (NEW → PARTIALLY_FILLED → FILLED / CANCELED / REJECTED / EXPIRED), 에러 매트릭스
4. `docs/onboarding/broker-runbook.md` 최종 — 아래 7개 시나리오 구체화 (I-Imp-11):
   1. **KIS 토큰 일일 발급 초과** → 캐시 삭제 → 1시간 대기 → 재발급 순서 (수동 절차)
   2. **Binance testnet 잔고 리셋** → 재충전 감지 → env 재입력 필요성 판정
   3. **listenKey 분실 / WS 24h forced disconnect** → 재발급 + WS 재접속 트리거, `get_order` reconcile 범위
   4. **KIS WS 세션 41개 한도 도달** → 기존 구독 drop 우선순위 (오래된 종목 먼저), 체결통보 1건 보장
   5. **env 오타로 CANO 파싱 실패** → `ConfigurationError` 메시지 스펙 (어느 env 인지, 기대 포맷 명시)
   6. **kill switch trip → release 절차** → 운영자 확인 체크리스트 (포지션 스냅샷, 미체결 주문, 손실 원인)
   7. **paper → live 전환 체크리스트** → 심볼 whitelist, 주문 한도, kill switch 상태, 시크릿 분리, feature flag `BROKER_ROUTER_ENABLED`, CI cron (KST 09:30) 조정
   8. **KIS 통합 테스트 실행 윈도우** (C5): 평일 KST 09:00~15:30, 공휴일 제외 (공휴일 자동 판정은 후행 이슈)

### Step 8.5 — .ai.md 갱신 (CLAUDE.md 불변식)
- `src/brokers/.ai.md`, `binance/.ai.md`, `kis/.ai.md` 에 v2 결정사항 반영
- `docs/work/active/000068-broker-api/00_issue.md` AC 체크 + 작업 내역

## 6. 시크릿 / 설정 (사용자 네이밍)

`.env.example`:
```dotenv
# ── Binance Futures (DEMO = testnet 키, 실전 금지) ────────
BINANCE_DEMO_API_KEY=                # testnet.binancefuture.com 에서 발급
BINANCE_DEMO_SECRET_API_KEY=         # testnet secret
BINANCE_BASE_URL=https://testnet.binancefuture.com    # live (운영 시): https://fapi.binance.com
BINANCE_WS_URL=wss://fstream.binancefuture.com/ws     # live: wss://fstream.binance.com/ws
# (운영 전환 시 BINANCE_REAL_API_KEY 등을 별도 추가 — 본 이슈에서는 사용 안 함)

# ── KIS (한국투자증권, 사용자 네이밍 = HANTOO_*) ──────────
# 모의투자
HANTOO_FAKE_API_KEY=                # paper App Key
HANTOO_FAKE_SECRET_API_KEY=         # paper App Secret
HANTOO_CREDIT_NUMBER=12345678-01    # 포맷 확정: 8자리 숫자 + "-" + 2자리 숫자 (regex ^[0-9]{8}-[0-9]{2}$)
                                    # 하이픈 split → CANO (앞 8), ACNT_PRDT_CD (뒤 2, 보통 "01")
# 실전투자 (운영 전까지 사용 금지)
HANTOO_REAL_API_KEY=
HANTOO_REAL_SECRET_API_KEY=

# ── 공통 토글 ───────────────────────────────────────────
ACTIVE_BROKER=kis                   # kis | binance_futures
BROKER_ROUTER_ENABLED=false         # P15: router 점진 롤아웃 flag
```

`config/broker.yml`:
```yaml
active_broker: kis                  # 주식이 주 목적
brokers:
  binance_futures:
    paper: true
    rate_limit:
      weight_per_minute: 6000       # 실측
      orders_per_minute: 1200       # 실측
      orders_per_10s: 300           # 실측
    recv_window_ms: 5000
    position_mode: one-way          # hedge 는 운영 결정 후 변경
    default_margin_type: ISOLATED
  kis:
    paper: true
    rate_limit:
      orders_per_sec: 2             # 모의 한도 (live: 20)
      oauth_per_minute: 1           # 토큰 발급 제한
    single_auction_policy: WAIT
    token_cache_path: .omc/state/kis_token.json
```

## 7. Guardrails (v2)

### Must Have
- `client_order_id` 호출자 결정론적 부여 + Binance regex 검증
- **Decimal 전용** (가격·수량) — 브로커 경계에서 `BrokerFill`, execution 경계에서만 기존 `Fill(float)` 로 변환 (C1)
- Secret env 만, `SecretMaskingFilter` 로 모든 HTTP/WS 로그 마스킹 (I6)
- `paper`/`live` 명시적 플래그, **기본 `paper=True`**
- 단위 테스트 네트워크 호출 0 (responses + WS fake)
- `KillSwitch.assert_allow_order` 진입 게이트
- 모든 주문 경로 메트릭 기록 (broker 라벨 표준화 N9)
- `.ai.md` 신규 디렉토리 필수 (CLAUDE.md 불변식)
- Binance: `ensure_position_mode/leverage/margin_type` 주문 전 1회 확인 (P12)
- KIS: **AES-256-CBC PKCS7** 복호화 · TR_ID paper/live 분리 (C5, I5)
- KIS: **토큰 1분 1회·24h 캐시** · 재발급 spike 방어 (P4)
- WS: (broker_order_id, trade_id) dedup, 재연결 시 `get_order` reconcile, 24h 사전 재접속 (C4)
- 기동 시 `health_check` + 주기 (1분), 임계치 초과 시 kill switch 자동 trip (P11)
- Pytest markers 등록 + integration 기본 skip (I7)

### Must NOT Have
- LLM 호출 (CLAUDE.md 불변식)
- 단위 테스트의 실제 네트워크 호출
- 시크릿 하드코딩 또는 평문 커밋
- 자동 commit/push
- float 가격·수량
- 조용한 예외 흡수 (`except: pass`)
- WS 이벤트 단독 신뢰 (REST reconcile 없이)
- `reduceOnly` 와 `emergency_exit` 혼동 (I9)

## 8. Pre-mortem (v2, 10 → 15 시나리오)

기존 10건 유지 + 추가 5건:

| # | 시나리오 | 방어 |
|---|---------|------|
| P1 | Binance HMAC 미스매치 (-1021) | 공식 예제 fixture 서명 테스트 + time drift 자동 보정 |
| P2 | listenKey 만료 → WS 끊김 | 30분 keepalive, 실패 시 재발급, `get_order` 폴링 reconcile |
| P3 | KIS 토큰 일일 한도 초과 | 디스크 캐시, 만료 5분 전 사전 갱신 |
| P4 | WS 재연결 구간 체결 누락 | 시작·주기 `get_order` reconcile + dedup |
| P5 | Rate limit 초과 계정 차단 | 실측 버킷 + 응답 헤더 동적 차감, 90% 도달 시 정지 |
| P6 | KRX 단일가 구간 주문 | execution 레이어 단일가 게이팅 + 통합 테스트 시각 fixture |
| P7 | client_order_id 충돌 | (strategy, symbol, side, ts) 해시 + 36자·regex 검증 |
| P8 | Decimal/float 혼용 | pydantic Decimal 강제, JSON `parse_float=Decimal` |
| P9 | 시크릿 로그 노출 | `SecretMaskingFilter` 10개 키 패턴, URL query 치환 |
| P10 | kill switch 우회 | `emergency_exit` 별도 명명, 통합 테스트 정상 주문 False 강제 |
| **P11** | **거래소 정기/긴급 점검 중 주문** | `health_check()` 주기 호출, 임계 초과 시 kill switch trip + `broker_unhealthy` 메트릭 |
| **P12** | **포지션 모드 mismatch (one-way↔hedge)** | `ensure_position_mode` 기동 시 검증, 자동 수정 금지 — `BrokerStartupError` |
| **P13** | **격리 마진 청산 임박** | `get_positions` 에 liquidation_price·margin_ratio 포함, 90% 초과 경보 |
| **P14** | **서버 시각 drift** | 기동 시 `GET /fapi/v1/time` + 15분 재측정, `recv_window` 5s, -1021 자동 재시도 1회 |
| **P15** | **배포 중 반쯤 마이그레이션** | feature flag `BROKER_ROUTER_ENABLED`, 체결 이벤트 `source_version` 태그, 롤백 SOP 문서 |

## 9. 테스트 매트릭스 (v2)

| 레벨 | 도구 | 범위 | CI |
|------|------|------|----|
| 단위 | pytest + responses + WS fake | types/base/errors/rate_limiter/client_id/secret_masking/router/config, 각 adapter REST 서명·파라미터·quantize·error_map, KIS crypto | **항상 실행** |
| 컴포넌트 | pytest + WS fake | adapter↔ws 통합, kill switch 게이팅, idempotency, partial fill 시퀀스, reconnect reconcile, health check | **항상 실행** |
| 통합 | pytest -m integration | Binance testnet E2E, **KIS paper E2E (AC2)** | **nightly only**, default skip |
| 관측성 | metric 카운터·게이지 단언 | 주문 경로 전부 메트릭, broker 라벨 표준화 | 단위 포함 |

## 10. 단계별 커밋 단위 (Step 묶음, 사용자 선택)

1. **Step 0** `chore(brokers): bootstrap spec + deps + .ai.md + pytest markers`
2. **Step 1** `feat(brokers): add BrokerAdapter Protocol + types + rate limiter + client_id + secret masking`
3. **Step 2 + 2.5** `feat(brokers/binance): add REST adapter + symbol filters + ensure_* + error map`
4. **Step 3** `feat(brokers/binance): add WS + reconciler + partial fill + reconnect recovery`
5. **Step 4 + 4.5** `feat(brokers): integrate idempotency + kill switch + metrics + health check`
6. **Step 5** `feat(brokers): add OrderRouter + config + active_broker swap protocol`
7. **Step 6 + 6.5** `feat(brokers/kis): add paper adapter + OAuth + TR_ID mapping + AES256 WS + KRX tick sizes`
8. **Step 7** `test(brokers): add Binance testnet + KIS paper integration (AC1+AC2 evidence)`
9. **Step 8 + 8.5** `docs(brokers): finalize spec + runbook + .ai.md`

각 커밋은 사용자 승인 후 실행.

## 11. 리뷰 대응 체크리스트 (v1 리뷰 → v2)

### 🔴 Critical
- [x] C1: `BrokerFill(Decimal)` 별도 정의 → `types.py`
- [x] C2: `OrderRequest` 에 `position_side/reduce_only/close_position` 추가 + `ensure_*` 메서드
- [x] C3: Step 2.5 `symbol_filters.py` + quantize
- [x] C4: `ReconnectReconciler` + (broker_order_id, trade_id) dedup
- [x] C5: `tr_ids.py` paper/live 상수 테이블

### 🟡 Important (12건 전부 수용)
- [x] I1 named bucket rate limiter · I2 time drift 자동 보정 · I3 OrderType Q6 명시 · I4 client_order_id 길이/regex · I5 AES-256-CBC + PKCS7 · I6 SecretMaskingFilter · I7 markers 등록 · I8 swap_active 프로토콜 · I9 `emergency_exit` 명명 · I10 partial fill 시퀀스 · I11 의존성 상한 · I12 config env loader

### 🟢 Nice to have (선택 수용)
- [x] N1 Decimal 직렬화 · N3 상태 머신 다이어그램 · N4 단일가 게이팅 execution 레이어 · N8 `qta_open_orders` gauge 검토 · N9 broker 라벨 표준화
- [ ] N2/N5/N6/N7 (저우선, 필요 시 후행)

### Pre-mortem +5
- [x] P11~P15 전부 반영

## 12. 미해결 질문 (v2)

| # | 상태 | 내용 |
|---|------|------|
| Q1 | ✅ 확정 | Binance testnet 사용 |
| Q2 | ✅ 확정 | KIS 모의투자 풀스펙 (축소 없음) |
| Q3 | ✅ 확정 | sync, async → 이슈 #73 |
| Q4 | ✅ 확정 | Futures 만, Spot 후행 |
| Q5 | ✅ 확정 | Step 단위 묶음 |
| Q6 | 대기 | STOP_MARKET/STOP_LIMIT/TP/TRAILING 는 본 이슈 범위 외 — 후행 이슈 생성 시점 (Step 7 후?) |
| Q7 | 대기 | KIS 포지션 모드 (현물이라 hedge 개념 무관, 당분간 BOTH only) |
| Q8 | 대기 | 심볼 whitelist 도입 시점 (운영 넘어가기 직전 live 에만) |
| Q9 | 대기 | feature flag `BROKER_ROUTER_ENABLED` 초기값 (false → 점진 롤아웃) |
| Q10 | 대기 | KIS WS wss:// 지원 여부 — 구현 시 공식 포털 재확인 |

## 13. 참고

- 배경: `docs/background/10-broker-api-comparison.md`
- 실행 알고리즘 스펙: `docs/specs/execution-algorithms.md`
- 기존 실행 코드: `src/execution/`
- Kill switch: `src/ops/kill_switch.py`, `docs/specs/kill-switch-dr.md`
- 메트릭: `src/observability/metrics.py` (`broker` 라벨 기존)
- 실대조: `.omc/research/kis-api-facts.md`, `.omc/research/binance-futures-facts.md`
- v1 리뷰: plan-reviewer (Critical 5 · Important 12 · Pre-mortem +5)
- 후행 이슈: #73 (async 마이그레이션), 향후 생성 예정 (STOP/TP, Binance Spot, secret manager 통합)
- 선행 이슈: #67 (백테스트 MVP) — 완료됨
