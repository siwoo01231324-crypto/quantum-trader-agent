---
type: research
id: 34-patents-execution-algos
name: "실행 알고리즘 특허 조사 (TWAP·VWAP·SOR·ML 실행 최적화)"
sources:
  - https://patents.google.com/patent/US8571967B1/en
  - https://patents.google.com/patent/US11164248B2/en
  - https://patents.google.com/patent/US20210272201A1/en
  - https://patents.google.com/patent/US12067619B1/en
---

# 실행 알고리즘 특허 조사 (TWAP·VWAP·SOR·ML 실행 최적화)

> ⚠️ **법적 고지**: 본 노트는 학술·회피설계 목적 조사이며 변리사 리뷰가 아님.
> 상용 서비스 전 법무 검토 필수.
> 관련 노트: [[07-market-microstructure-basics]], [[10-broker-api-comparison]], [[08-strategy-paradigms]] — 우리 시스템 강화 및 침해 리스크 제거 목적.

---

## 1. 조사 범위

| 항목 | 내용 |
|------|------|
| 키워드 | TWAP, VWAP, Smart Order Routing (SOR), 슬리피지 예측, implementation shortfall, market impact, 주문 분할, ML 실행 최적화 |
| CPC 분류 | G06Q40/04 (거래·교환), G06N20/00 (ML), G06Q40/0421 (AI 거래) |
| 조사 기간 | 2010년 이후 공개, 유효 또는 최근 만료 특허 우선 |
| 조사 DB | Google Patents, USPTO |
| 조사 특허 수 | 4건 |
| 현재 코드 연결 | `src/execution/twap.py`, `src/execution/vwap.py`, `src/brokers/router.py` |

---

## 2. 특허 1 — US8571967B1

### 2.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US8571967B1 |
| 제목 | System and Method for Algorithmic Trading Strategies |
| 출원인 | Goldman Sachs & Co LLC |
| 공개일 | 2013-10-29 |
| 출원일 | 2010-05-18 |
| 법적 상태 | **만료** (2026-12-12 만료 예정 — Expired - Fee Related) |
| CPC | G06Q40/04 |
| URL | https://patents.google.com/patent/US8571967B1/en |

### 2.2 청구항 핵심 요약

독립항 핵심 구성요소:

- **(a) 역사적 거래량 패턴 분석**: 종목별 과거 체결량 분포를 분석해 최적 실행 궤적(execution trajectory) 결정
- **(b) VWAP 목표 추적**: 실행 평균가가 VWAP에 수렴하도록 슬라이스 타이밍·수량 동적 조정
- **(c) 기술 지표 기반 궤적 조정**: 단기 기술 지표(예: 모멘텀)를 이용해 슬라이스 타이밍 미세 조정
- **(d) 스프레드 포착 최대화**: 통계적 방법으로 스프레드 포착(spread capture) 확률 극대화
- **(e) 볼륨 제약 모니터링**: 주문이 시장 거래량 대비 과도한 비율을 차지하지 않도록 제한

### 2.3 💎 강화 제안 — VWAP 볼륨 프로파일 동적 갱신

**제안 이름**: 실시간 당일 체결량 피드백으로 VWAP 볼륨 프로파일 자동 재계산

**적용 대상 파일/함수**: `src/execution/vwap.py::VWAPAlgo._emit_next()` 및 `VWAPAlgo.__init__(volume_profile)`

**접목 방법**: 현재 `VWAPAlgo`는 초기화 시 `volume_profile: list[float]`를 정적으로 주입받아 전체 실행 기간 동안 고정 비율로 사용한다. 특허 (a)+(b) 구성요소에서 착안하여, 실행 중 `on_market_tick(tick)` 콜백에서 누적 체결량(cumulative_volume)을 받아 남은 슬라이스의 비율 벡터를 Bayesian update 방식으로 재계산하는 `live_volume_updater` 매개변수를 추가할 수 있다. 구체적으로 `VWAPAlgo.on_market_tick(tick)` 시그니처에 `realized_volume: int` 파라미터를 추가하고, 남은 슬라이스의 `weights[idx:]`를 `(역사적 비율 × α) + (당일 실시간 비율 × (1-α))`로 블렌딩한다(`α`는 `algo_params["vwap_alpha"]`로 설정). 이는 특허 (a)(b) 구성요소를 **수식·파라미터를 다르게 구현하여** 회피하면서도 실질적 성능 개선을 달성한다.

**기대 효과**: 장중 예상치 못한 거래량 스파이크(VI 발동, 공시 전후) 시 남은 슬라이스를 자동으로 재분배하여 실제 VWAP 벤치마크 대비 추적 오차(tracking error)를 줄인다. KRX 환경에서 동시호가·VI 발동 구간([[07-market-microstructure-basics]] §4)에 특히 효과적.

**저비용 검증 경로**: `tests/test_vwap_live_update.py`에서 고정 profile vs. 동적 갱신 비교 백테스트 — 슬리피지 감소율 측정.

### 2.4 차용 아이디어 메모

스프레드 포착(spread capture) 통계 모델: 슬라이스를 지정가(post-only)로 먼저 제출하고, 미체결 시 IOC 시장가로 전환하는 2단계 로직. `src/execution/limit.py`와 연계 시 수수료 절감 가능.

### 2.5 회피 필요 영역

| 구성요소 | 회피 설계 |
|----------|----------|
| (a) 역사적 거래량 패턴으로 궤적 결정 | 우리는 외부 거래량 데이터 대신 **당일 실시간 누적 체결량**을 primary signal로 사용 — 정적 역사적 패턴만의 독점적 활용 아님 |
| (b) VWAP 수렴 슬라이스 조정 | Goldman 특허는 단일 종목·단일 파라미터 세트 구조. 우리는 전략 레벨 `algo_params` dict로 종목별 파라미터를 분리 → 모듈 경계 다름 |
| (e) 볼륨 제약 | 이미 특허 만료(2026-12 예정)로 청구항 자유 활용 가능해질 예정 |

### 2.6 우리 코드 연결고리

- `src/execution/vwap.py` — `VWAPAlgo` 구현 (현재: 정적 volume_profile)
- `src/execution/base.py::ParentOrder.algo_params` — VWAP 파라미터 전달 dict
- `src/brokers/router.py::OrderRouter` — VWAP child order 라우팅 게이트

---

## 3. 특허 2 — US11164248B2

### 3.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US11164248B2 |
| 제목 | Multi-modal trade execution with smart order routing |
| 출원인 | CME Group Inc (Chicago Mercantile Exchange Inc) |
| 공개일 | 2021-11-02 |
| 출원일 | 2016-04-19 |
| 법적 상태 | **유효** (2039-07-15 만료) |
| CPC | G06Q40/04 |
| URL | https://patents.google.com/patent/US11164248B2/en |

### 3.2 청구항 핵심 요약

독립항 핵심 구성요소:

- **(a) 이중 매칭 엔진**: 동일 금융상품에 대해 FIFO 방식과 Pro Rata 방식 두 개의 독립적 오더북 유지
- **(b) 스마트 오더 라우팅**: 가격·수량 가용성 기반으로 최적 실행 플랫폼에 주문 자동 배분
- **(c) Implied Order 기능**: 복수 오더북에 걸쳐 유동성을 합성하여 통합 시장 접근 제공
- **(d) 동적 알고리즘 전환**: 시장 상태·거래 시간 등 파라미터에 따라 단일 매칭 엔진이 매칭 방식 전환

### 3.3 💎 강화 제안 — OrderRouter에 브로커별 실행 비용 기반 동적 라우팅

**제안 이름**: 브로커별 실시간 레이턴시·수수료 추정치 기반 최적 라우팅 점수 도입

**적용 대상 파일/함수**: `src/brokers/router.py::OrderRouter.place_order()` 및 신규 `src/brokers/router.py::ExecutionCostEstimator`

**접목 방법**: 특허 (b) 구성요소의 "best execution platform" 선택 개념을 착안으로, `OrderRouter`에 `ExecutionCostEstimator` 헬퍼 클래스를 추가한다. 이 클래스는 최근 N건 `BrokerFill`에서 `(fill.price - mid_price) / mid_price` 슬리피지와 `fill.fee`를 집계하여 브로커별 `execution_cost_score`를 산출한다. `OrderRouter.place_order()` 진입 시 `KIS` vs `Binance` 등 다중 브로커가 등록된 경우 점수가 낮은 브로커로 자동 라우팅한다. `algo_params["force_broker"]` 오버라이드로 전략이 특정 브로커를 강제 지정 가능.

이는 특허 (a)(이중 매칭 엔진) 구성요소를 우리가 채택하지 않고, (b)의 "가격·수량 기반 라우팅"만을 **단일 브로커 스왑 프레임워크**로 재구성하여 침해 구성요건 전체 충족을 회피한다.

**기대 효과**: KIS fallback 전환([[10-broker-api-comparison]] §5) 로직이 단순 장애 기반에서 **실행 비용 최적화 기반**으로 격상된다. 저유동성 구간에서 Binance Futures 대비 KIS 슬리피지가 높아질 때 자동으로 라우팅 비율 조정 가능.

**저비용 검증 경로**: `tests/test_order_router_cost.py`에서 mock 브로커 2개로 슬리피지 차이 시나리오 → 라우팅 선택 검증.

### 3.4 차용 아이디어 메모

Implied Order 합성: 한국 ETF + 구성 종목 간 implied spread 포착. KRX 현물 특성상 직접 적용 복잡하나, 향후 파생상품 확장 시 참고.

### 3.5 회피 필요 영역

| 구성요소 | 회피 설계 |
|----------|----------|
| (a) 이중 매칭 엔진 (FIFO + Pro Rata) | 우리는 단일 `OrderRouter`가 단일 브로커에 위임 — 내부 이중 매칭 엔진 없음 |
| (c) Implied Order across multiple order books | 현재 미구현. 구현 시 단일 오더북 집계 방식으로 차별화 필요 |
| (d) 시장 상태 기반 알고리즘 동적 전환 | 구현 시 `algo_params["matching_mode"]` 키로 전략 레벨에서 명시 선택 방식으로 설계 — 자동 전환 아님 |

### 3.6 우리 코드 연결고리

- `src/brokers/router.py::OrderRouter` — 현재: 단일 active 브로커에 위임, `swap_active()` 수동 전환만 지원
- `src/brokers/base.py::BrokerAdapter` — 다중 브로커 추상화 인터페이스
- `src/brokers/types.py::BrokerFill` — 슬리피지 추정에 필요한 체결 데이터

---

## 4. 특허 3 — US20210272201A1

### 4.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US20210272201A1 |
| 제목 | Systems for optimizing trade execution |
| 출원인 | Roman Ginis (개인 발명자) |
| 공개일 | 2021-09-02 |
| 출원일 | 2021-05-17 |
| 법적 상태 | **심사 계속 중 (Pending)** |
| CPC | G06Q40/04, G06N20/00, G06Q30/02 |
| URL | https://patents.google.com/patent/US20210272201A1/en |

### 4.2 청구항 핵심 요약

독립항 핵심 구성요소:

- **(a) 시장 반응 모듈**: 최근 체결이 이후 시간 구간 가격에 미치는 영향(market response) 계산
- **(b) ML 엔진**: 실시간·역사적 데이터로 훈련된 모델이 adverse selection·market impact 최소화 파라미터 계산
- **(c) 매칭 파라미터**: 거래 시간 윈도우, 부분 체결 임계값, 매칭 타이밍·수량·가격, 체류 시간 요건
- **(d) 볼라틸리티 레짐 적응**: 변동성 레짐·스프레드·시간대에 따라 매칭 빈도 동적 조정 (피드백 루프)

### 4.3 💎 강화 제안 — TWAP 슬라이스 간격에 볼라틸리티 레짐 반영

**제안 이름**: 변동성 레짐 기반 TWAP 슬라이스 간격 동적 조정 (`volatility_adaptive_twap`)

**적용 대상 파일/함수**: `src/execution/twap.py::TWAPAlgo._maybe_emit()` 및 `TWAPAlgo.__init__()`

**접목 방법**: 특허 (d) 구성요소의 "볼라틸리티 레짐에 따른 매칭 빈도 조정" 개념을 착안으로, 현재 `TWAPAlgo`가 `duration / slice_count`로 균등 분할하는 방식에 `volatility_weight: list[float]` 선택 파라미터를 추가한다. 이 파라미터는 `on_market_tick` 시점의 실현 변동성(예: 최근 5 tick bid-ask spread 평균)을 기반으로 외부에서 계산·주입된다. 변동성이 낮은 구간에는 슬라이스를 조기에 집중 실행하고, 변동성이 높은 구간(VI 발동 직후 등)에는 슬라이스를 지연함으로써 시장충격을 줄인다. ML 모델 의존 없이 단순 규칙 기반으로 구현하여 특허 (b) ML 엔진 구성요소를 의도적으로 배제한다.

**기대 효과**: KRX VI 발동([[07-market-microstructure-basics]] §4-2) 직후 단일가 전환 구간에서 TWAP 슬라이스 발송을 자동 일시 정지하고 접속매매 재개 후 재개. 현재 `TWAPAlgo`는 이 동작이 없어 단일가 구간에서 불필요한 IOC 주문이 발생할 수 있음.

**저비용 검증 경로**: `src/execution/krx_handler.py` 이벤트(VI 발동, 서킷브레이커)를 TWAP 실행 루프에 연결 → VI 발동 시나리오 백테스트에서 슬리피지 개선율 측정.

### 4.4 차용 아이디어 메모

ML 기반 adverse selection 예측: 향후 `G06N20/00` 범주 접근 시, 현재 pending 상태인 이 특허의 독립항이 등록되면 침해 여부 재검토 필요. 지금은 규칙 기반으로만 구현.

### 4.5 회피 필요 영역

| 구성요소 | 회피 설계 |
|----------|----------|
| (a) 시장 반응 모듈 (ML 기반) | 우리는 통계적 spread/volume 지표로 대체 — ML 모델 없음 |
| (b) ML 엔진 피드백 루프 | 우리 TWAP은 규칙 기반 휴리스틱 사용. ML 도입 시 별도 서비스로 분리하여 실행 알고 본체와 경계 명확히 |
| (c) 체류 시간 요건(residency time) | 우리는 IOC TIF 사용으로 체류 시간 개념 없음 — 구성요소 자체 미채택 |

### 4.6 우리 코드 연결고리

- `src/execution/twap.py::TWAPAlgo` — 현재: 균등 분할, 볼라틸리티 미반영
- `src/execution/krx_handler.py` — KRX 단일가·VI 이벤트 핸들러 (연동 대상)
- `src/execution/base.py::Tick` — bid/ask로 spread 계산 가능

---

## 5. 특허 4 — US12067619B1

### 5.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US12067619B1 |
| 제목 | Systems and methods for electronic trade order routing |
| 출원인 | BlackRock Finance Inc / BlackRock Inc |
| 공개일 | 2024-08-20 |
| 출원일 | 2022-06-22 |
| 법적 상태 | **유효 (Active)** |
| CPC | G06Q40/04, G06Q40/0421, G06Q40/045, G06Q40/0451, G06F18/2415 |
| URL | https://patents.google.com/patent/US12067619B1/en |

### 5.2 청구항 핵심 요약

독립항 핵심 구성요소:

- **(a) 속성 벡터 추출**: 수신된 주문 데이터에서 속성 벡터(attribute vector) 추출
- **(b) 실행 스타일 확률 생성**: Auto / RFQ (Request for Quote) / Voice 세 가지 실행 모드별 확률 생성
- **(c) Implementation Shortfall 추정**: 각 실행 스타일별 실행 비용 지표(IS 추정치) 산출
- **(d) 최적 라우팅 선택**: 결합 비용 지표를 최소화하면서 확률 임계값을 충족하는 실행 스타일 선택
- **(e) 실행 지시 전송**: 선택된 플랫폼으로 실행 지시 전달

### 5.3 💎 강화 제안 — 주문별 Implementation Shortfall 사전 추정 로그

**제안 이름**: 주문 발송 전 IS(Implementation Shortfall) 사전 추정 및 로깅

**적용 대상 파일/함수**: `src/brokers/router.py::OrderRouter.place_order()` 신규 `pre_flight_is_estimate()` 헬퍼 추가

**접목 방법**: 특허 (c)(d) 구성요소의 "IS 추정 후 라우팅 선택" 개념에서 착안하여, `OrderRouter.place_order()` 진입 시점에 `pre_flight_is_estimate(req, market_snapshot)` 함수를 호출한다. 이 함수는 단순 공식 `IS_est = spread/2 + market_impact_coeff * sqrt(qty / avg_daily_volume)`으로 IS를 추정하고 결과를 `observability` 메트릭으로 기록한다(`src/observability/` 연계). 실제 체결 후 `BrokerFill`에서 실현 IS를 계산해 사전 추정과 비교하여 `is_prediction_error` 메트릭을 산출한다. IS 추정치가 `algo_params["max_is_bps"]` 임계값 초과 시 주문 보류 후 전략 레이어에 콜백을 반환하는 선택적 게이트 기능도 추가 가능.

이 구현은 특허 (b) "실행 스타일 확률(Auto/RFQ/Voice)" 구성요소를 채택하지 않고, IS 추정 수식도 BlackRock 방식과 다른 단순 파라메트릭 모델을 사용하여 독립항 전체 구성요소 충족을 회피한다.

**기대 효과**: 주문별 거래비용 사전/사후 비교가 가능해져 전략 수익률에서 실행 비용 기여분을 정량화할 수 있다. `src/observability/` 메트릭과 연동하면 브로커·전략별 슬리피지 드리프트를 실시간 모니터링 가능 ([[10-broker-api-comparison]] §6 observability 연계).

**저비용 검증 경로**: `tests/test_is_estimator.py`에서 mock 시장 스냅샷 → IS 추정 → fill 비교 단위 테스트. 이후 백테스트 레포트에 `avg_is_bps` 컬럼 추가로 전략별 실행 비용 비교.

### 5.4 차용 아이디어 메모

확률 임계값 기반 실행 모드 선택: 향후 다중 시장(KRX 현물 + Binance 선물) 동시 운용 시, RFQ 개념을 "지정가 first, 시장가 fallback" 2단계 TIF 전략으로 변환 가능.

### 5.5 회피 필요 영역

| 구성요소 | 회피 설계 |
|----------|----------|
| (a) 속성 벡터 추출 (ML feature) | 우리는 주문 속성을 `OrderRequest` dataclass 필드로 직접 사용 — 별도 벡터 추출 단계 없음 |
| (b) Auto/RFQ/Voice 3모드 확률 분류 | 우리 시스템에 Voice/RFQ 모드 없음 — 구성요소 자체 미존재 |
| (d) 결합 비용 최소화 선택 알고리즘 | 우리는 단순 `execution_cost_score` 비교로 대체 — 확률 임계값 결합 최소화 알고리즘 불채택 |

### 5.6 우리 코드 연결고리

- `src/brokers/router.py::OrderRouter.place_order()` — IS 추정 훅 삽입 지점
- `src/brokers/types.py::BrokerFill` — 실현 IS 계산용 체결 데이터
- `src/observability/` — IS 메트릭 기록 대상

---

## 6. 종합 매트릭스

| 특허 | 출원인 | 법적 상태 | 💎 강화 제안 요약 | 회피 핵심 | 연결 코드 경로 |
|------|--------|----------|-----------------|----------|---------------|
| US8571967B1 | Goldman Sachs | 만료 예정 (2026-12) | VWAP 볼륨 프로파일 실시간 갱신 | 정적 패턴 대신 당일 실시간 체결량 blend | `src/execution/vwap.py::VWAPAlgo` |
| US11164248B2 | CME Group | **유효** (2039) | OrderRouter 실행 비용 기반 동적 라우팅 | 이중 매칭엔진 미채택, 단일 라우터 구조 유지 | `src/brokers/router.py::OrderRouter` |
| US20210272201A1 | Roman Ginis | **심사 중** | TWAP에 볼라틸리티 레짐 적응형 간격 적용 | ML 엔진 배제, 규칙 기반 spread 휴리스틱 | `src/execution/twap.py::TWAPAlgo` |
| US12067619B1 | BlackRock | **유효** (2024) | 주문별 IS 사전 추정 + observability 연동 | RFQ/Voice 모드 미채택, IS 수식 독자 정의 | `src/brokers/router.py::place_order()` |

---

## 7. 우리 레포 강화 로드맵

| 강화 제안 | 대상 파일/함수 | 우선순위 | 예상 난이도 | 의존 이슈 |
|-----------|--------------|----------|------------|----------|
| VWAP 볼륨 프로파일 실시간 blend (`vwap_alpha`) | `src/execution/vwap.py::VWAPAlgo` | **High** | 낮음 (파라미터 추가 수준) | #68 (브로커 API — tick 데이터 공급) |
| TWAP 볼라틸리티 레짐 적응 + KRX VI 연동 | `src/execution/twap.py`, `src/execution/krx_handler.py` | **High** | 중간 (VI 이벤트 통합 필요) | `krx_handler.py` 이벤트 버스 구현 여부 |
| OrderRouter IS 사전 추정 + 메트릭 기록 | `src/brokers/router.py`, `src/observability/` | **Medium** | 낮음~중간 | `src/observability/` 메트릭 인프라 |
| 브로커별 실행 비용 점수 기반 동적 라우팅 | `src/brokers/router.py::OrderRouter` | **Low** | 중간 (다중 브로커 등록 로직 필요) | KIS + Binance 동시 운용 의사결정 |

---

## 8. 후속 이슈 후보

### 후보 A — TWAP/VWAP KRX VI 연동 실행 게이트

- **제목**: `feat: execution algo KRX VI/circuit-breaker gate — TWAP·VWAP 단일가 구간 자동 일시정지`
- **요약**: `src/execution/krx_handler.py`의 VI 발동/해제 이벤트를 `TWAPAlgo`·`VWAPAlgo`의 `on_market_tick` 루프에 연결하여, 단일가 구간 진입 시 슬라이스 발송을 일시 정지하고 접속매매 재개 후 자동 재개하는 게이트 로직 구현.
- **근거 특허**: US20210272201A1 (d) 볼라틸리티 레짐 적응 — 우리 구현은 ML 없는 규칙 기반으로 회피 설계.
- **연결 노트**: [[07-market-microstructure-basics]] §4-2 (VI), [[10-broker-api-comparison]] §6

### 후보 B — Implementation Shortfall 사전 추정 및 실행 TCA 대시보드

- **제목**: `feat: pre-flight IS estimator + post-trade TCA metric — 주문별 실행 비용 추적`
- **요약**: `OrderRouter.place_order()`에 `pre_flight_is_estimate()` 훅을 추가하고, 사전 IS 추정 vs. 실현 IS를 `src/observability/` 메트릭으로 기록. 전략별·브로커별 평균 IS를 리포트하는 TCA(Transaction Cost Analysis) 대시보드 문서 추가.
- **근거 특허**: US12067619B1 (c)(d) IS 기반 라우팅 선택 — 우리는 라우팅 로직 아닌 측정·로깅만 채택하여 청구항 전체 구성요소 충족 회피.
- **연결 노트**: [[10-broker-api-comparison]] §6, [[07-market-microstructure-basics]] §3-3

---

## 출처

- Google Patents — US8571967B1 (Goldman Sachs, VWAP 알고리즘): https://patents.google.com/patent/US8571967B1/en
- Google Patents — US11164248B2 (CME Group, SOR 멀티모달 실행): https://patents.google.com/patent/US11164248B2/en
- Google Patents — US20210272201A1 (Roman Ginis, ML 실행 최적화): https://patents.google.com/patent/US20210272201A1/en
- Google Patents — US12067619B1 (BlackRock, 전자 주문 라우팅): https://patents.google.com/patent/US12067619B1/en
- Google Patents 검색 — G06Q40/04 TWAP VWAP 알고리즘 특허 목록: https://patents.google.com/?q=TWAP+VWAP+execution&cpc=G06Q40/04
- QuestDB — Algorithmic Execution Strategies 개요: https://questdb.com/glossary/algorithmic-execution-strategies/
