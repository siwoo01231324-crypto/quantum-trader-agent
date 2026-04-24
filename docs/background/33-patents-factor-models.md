---
type: research
id: 33-patents-factor-models
name: "33. 팩터 모델·알파 시그널 특허 조사"
sources:
  - https://patents.google.com/patent/US20210224700A1/en
  - https://patents.google.com/patent/US20140081889A1/en
  - https://patents.google.com/patent/US20130332391A1/en
  - https://patents.google.com/patent/US8433645B1/en
  - https://patents.google.com/patent/US11645522B2/en
---

# 33. 팩터 모델·알파 시그널 특허 조사

> ⚠️ **법적 고지**: 본 노트는 학술·회피설계 목적 조사이며 변리사 리뷰가 아님.
> 상용 서비스 전 법무 검토 필수.
> 관련 노트: [[13-feature-alpha-catalog]], [[08-strategy-paradigms]], [[20-position-sizing]], [[26-point-in-time-data]] — 우리 시스템 강화 및 침해 리스크 제거 목적.

## 1. 조사 범위

**키워드**: 알파 팩터, 팩터 조합, ML 기반 시그널, 앙상블, 스태킹, 팩터 IC, 룩어헤드 방지, 포트폴리오 정화, 팩터 인덱스, 딥러닝 금융 예측

**검색 DB**: Google Patents (patents.google.com), KIPRIS (kipris.or.kr)

**조사 기준**:
- 독립항이 명확한 공개공보
- 시장 관련성 높고 최근 10년 이내 공개 우선
- 주제: 팩터 모델, 알파 시그널 생성·조합, ML 기반 주식 선택, 룩어헤드 방지

**조사된 특허**: 4건 (US20210224700A1, US20140081889A1, US20130332391A1, US8433645B1)

---

## 2. 특허 1 — US20210224700A1

### 2.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US20210224700A1 |
| 제목 | Techniques to forecast financial data using deep learning |
| 출원인 | State Street Corporation |
| 출원일 | 2019-03-15 |
| 공개일 | 2021-07-22 |
| 법적 상태 | 등록(활성), 만료일 2039-11-30 |
| URL | https://patents.google.com/patent/US20210224700A1/en |

### 2.2 청구항 핵심 요약

독립항 범위:
- (a) 과거 시계열 데이터를 두 시퀀스(장기/단기)로 분할
- (b) 장기 시퀀스에 LSTM 신경망을 적용하여 내부 패턴(추세·계절성) 파라미터 산출
- (c) 단기 시퀀스에 커널 함수를 적용하여 외생 팩터 파라미터 산출
- (d) 두 파라미터 세트를 연결(concatenate)하여 최종 예측값 생성
- (e) 예측값 vs 실제값 손실 비교 후 파라미터 반복 조정

핵심 혁신: LSTM(내부 패턴) + 커널 함수(외생 팩터)의 이중 모델링 결합 — 이를 "deep-SARIMAX"라 명명.

적용 사례: 미국채 수익률, OAS(옵션조정스프레드), S&P500 지수 예측.

**주의**: 청구항은 개별 주식 선택이나 알파 팩터를 명시적으로 다루지 않음. 거시 금융 시계열 예측에 집중.

### 2.3 💎 강화 제안 1 — 이중 시퀀스 분리를 팩터 계산에 적용 (룩어헤드 강화)

- **제안 이름**: LSTM 장기 패턴 + 커널 단기 외생변수 이중 입력 구조를 `src/signals/` 팩터에 적용
- **적용 대상 파일/함수**: `src/signals/registry.py::compute()` 및 신규 `src/signals/lstm_factor.py`
- **접목 방법**: 현재 `registry.py::compute()`는 단일 OHLCV 딕셔너리를 팩터 함수에 그대로 전달한다. State Street 특허의 핵심 구조—"장기 시퀀스(내부 패턴용)"와 "단기 시퀀스(외생 팩터용)"를 분리하여 각각 다른 모델에 공급—를 차용하면, `FactorSpec`에 `long_window`(LSTM 내부 패턴용, 예: 252봉)와 `short_window`(외생 변수용, 예: 20봉)를 추가 필드로 선언하고 `compute()`에서 자동으로 슬라이싱하여 전달할 수 있다. 이로써 팩터별 입력 길이를 레지스트리 수준에서 강제할 수 있어 룩어헤드 방지 `assert_no_lookahead()`의 입력 일관성이 높아진다.
- **기대 효과**: 팩터 함수가 자체적으로 시퀀스 분할을 처리하지 않아도 됨 → 팩터 코드 단순화. `lookahead_guard.py`의 `_slice_inputs()`와 연동 시 단기 시퀀스 경계가 명확히 정의되어 데이터 누출 감지 정확도 향상.
- **저비용 검증 경로**: `FactorSpec`에 `long_window: int = 252`, `short_window: int = 20` 필드 추가 → `compute()`에서 슬라이싱 로직 5줄 추가 → 기존 테스트 통과 여부 확인.

### 2.4 차용 아이디어 메모

- 미래 ML 팩터 도입 시: LSTM + 커널 함수 이중 구조로 비선형 추세와 외생 변수(외국인 수급, 공매도 잔고)를 분리 모델링 → `[[13-feature-alpha-catalog]]` §3 대체데이터와 연계 가능.

### 2.5 회피 필요 영역

청구항 구성요소 카탈로그:
- (a) 시계열 두 시퀀스 분할
- (b) 비선형 딥러닝(LSTM) 내부 패턴 모델
- (c) 커널 함수 외생 팩터 파라미터
- (d) 두 파라미터 연결 후 예측

**회피 설계**: 우리 시스템은 (b)를 LSTM이 아닌 지수이동평균(EMA) 또는 MACD 기반 피처로 대체하고, (c)를 커널 함수 대신 크로스섹션 z-score 정규화로 치환하면 (d)의 연결 구조를 유지하면서도 독립 설계 확보 가능.

### 2.6 우리 코드 연결고리

- `src/signals/registry.py` — `FactorSpec`, `compute()`: 팩터 입력 시퀀스 관리 대상
- `src/signals/lookahead_guard.py` — `assert_no_lookahead()`: 시퀀스 분리와 직접 연동

---

## 3. 특허 2 — US20140081889A1

### 3.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US20140081889A1 |
| 제목 | Purifying Portfolios Using Orthogonal Non-Target Factor Constraints |
| 출원인 | Axioma Inc |
| 출원일 | 2013-09-12 |
| 공개일 | 2014-03-20 |
| 법적 상태 | 포기(Abandoned) — 침해 리스크 낮음 |
| URL | https://patents.google.com/patent/US20140081889A1/en |

### 3.2 청구항 핵심 요약

독립항 범위:
- (a) 타겟 팩터 노출을 극대화하는 포트폴리오 최적화
- (b) 비타겟 팩터를 타겟 팩터에 대한 직교 성분(orthogonal projection)으로 자동 변환
- (c) 변환된 직교 제약을 표준 평균-분산 최적화에 적용
- (d) 직교화된 제약 포트폴리오가 전통 제약 대비 우월한 위험조정 수익률 달성

핵심 혁신: Gram-Schmidt 직교화를 팩터 제약에 적용하여 타겟 팩터 노출을 유지하면서 비의도적 팩터 베팅 제거 ("포트폴리오 정화").

백테스트: 미국 및 유럽 주식 대상, 직교화 제약 포트폴리오가 전통 제약 접근 대비 우월.

### 3.3 💎 강화 제안 2 — 직교 팩터 정화를 `src/signals/` 크로스섹션 정규화에 통합

- **제안 이름**: 섹터·사이즈 직교화(Gram-Schmidt) 정규화를 `neutralize()` 함수에 추가
- **적용 대상 파일/함수**: `src/signals/registry.py::compute()` 전처리 단계 또는 신규 `src/signals/neutralize.py::orthogonal_neutralize()`
- **접목 방법**: 현재 [[13-feature-alpha-catalog]] §5의 `alpha_catalog/utils.py::neutralize(factor, exposures)` 스텁은 잔차 회귀(OLS) 기반 섹터·사이즈 중립화를 제안한다. Axioma 특허의 핵심 아이디어를 차용하면 이를 Gram-Schmidt 직교화로 강화할 수 있다: (1) 타겟 팩터 벡터 `f_target`을 기준으로 각 비타겟 팩터 벡터 `f_i`를 직교 분해 (`f_i_orth = f_i - (f_i · f_target / f_target · f_target) * f_target`), (2) 직교 성분만 제약으로 사용하여 최적화 문제 구성. 이 구조는 `registry.py`의 `compute()` 후처리로 삽입하거나, 팩터 파이프라인 종단에 `orthogonal_neutralize(raw_factor, sector_exposure, size_exposure)` 형태로 호출 가능.
- **기대 효과**: 현재 OLS 잔차 회귀는 타겟 팩터 노출을 부분적으로 희석시키는 부작용이 있다. 직교화 방식은 타겟 팩터 IC(정보계수)를 보존하면서 비의도 팩터 노출만 제거 → 시그널 순도(Signal Purity) 향상. 한국 시장에서 사이즈·섹터 팩터가 모멘텀·밸류 팩터와 높은 상관을 가지는 구조에서 특히 유효.
- **저비용 검증 경로**: 한국 코스피200 종목에서 모멘텀 팩터를 OLS 중립화 vs 직교화 중립화로 각각 처리 후 전월 수익률 IC(RankIC) 비교. 데이터: pykrx 일봉 + KRX 시가총액. 예상 구현 시간: 2~3일.

### 3.4 차용 아이디어 메모

- 알파 팩터 라이브러리를 확장할 때, 새 팩터 등록 시 기존 팩터들과의 직교화 여부를 `FactorSpec`에 `orthogonalize_against: list[str] = []` 필드로 선언하면 레지스트리가 자동 정화 처리 가능.

### 3.5 회피 필요 영역

청구항 구성요소 카탈로그:
- (a) 타겟 팩터 노출 극대화 최적화
- (b) 비타겟 팩터 → 직교 성분 자동 변환
- (c) 변환 제약을 최적화에 삽입

**회피 설계**: 특허는 포기(Abandoned) 상태로 법적 리스크 없음. 그러나 설계 독립성을 위해 (b)의 자동 변환 대신 사전 전처리(pre-processing) 단계에서 수동으로 직교화를 적용하고, 최적화 단계와 분리 운영하면 구조적 차별화 확보.

### 3.6 우리 코드 연결고리

- `src/signals/registry.py` — `FactorSpec`, `compute()`: 직교화 필드 추가 위치
- [[13-feature-alpha-catalog]] §5 `alpha_catalog/utils.py::neutralize()`: 현재 구현 스텁

---

## 4. 특허 3 — US20130332391A1

### 4.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US20130332391A1 |
| 제목 | Methodology and Process For Constructing Factor Indexes |
| 출원인 | Axioma Inc |
| 출원일 | 2013-08-13 |
| 공개일 | 2013-12-12 |
| 법적 상태 | 포기(Abandoned) — 침해 리스크 없음 |
| URL | https://patents.google.com/patent/US20130332391A1/en |

### 4.2 청구항 핵심 요약

독립항 범위:
- (a) 종목 유니버스 선택 및 벤치마크 포트폴리오 정의
- (b) 두 개의 리스크 모델 지정
- (c) 벤치마크와 팩터 노출이 현저히 다른 타겟 팩터 포트폴리오 구성
- (d) 규정된 추적오차(tracking error) 임계값 이하를 최소화하는 가중치 결정
- (e) 순차적 최적화 문제(추적오차 최소화 → 회전율 최소화 → 비모멘텀 팩터 중립화)

핵심 혁신: 단일 제약 최적화 대신 단계적(tiered) 순차 최적화로 팩터 인덱스 구성. 모멘텀 팩터 정의: "최근 250 거래일 누적 수익률, 마지막 20 거래일 제외" (12-1 모멘텀).

### 4.3 💎 강화 제안 3 — 단계적 순차 최적화를 팩터 등록 파이프라인에 도입

- **제안 이름**: 팩터 파이프라인에 순차 품질 필터(Tiered Quality Gate) 도입
- **적용 대상 파일/함수**: `src/signals/registry.py::register()` 데코레이터 및 신규 `src/signals/pipeline.py::run_pipeline()`
- **접목 방법**: 현재 `registry.py`는 팩터를 등록하고 `compute()`로 단일 호출한다. Axioma 특허의 순차 최적화 구조를 차용하면, 팩터 파이프라인을 3단계 품질 게이트로 구성할 수 있다: **Stage 1** — 룩어헤드 검증 (`assert_no_lookahead()`), **Stage 2** — IC 임계값 필터 (ICIR ≥ 0.3 미만 팩터 비활성화), **Stage 3** — 팩터간 상관 중복 제거 (pairwise |correlation| > 0.7이면 IC 낮은 쪽 제거). `run_pipeline(factor_names, ohlcv, min_icir=0.3, max_corr=0.7)` 형태로 호출하면 매 리밸런싱 주기마다 활성 팩터 세트를 동적으로 결정.
- **기대 효과**: 팩터 품질 저하(IC 감소, 팩터 붐비기)를 레지스트리 수준에서 자동 감지. 현재 수동 확인에 의존하는 팩터 유효성 관리를 자동화하여 알파 소멸(alpha decay) 조기 탐지 가능. [[08-strategy-paradigms]] §3 ML 전략의 과적합 방지와 직결.
- **저비용 검증 경로**: `registry.py` 위에 `pipeline.py` 모듈을 신규 생성, `run_pipeline()` 구현 후 기존 RSI/MACD/ATR 팩터에 적용하여 각 Stage 통과 여부 확인. 예상 구현: 1일.

### 4.4 차용 아이디어 메모

- 12-1 모멘텀 정의(250일 누적 수익률, 마지막 20일 제외)를 `src/signals/` 모멘텀 팩터 구현 시 기준 정의로 활용. [[13-feature-alpha-catalog]] §2 모멘텀 팩터 정의와 일치.
- 회전율 최소화를 2단계로 배치하는 구조는 우리 리밸런싱 로직에서도 참고 가능.

### 4.5 회피 필요 영역

청구항 구성요소 카탈로그:
- (a) 종목 유니버스 + 벤치마크 정의
- (b) 두 리스크 모델 지정
- (c) 추적오차 최소화 최적화
- (d) 순차적 최적화(회전율 → 팩터 중립화)

**회피 설계**: 포기(Abandoned) 상태. 그러나 설계 독립성을 위해 (b)의 "두 리스크 모델" 구조 대신 단일 리스크 모델 + 동적 팩터 가중치 조정 방식을 채택. (d)의 순차 최적화 대신 병렬 품질 게이트 방식으로 구현.

### 4.6 우리 코드 연결고리

- `src/signals/registry.py` — `FACTOR_REGISTRY`, `list_factors()`: 파이프라인 진입점
- `src/signals/lookahead_guard.py` — Stage 1 룩어헤드 검증의 기반

---

## 5. 특허 4 — US8433645B1

### 5.1 서지 정보

| 항목 | 내용 |
|------|------|
| 등록번호 | US8433645B1 |
| 제목 | Methods and systems related to securities trading |
| 출원인 | Portware LLC (현재); Alpha Vision Services LLC (원출원) |
| 출원일 | 2012-09-13 |
| 공개일 | 2013-04-30 |
| 법적 상태 | 등록(활성) |
| URL | https://patents.google.com/patent/US8433645B1/en |

### 5.2 청구항 핵심 요약

독립항 범위:
- (a) 수백 개의 기본·기술 정보 드라이버 분석
- (b) 입력 주문에 시장 상황 + 역사적 패턴 기반 "알파 프로파일" 할당
- (c) 알파 프로파일로 편향없는 가격 움직임 예측 팩터 식별
- (d) 최적 실행 전략 권고
- (e) 구현 단차(Implementation Shortfall)를 알파 손실·시장 충격·역선택·기회 절감으로 분해

핵심 혁신: 실행 품질 분석에 "알파 프로파일"을 도입하여 알파 손실과 시장 충격을 분리 측정.

**주의**: 이 특허는 실행 알고리즘 영역에 걸쳐 있으나, 알파 시그널 → 실행 전략 연결 구조가 팩터 모델과 직접 연관됨.

### 5.3 💎 강화 제안 4 — 알파 프로파일 기반 팩터 → 실행 연결 인터페이스 설계

- **제안 이름**: `FactorSpec`에 `alpha_horizon` 메타데이터 추가로 팩터 → 실행 레이어 연결
- **적용 대상 파일/함수**: `src/signals/registry.py::FactorSpec` 데이터클래스
- **접목 방법**: 현재 `FactorSpec`은 `name`, `func`, `inputs`, `default_params`만 보유한다. Portware 특허의 "알파 프로파일" 개념을 차용하면 각 팩터에 `alpha_horizon: int` (알파 지속 예상 봉수), `signal_type: str` (예: "momentum", "mean_reversion", "event")를 추가할 수 있다. 이 메타데이터를 실행 모듈이 읽어 알파 지속 시간이 짧은 팩터(단기 모멘텀)는 공격적 실행 알고리즘(적은 분할), 알파 지속이 긴 팩터(밸류)는 TWAP/VWAP 분할 실행으로 자동 라우팅하는 기반을 마련할 수 있다.
- **기대 효과**: 팩터 특성에 맞는 실행 전략 자동 선택 → 알파 손실(alpha decay due to slow execution) 최소화. 현재 `src/brokers/`와 향후 실행 모듈(`src/execution/`) 연결 설계의 기초.
- **저비용 검증 경로**: `FactorSpec`에 `alpha_horizon: int = 1`, `signal_type: str = "unknown"` 추가 (기본값으로 기존 코드 무파괴). 신규 팩터 등록 시 명시적으로 채우도록 가이드 문서 업데이트.

### 5.4 차용 아이디어 메모

- 구현 단차 분해(알파 손실 / 시장 충격 / 역선택)는 백테스트 엔진에서 거래비용 분석 기능으로 추후 구현 가능. `[[20-position-sizing]]` 켈리 사이징과 연계 시 실질 엣지 추정에 유용.

### 5.5 회피 필요 영역

청구항 구성요소 카탈로그:
- (a) 수백 드라이버 분석
- (b) 알파 프로파일 할당
- (c) 편향없는 가격 예측 팩터 식별
- (d) 실행 전략 권고 + 단차 분해

**회피 설계**: (b)의 "알파 프로파일" 할당을 우리는 팩터 메타데이터 필드로만 구현하고 실행 전략 자동 권고((d)) 기능은 별도 모듈로 분리. (c)의 "편향없는" 가격 예측 개념을 구현 단차가 아닌 룩어헤드 방지(causal factor) 검증으로 대체.

### 5.6 우리 코드 연결고리

- `src/signals/registry.py::FactorSpec`: 알파 메타데이터 추가 위치
- `src/brokers/` — KIS, Binance 커넥터: 향후 알파 프로파일 → 실행 전략 라우팅 수신자

---

## 6. 종합 매트릭스

| 특허 | 출원인 | 법적 상태 | 강화 제안 요약 | 핵심 회피 포인트 | 연결 코드 경로 |
|------|--------|-----------|----------------|-----------------|----------------|
| US20210224700A1 | State Street Corp | 등록(활성) | 이중 시퀀스(장기/단기) 분리를 `FactorSpec`에 `long_window`/`short_window` 필드로 도입 | LSTM + 커널 조합 대신 EMA + z-score 대체 | `src/signals/registry.py`, `lookahead_guard.py` |
| US20140081889A1 | Axioma Inc | 포기 | Gram-Schmidt 직교화 기반 `orthogonal_neutralize()` 추가 → 팩터 IC 보존하며 비의도 노출 제거 | 자동 변환 대신 사전 전처리로 분리 | `src/signals/registry.py`, `utils.py::neutralize()` |
| US20130332391A1 | Axioma Inc | 포기 | 3단계 품질 게이트(룩어헤드→ICIR→상관 중복) `run_pipeline()` 구현 | 두 리스크 모델 대신 단일 모델 + 동적 가중치 | `src/signals/registry.py`, `lookahead_guard.py` |
| US8433645B1 | Portware LLC | 등록(활성) | `FactorSpec`에 `alpha_horizon`, `signal_type` 메타데이터 추가 → 실행 레이어 자동 라우팅 기반 | 실행 전략 자동 권고 기능은 별도 모듈로 분리 | `src/signals/registry.py::FactorSpec`, `src/brokers/` |

---

## 7. 우리 레포 강화 로드맵

| # | 강화 제안 | 우선순위 | 예상 난이도 | 의존 |
|---|-----------|---------|------------|------|
| SP-1 | `FactorSpec`에 `long_window`/`short_window` 추가, `compute()`에서 자동 슬라이싱 | High | Low (1일) | 없음 |
| SP-2 | `orthogonal_neutralize()` 구현 — Gram-Schmidt 직교화 팩터 정화 | High | Medium (3일) | SP-1 |
| SP-3 | `run_pipeline()` 3단계 품질 게이트 구현 (룩어헤드→ICIR→상관) | High | Medium (2일) | SP-1 |
| SP-4 | `FactorSpec`에 `alpha_horizon`, `signal_type` 메타데이터 필드 추가 | Med | Low (0.5일) | 없음 |

**SP-1, SP-4는 비파괴적(기본값 추가)이므로 즉시 적용 가능.**
**SP-2, SP-3는 신규 모듈로 추가 → 기존 팩터 파이프라인 무영향.**

---

## 8. 후속 이슈 후보

### 이슈 후보 A: `src/signals/` 팩터 파이프라인 품질 게이트 구현

- **제목**: feat: 팩터 파이프라인 품질 게이트 (ICIR 필터 + 상관 중복 제거)
- **요약**: `src/signals/pipeline.py::run_pipeline()` 신규 구현. Stage 1 룩어헤드, Stage 2 ICIR ≥ 0.3, Stage 3 pairwise 상관 ≤ 0.7 필터. 매 리밸런싱 시 활성 팩터 세트 자동 결정.
- **연결 노트**: [[33-patents-factor-models]] SP-3, [[13-feature-alpha-catalog]] §7 체크리스트
- **근거 특허**: US20130332391A1 (순차 최적화 구조), US20210224700A1 (시퀀스 분리)

### 이슈 후보 B: `FactorSpec` 직교화 메타데이터 + `orthogonal_neutralize()` 구현

- **제목**: feat: 팩터 직교화 정규화 모듈 (`orthogonal_neutralize`) 추가
- **요약**: `src/signals/neutralize.py` 신규. `orthogonal_neutralize(factor, sector_exposure, size_exposure)` 구현. `FactorSpec`에 `orthogonalize_against: list[str]` 필드 추가. 코스피200 대상 OLS vs Gram-Schmidt IC 비교 테스트 포함.
- **연결 노트**: [[33-patents-factor-models]] SP-2, [[13-feature-alpha-catalog]] §5
- **근거 특허**: US20140081889A1 (직교 제약 포트폴리오 정화)

---

## 관련 노트

- [[13-feature-alpha-catalog]] — 본 노트의 강화 제안이 적용될 알파·피처 카탈로그
- [[08-strategy-paradigms]] — ML 전략 패러다임 및 과적합 방지 배경
- [[20-position-sizing]] — 팩터 시그널 → 포지션 사이징 연결
- [[26-point-in-time-data]] — 룩어헤드 방지·수정주가 설계 (본 노트 §2 이중 시퀀스와 연계)

---

## 출처

- Google Patents US20210224700A1 (State Street, Deep Learning Financial Forecasting): https://patents.google.com/patent/US20210224700A1/en
- Google Patents US20140081889A1 (Axioma, Portfolio Purification): https://patents.google.com/patent/US20140081889A1/en
- Google Patents US20130332391A1 (Axioma, Factor Index Construction): https://patents.google.com/patent/US20130332391A1/en
- Google Patents US8433645B1 (Portware, Alpha Profile Securities Trading): https://patents.google.com/patent/US8433645B1/en
- Google Patents US11645522B2 (Random Forest Stock Prediction): https://patents.google.com/patent/US11645522B2/en
- KIPRIS 특허 검색 서비스: https://www.kipris.or.kr/
- KIPRIS Plus: https://plus.kipris.or.kr/
- AlphaForge — 포뮬레익 알파 팩터 동적 조합 프레임워크: https://arxiv.org/html/2406.18394v1
- 동적 가중치 기반 복합 ML 주식 선택 전략: https://arxiv.org/html/2508.18592v1
- IC 기반 팩터 가중치 실무 접근: https://insight.factset.com/a-practical-approach-to-weighting-signals

adopted in #76 with differentiated formula — see [[signal-interface]]
