---
type: research
id: 27-corporate-actions
name: "Corporate Actions — 이벤트별 가격·수량·포지션 조정 프로토콜"
sources:
  - https://open.krx.co.kr/contents/OPN/02/02010000/OPN02010000.jsp
  - https://kind.krx.co.kr/
  - https://opendart.fss.or.kr/
  - https://github.com/sharebook-kr/pykrx
  - https://www.investopedia.com/terms/c/corporateaction.asp
  - https://www.isda.org/bookstore/equity-derivatives-definitions/
---

# Corporate Actions — 이벤트별 가격·수량·포지션 조정 프로토콜

> [[data-lake-schema]] §4.5 는 `corp_action` 테이블 스키마만 제공 (`action_type ∈ {split, dividend, merger, rename, delist}`). 실무에는 더 많은 이벤트 타입과 각각의 가격·수량·포지션 조정 로직이 필요하다. 본 노트는 (1) KRX 에서 발생하는 8가지 주요 이벤트, (2) 이벤트별 조정 공식, (3) 백테스트·라이브에서 포지션 처리 방식, (4) 데이터 소스·실무 체크리스트를 정리한다.

---

## 1. 이벤트 카테고리 8종 (KRX 기준)

| # | 이벤트 | 코드 | 주가 조정 | 수량 조정 | 현금 발생 | 빈도 |
|---|-------|------|-----------|-----------|-----------|------|
| 1 | **액면분할** | `split` | ✓ (1/N) | ✓ (×N) | — | 드묾 |
| 2 | **액면병합** | `merge_ratio` | ✓ (×N) | ✓ (1/N) | — | 매우 드묾 |
| 3 | **현금배당** | `cash_dividend` | ✓ (배당락) | — | ✓ (배당금) | 연 1~2회 |
| 4 | **주식배당** | `stock_dividend` | ✓ | ✓ (+r) | — | 중간 |
| 5 | **무상증자** | `bonus` | ✓ (1/(1+r)) | ✓ (×(1+r)) | — | 빈번 |
| 6 | **유상증자** | `rights` | ✓ (권리락) | 선택적 (청약 시) | − (청약 시) | 빈번 |
| 7 | **합병** | `merger` | — (피합병 상폐) | ✓ (전환비율) | ± (현금병합 시) | 드묾 |
| 8 | **스핀오프** | `spinoff` | ✓ (분할락) | 분할 전 종목 유지 + 신규 종목 부여 | — | 매우 드묾 |

**기타 주의**: 자본감소 (감자), 재상장, 종목코드 변경 (rename) 은 가격·수량 조정 없이 메타만 업데이트.

---

## 2. 이벤트별 조정 공식 상세

### 2.1 액면분할 N:1 (Stock Split)

- 1 주 → N 주. 가격은 1/N. 시가총액 동일.
- 예: 삼성전자 2018-05-04 50:1 분할. 전일 종가 2,650,000원 → 당일 시가 53,000원 (×1/50).
- **조정 factor (backward)**: `1/N` 을 이전 가격·거래량 역으로 곱 ([[26-point-in-time-data]] §3.3)
- **포지션 처리**: 100주 보유 시 분할 후 5,000주 자동 증가. 시뮬 엔진도 ex_date 전일 장 마감 후 자동 적용.

### 2.2 액면병합 1:N (Reverse Split)

- N 주 → 1 주. 가격 ×N. 시총 동일.
- 조정 factor: `N`
- 단주 처리: 병합 비율에 안 맞는 잔주는 현금지급 (통상 병합가 기준). 백테스트에서는 **소수점 주식 허용** 단순화 권장.

### 2.3 현금배당 (Cash Dividend)

- 배당락일 (ex-dividend date): 당일부터 배당권 없음. 당일 기준가 = 전일 종가 − 배당금/주
- **조정 factor**: `factor = (prev_close - div_per_share) / prev_close`
- **현금 발생**: 배당락 후 실제 지급일 (KRX 일반 지급일 = 배당락일 + 2~3개월)
- 백테스트 처리:
  - Total Return 기준: 배당을 재투자 또는 현금보유로 기록
  - Price Return 만 쓰면 수익률 **저평가**
  - [[19-portfolio-risk]] 의 공분산 추정은 total return 기반
- 배당세 15.4% ([[tax-automation]] §2-3) 는 최종 순수익 계산에서 차감

### 2.4 주식배당 (Stock Dividend, 비율 r)

- 보유 1 주당 r 주 추가. 가격은 `1/(1+r)` 로 내림.
- 조정 factor: `1/(1+r)`
- 수량: `×(1+r)` → 백테스트 portfolio 에서 자동 증가

### 2.5 무상증자 (Bonus Issue, 비율 r)

- 실질적으로 주식배당과 동일하게 처리.
- 조정 factor: `1/(1+r)`
- 차이: 회계 처리상 자본 출처 (준비금→자본금)

### 2.6 유상증자 (Rights Issue, 가장 복잡)

파라미터:
- `subscription_price`: 청약 가격 (시가보다 할인)
- `r`: 배정비율 (1주당 신규 r 주)
- `prev_close`: 권리락일 전일 종가

**권리락 가격 공식**:
```
theoretical_price = (prev_close + subscription_price * r) / (1 + r)
```

**조정 factor (backward)**:
```
factor = theoretical_price / prev_close
```

**청약 결정**:
- 청약하면 현금 `subscription_price × r × holdings` 유출, 수량 `×(1+r)`
- 청약 포기하면 권리 시장가 매각 (신주인수권증서 거래). 본 프로젝트 초기는 **전액 청약** 가정 단순화.

### 2.7 합병 (Merger)

- 피합병 회사 주식 상장폐지. 주주는 존속회사 주식을 **합병비율** 로 교환.
- 예: A (피합병) 1주 → B (존속) 0.3주
- 포지션 처리: 피합병 종목 포지션 → 존속 종목 포지션 (수량 × ratio). 잔주는 현금.
- 백테스트 시뮬: `corp_action.action_type='merger', meta.ratio=0.3, meta.survivor='B'`

### 2.8 스핀오프 (Spinoff, 회사분할)

- 모회사 1주 보유 → 모회사 1주 + 자회사 r 주
- 모회사 주가는 자회사 공정가치만큼 하락
- 조정 factor (모회사): `factor = (prev_close - spinoff_fair_value) / prev_close`
- 신규 종목 (자회사) 은 `listed_at` 이 스핀오프일

---

## 3. 통합 데이터 모델 (data-lake-schema 확장 제안)

기존 [[data-lake-schema]] §4.5 의 `corp_action` 은 `ratio DOUBLE, meta JSON` 이 너무 유연. 실무적으로는 타입별 필수 필드를 명시:

```sql
CREATE TABLE corp_action (
  symbol              VARCHAR NOT NULL,
  ex_date             DATE    NOT NULL,
  action_type         VARCHAR NOT NULL,
  -- 공통
  ratio               DOUBLE,                    -- split: N, bonus: r, merger: swap_ratio
  -- cash_dividend
  dividend_per_share  DOUBLE,
  payment_date        DATE,
  -- rights
  subscription_price  DOUBLE,
  record_date         DATE,                      -- 권리확정일
  -- merger / spinoff
  counterpart_symbol  VARCHAR,                   -- 존속회사 / 자회사
  counterpart_ratio   DOUBLE,
  -- 감사
  source              VARCHAR NOT NULL,          -- 'KRX','DART','pykrx'
  ingested_at         TIMESTAMP NOT NULL DEFAULT now(),
  PRIMARY KEY(symbol, ex_date, action_type)
);
```

PIT 원칙 ([[26-point-in-time-data]]): `ingested_at` 이후 버전은 append-only 로 추가. 공시 수정은 새 row 로 저장, 조회 시 `ingested_at <= as_of` 필터.

---

## 4. 백테스트·라이브 처리 순서 (pseudocode)

```
Each trading day t:
    # 1. load PIT snapshot
    universe = tradable_universe(as_of=t)

    # 2. 전일 발생 corp_action 반영
    for action in corp_action.where(ex_date == t):
        if action.type in {split, merge_ratio, bonus, stock_dividend}:
            portfolio.adjust_qty(symbol, factor=action.ratio)
        elif action.type == cash_dividend:
            portfolio.cash += action.dividend_per_share * portfolio.qty(symbol)
        elif action.type == rights:
            # 단순화: 전액 청약 가정
            cost = action.subscription_price * action.ratio * portfolio.qty(symbol)
            portfolio.cash -= cost
            portfolio.adjust_qty(symbol, factor=1 + action.ratio)
        elif action.type == merger:
            portfolio.swap(symbol, to=action.counterpart, ratio=action.counterpart_ratio)
        elif action.type == spinoff:
            portfolio.add(action.counterpart, qty=portfolio.qty(symbol) * action.counterpart_ratio)
        elif action.type == delist:
            portfolio.close(symbol, price=ohlcv.last_close(symbol))

    # 3. 전략 실행 — 가격은 ohlcv_adj (§수정주가)
    orders = strategy.decide(market_state=ohlcv_adj.as_of(t))

    # 4. 체결 시뮬 — 가격은 ohlcv raw (§원주가)
    fills = matching_engine.execute(orders, ohlcv_raw.as_of(t))
```

---

## 5. 데이터 소스·수집 경로

| 소스 | API / 방법 | 커버 | 지연 |
|------|-----------|------|------|
| **KRX 공지사항** | `https://kind.krx.co.kr/` 크롤링 | 전 이벤트 | 공시 즉시 |
| **DART (금감원)** | OpenDART API (`opendart.fss.or.kr`) | 공시 원문, 재무제표 | 실시간 |
| **pykrx** | `get_market_ohlcv_by_date(adjusted=True)`, `get_market_fundamental` | 수정주가·배당·단순 corp_action | T+1 |
| **증권사 API** (KIS·키움) | 공시 이벤트 push, 배당 기준일 쿼리 | 실시간 | 실시간 |

**본 프로젝트 수집 전략**:
1. **KRX KIND** 크롤 — 모든 이벤트의 1차 소스. 단, 권리·분할 상세 파라미터는 KIND 본문 텍스트 파싱 필요
2. **DART OpenAPI** — 합병·분할·유상증자는 정정공시 포함해 전 history. 정형 데이터 제공
3. **pykrx 교차검증** — [[26-point-in-time-data]] §5 의 T5 테스트

---

## 6. 실무 체크리스트

- [ ] 모든 `corp_action` 은 단일 소스 아닌 **2개 이상 교차검증** (KRX + DART)
- [ ] 배당락일·권리락일 등 ex_date 는 **거래일 기준** (휴장이면 다음 영업일)
- [ ] 수량 조정은 **정수** 로 유지 (소수점 주식 시뮬 허용 여부 명시)
- [ ] 합병·스핀오프는 종목코드 매핑 테이블 별도 유지
- [ ] 유상증자 청약 로직은 명시적 default ("전액 청약") + 예외 케이스 로그
- [ ] 상장폐지 처리는 [[26-point-in-time-data]] §4.3 의 "청산 가정" 옵션 명시
- [ ] 총 수익률 계산 시 `return = (close_adj + div_per_share) / close_adj.shift(1) - 1` 형태 사용
- [ ] 배당소득세 15.4% ([[tax-automation]]) 차감 후 net return 리포트
- [ ] 연말 포지션 스냅샷에서 corp_action 적용 전/후 NAV 보고

---

## 7. 테스트 케이스 (tests/test_corp_actions.py)

- `test_split_50to1_samsung_2018` — 실 과거 케이스 재현
- `test_cash_dividend_skkim_2024` — 배당락 + 총 수익률 계산
- `test_rights_issue_dongwha_2023` — 유상증자 theoretical price 공식 검증
- `test_merger_delisting` — 피합병 종목 상폐 + 존속 종목 전환비율 처리
- `test_spinoff_lg_chem_2020` — LG화학 → LG에너지솔루션 스핀오프 케이스
- `test_bonus_10pct` — 무상증자 10% 가격·수량 검증

각 테스트는 pykrx raw 데이터 + 자체 backward_adjust 결과 비교.

---

## 8. 한계·알려진 이슈

1. **공시일 ≠ ex_date**: KRX 에서 권리락일은 배당기준일 기준으로 공시일보다 뒤. 엄밀한 PIT 는 "공시일 이후 알려진 정보" 로 필터링해야 함
2. **미수 청약의 회계 처리** — 본 프로젝트 v1 는 전액 청약 가정. 현실 투자자는 일부 청약 후 신주인수권증서 매각하는 패턴 — v2 에서 확장
3. **해외 ADR 재상장** — 미국 ADR 형태로 재상장되는 경우 (예: Coupang) KRX corp_action 스코프 밖
4. **한국 특수 — 차등의결권**: 2023 년 이후 상장 벤처는 차등의결권 주식 발행 가능. 의결권 비율이 다르므로 M&A 시나리오에서 주의
5. **Dual Listing** — 네이버·카카오 등 일부 해외 예탁 시장 중복. 본 프로젝트 KRX 단일 시장 기준

---

## 관련 노트

- [[data-lake-schema]] — §4.5 `corp_action` 테이블의 운영 상세화
- [[26-point-in-time-data]] — 본 노트의 조정 계수가 PIT 적용의 핵심 입력
- [[13-feature-alpha-catalog]] — 수정주가·생존편향 맥락에서 본 노트 참조
- [[12-validation-protocol]] — §2 체크리스트 #6 "Corporate actions" 를 본 노트가 상세화
- [[tax-automation]] — 배당세 15.4%, 양도차익 통산 계산과 연결
- [[20-position-sizing]] — 사이징의 volatility 추정 시 수정주가 사용
- [[kill-switch-runbook]] — 합병·상장폐지 이벤트 시 긴급 청산 절차

---

## 출처

- KRX — Corporate Action 가이드. <https://open.krx.co.kr/contents/OPN/02/02010000/OPN02010000.jsp>
- KRX KIND (한국거래소 공시 시스템). <https://kind.krx.co.kr/>
- 금융감독원 DART. <https://opendart.fss.or.kr/>
- pykrx GitHub — Corporate action fields. <https://github.com/sharebook-kr/pykrx>
- Investopedia — *Corporate Action Overview*. <https://www.investopedia.com/terms/c/corporateaction.asp>
- ISDA — *Equity Derivatives Definitions* (합병·스핀오프 시장 관행). <https://www.isda.org/bookstore/equity-derivatives-definitions/>
- 자본시장법 (FSCMA) — 권리락·배당락 규정
- Bloomberg — *Corporate Actions Reference Data* (산업 표준 분류 참조)
- 삼정 KPMG (2023). *합병·분할 회계처리 가이드*. 국내 합병비율 산정 관행
- Lee, D. (2018). *Korean Stock Market Split Effects* — 학술 논문. 국내 분할 이벤트 이례 수익률 분석
