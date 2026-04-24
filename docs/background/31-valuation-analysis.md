---
type: research
id: 31-valuation-analysis
name: "기업가치 분석 — 가격·수익성·성장성·배당 지표 + KRX 특수성"
sources:
  - https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html
  - https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pbvdata.html
  - https://dart.fss.or.kr/
  - https://open.krx.co.kr/
  - https://pykrx.readthedocs.io/
---

# 기업가치 분석 — 가격·수익성·성장성·배당 지표 + KRX 특수성

> [[20-position-sizing]] §8 은 universe filter 에서 "저평가 + 고품질" 종목 선별을 전제한다.
> [[13-feature-alpha-catalog]] §2 는 Value·Quality 팩터를 열거하지만 공식·기준값은 본 노트에서 정리.
> [[26-point-in-time-data]] §4 는 announce_date vs period_end 분리의 중요성을 설명한다.

---

## 1. 가격 지표 6종

### 1-1. PER (주가수익비율)

**공식**: PER = 주가 / EPS (희석 후)

**해석**: 낮을수록 저평가 후보. 음수 EPS 시 무의미.

| 업종 | Damodaran 글로벌 중앙값 (2024) |
|------|-------------------------------|
| 전체 시장 | ~18× |
| 소비재 (필수) | ~22× |
| IT 하드웨어 | ~24× |
| 철강·소재 | ~12× |
| 금융 | ~11× |

> **sourceless disclaimer**: 위 업종별 수치는 Damodaran 연간 데이터셋
> (https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html)
> 2024년 글로벌 중앙값 기준. KRX 업종 분류(GICS 변환)와 1:1 대응하지 않으므로
> 직접 다운로드 후 업종 재매핑 필요.

**KRX 적용 시 주의**: 코스피200 전체 PER 는 KRX 통계월보
(https://open.krx.co.kr/) 에서 월별 공시. 지주사 연결 PER는 §6 참조.

---

### 1-2. PBR (주가순자산비율)

**공식**: PBR = 주가 / BPS (총자본 / 발행주식수)

**해석**: 1× 미만 → 청산가치 이하 거래. ROE 낮은 업종에서 구조적으로 낮음.

| 업종 | Damodaran 글로벌 중앙값 (2024) |
|------|-------------------------------|
| 전체 시장 | ~2.5× |
| 금융 | ~1.1× |
| 유틸리티 | ~1.4× |
| IT 소프트웨어 | ~5.0× |

> **sourceless disclaimer**: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pbvdata.html
> 2024년 글로벌 중앙값. KRX 평균 PBR은 역사적으로 글로벌 대비 20-30% 할인
> ("코리아 디스카운트") 경향 — 단일 URL 인용 불가, 밸류업 프로그램 섹션(§6) 참조.

---

### 1-3. PSR (주가매출비율)

**공식**: PSR = 시가총액 / 매출액 (최근 12개월 TTM)

**해석**: 적자 기업 비교에 유용. 일반적으로 1× 이하 저평가 신호로 사용하나 업종 편차 큼.

> **sourceless disclaimer**: Damodaran PSR 데이터셋
> (https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/psdata.html)
> — 전체 시장 중앙값 약 1.5×(2024). IT·바이오는 5×+ 도 "정상".

---

### 1-4. EV/EBITDA

**공식**: EV = 시가총액 + 순부채 (총부채 − 현금·현금등가물)
EBITDA = 영업이익 + D&A

**해석**: 자본구조·세율 무관 비교. M&A 스크리닝 표준 지표. 8× 이하 저평가 통용 기준.

> **sourceless disclaimer**: "8× 이하" 는 광범위하게 통용되나 단일 공식 출처 없음.
> Damodaran EBITDA multiple 파일: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/vebitda.html

---

### 1-5. EV/Sales

**공식**: EV / 매출액 (TTM)

**해석**: PSR 의 EV 버전. 부채 레버리지 높은 업종(통신·유틸리티) 비교 시 PSR 보완.

> **sourceless disclaimer**: Damodaran EV/Sales 데이터
> https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/vebitda.html 동일 파일.

---

### 1-6. PCR (주가현금흐름비율)

**공식**: PCR = 주가 / CFPS (영업현금흐름 / 발행주식수)

**해석**: EPS 조작 가능성이 높은 신흥시장 분석 시 EPS 대체재.

> **sourceless disclaimer**: PCR 업종별 기준값 단일 글로벌 DB 없음.
> 한국 맥락: OpenDART XBRL 재무제표 현금흐름표 → `영업활동으로인한현금흐름` 사용.

---

## 2. 수익성 지표 4종

### 2-1. ROE (자기자본이익률)

**공식**: ROE = 당기순이익 / 평균 자기자본

**해석**: 15% 이상 우량주 기준으로 널리 사용.

> **sourceless disclaimer**: "15% 이상" 은 교과서적 통용값이나 단일 URL 인용 불가.
> Magic Formula(§5-1) 원저 Greenblatt (2006) 는 ROIC 기준 사용.

**KRX 주의**: 재벌 계열사 내부거래(예: 삼성전자 → 삼성디스플레이 이익 이전) 로 ROE 왜곡 가능. §6 참조.

---

### 2-2. ROA (총자산이익률)

**공식**: ROA = 당기순이익 / 평균 총자산

**해석**: 금융업(높은 레버리지)은 ROA 1-2% 도 정상. 제조업 5% 이상 우량 기준.

> **sourceless disclaimer**: 업종별 ROA 임계값 단일 출처 없음.
> Damodaran 업종별 ROA: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/roe.html

---

### 2-3. 영업이익률

**공식**: 영업이익률 = 영업이익 / 매출액

**해석**: 10% 이상 우량 제조업 기준으로 통용.

> **sourceless disclaimer**: "10% 이상" 교과서 통용값. 업종 편차 매우 큼(IT 30%+ vs 유통 2-3%).

---

### 2-4. 부채비율

**공식**: 부채비율 = 총부채 / 자기자본

**해석**: 200% 이하 안정적 기준. 금융업 제외. 건설·해운은 구조적으로 높음.

> **sourceless disclaimer**: "200% 이하" 는 한국 금융감독원 여신심사 기준에서 파생된 통용값이나
> 공식 단일 URL 인용 불가. 금융감독원 기업공시시스템: https://dart.fss.or.kr/

---

## 3. 성장성 지표

### 3-1. 매출 성장률

**공식**: YoY 매출 성장률 = (당기 매출 − 전기 매출) / 전기 매출

최근 3년 CAGR = (최근 매출 / 3년 전 매출)^(1/3) − 1

**해석**: 10% 이상 고성장 기준. CAGR 7% 이상 장기 우량.

> **sourceless disclaimer**: 임계값 단일 출처 없음.

---

### 3-2. EPS 성장률

**공식**: EPS 성장률 = (당기 희석 EPS − 전기 희석 EPS) / |전기 희석 EPS|

**해석**: 전기 EPS 음수 시 무의미. PEG = PER / EPS 성장률 — 1.0 이하 저평가 신호.

> **sourceless disclaimer**: PEG 기준 "1.0 이하" 는 Peter Lynch 원저 (1989, *One Up on Wall Street*)
> 에서 유래한 통용값이나 URL 불가.

---

### 3-3. 영업이익 성장률

**공식**: YoY 영업이익 성장률

**해석**: 매출 성장 없이 영업이익 개선 → 원가구조 개선 신호. F-Score(§5-2) 에 포함.

---

## 4. 배당 지표

### 4-1. 배당수익률

**공식**: 배당수익률 = 연간 배당금 / 주가

**해석**: 3% 이상 고배당 기준 (KRX 코스피200 평균 약 2%대).

> **sourceless disclaimer**: "3% 이상" 통용값. KRX 배당통계:
> https://open.krx.co.kr/contents/OPN/01/01040200/OPN01040200.jsp

---

### 4-2. 배당성향

**공식**: 배당성향 = DPS / EPS

**해석**: 30-60% 지속가능 구간. 100% 초과 → 유보이익 소진, 감배 위험.

> **sourceless disclaimer**: "30-60%" 구간은 교과서 통용값. 단일 URL 인용 불가.

---

### 4-3. 배당 성장률

**공식**: 배당 CAGR = (현재 DPS / N년전 DPS)^(1/N) − 1

**해석**: 5년 연속 배당 증가 + CAGR 5% 이상 → "배당귀족" 후보 기준(§5-3 참조).

> **sourceless disclaimer**: 미국 S&P 배당귀족 기준(25년 연속 증가)의 한국판 완화 기준.
> KRX 공식 "배당귀족" 지수는 없음.

---

## 5. 복합 스크리닝 3종

### 5-1. Magic Formula 변형 (Greenblatt 한국판)

**원리**: 고 ROIC + 저 EV/EBIT 종목 동시 상위권 → 매수

**KRX 적용 변형**:
- ROIC → ROE (OpenDART XBRL 기준, ROIC 산출 어려움)
- EV/EBIT → EV/EBITDA (D&A 별도 추출 난이도)
- 금융업·지주사 제외 (§6 이유)
- 스크리닝 후 시가총액 500억 이상 유동성 필터

**단계**:
1. 전체 종목 ROE 역순위 (높을수록 1등)
2. 전체 종목 EV/EBITDA 순위 (낮을수록 1등)
3. 두 순위 합산 → 하위 30개 매수

> **sourceless disclaimer**: Greenblatt, J. (2006). *The Little Book That Still Beats the Market*.
> URL: 상업 출판물, 직접 링크 없음.

---

### 5-2. F-Score 단순판 (Piotroski 9점 중 5점)

**원리**: 수익성 + 레버리지 개선 + 운전자본 효율 9개 이진 신호 합산

**단순화 5개 신호** (구현 용이성 기준):

| 신호 | 조건 | 점수 |
|------|------|------|
| F1 | ROA > 0 | 1 |
| F2 | 영업현금흐름 > 0 | 1 |
| F3 | ROA 전년 대비 증가 | 1 |
| F4 | 부채비율 전년 대비 감소 | 1 |
| F5 | 영업이익률 전년 대비 증가 | 1 |

4점 이상 → 매수 후보.

> 원저: Piotroski, J.D. (2000). "Value Investing: The Use of Historical Financial Statement
> Information to Separate Winners from Losers." *Journal of Accounting Research*.
> URL: https://www.jstor.org/stable/2672906 *(sourceless disclaimer: JSTOR 유료)*

---

### 5-3. 배당귀족 한국판

**기준** (KRX 공식 없음, 본 프로젝트 정의):
1. 최근 5년 연속 배당 실시
2. 배당 CAGR ≥ 5% (5년)
3. 배당성향 30-70%
4. 부채비율 ≤ 200%
5. 시가총액 ≥ 1,000억

> **sourceless disclaimer**: 미국 S&P Dividend Aristocrats (25년 연속, S&P500 구성원)의
> 한국판 완화 버전. 공식 KRX 배당귀족 지수는 존재하지 않음.

---

## 6. KRX 특수성

### 6-1. 지주사 더블카운팅

지주사(예: 삼성물산, LG, SK㈜)는 자회사 지분가치를 연결 재무제표에 포함하므로
**지주사 + 자회사를 동시에 포트폴리오에 담으면 동일 자산을 이중 계상**한다.

완화: 지주사 필터 제외 또는 NAV 할인율 적용. KRX GICS 코드 `551010` (지주사) 필터링.

---

### 6-2. 재벌 내부거래 ROE 왜곡

삼성·SK·현대 등 대기업 그룹 계열사 간 내부거래는 일부 계열사의 ROE 를 인위적으로
높이거나 낮춘다. OpenDART 사업보고서 "특수관계자 거래" 주석 확인 필요.

**실용적 대응**: 재벌 계열사는 독립법인 ROE 와 함께 그룹 연결 ROE 병행 검토.

---

### 6-3. 밸류업 프로그램 2024~

2024년 2월 금융위원회가 "코리아 밸류업 프로그램" 발표. 기업들이 자발적으로
PBR·ROE 개선 계획을 공시하도록 유도. KRX 밸류업 지수(2024년 9월 출시) 편입 종목
리레이팅(re-rating) 효과 관찰 중.

> 공식 안내: https://www.fsc.go.kr/ (금융위원회) + https://open.krx.co.kr/
> KRX 밸류업 지수: https://open.krx.co.kr/contents/OPN/01/01040204/OPN01040204.jsp
> *(sourceless disclaimer: 편입 효과 실증 데이터는 2026-04-24 기준 아직 단기)*

---

### 6-4. 별도 vs 연결 재무제표

| 구분 | 내용 | 스크리닝 적용 |
|------|------|--------------|
| 별도 | 해당 법인만 | 지주사 ROE 왜곡 분석 |
| 연결 | 자회사 포함 | 일반 스크리닝 기본값 |

OpenDART API 는 `reprt_code` 파라미터로 구분:
- 1분기보고서: `11013`, 반기보고서: `11012`, 3분기보고서: `11014`, 사업보고서: `11011`

**기본값**: 연결 사업보고서(`11011`) 사용 권장.

---

### 6-5. 공시 지연 60일

한국 상장사 사업보고서 제출 법정 기한: 사업연도 종료 후 **90일** (코스피·코스닥 동일).
실무적으로 3월 말 집중 제출 → 4월 초까지 지연 가능.

**PIT 구현 시 중요**: 재무 데이터를 announce_date 기준으로 사용해야 look-ahead bias 방지.
→ [[26-point-in-time-data]] §4 참조.

> 출처: 자본시장법 제159조 (사업보고서 제출). 
> https://dart.fss.or.kr/info/main.do (DART 공시 일정 확인 가능)

---

## 7. 데이터 소스

| 소스 | 제공 데이터 | 접근 방법 |
|------|------------|-----------|
| **KIS API** | PER·PBR·EPS·BPS·배당수익률·ROE (실시간/일별) | `/uapi/domestic-stock/v1/finance/financial-ratio` TR-ID 조회 |
| **OpenDART** | XBRL 재무제표 (원문), 사업보고서 전 항목 | https://opendart.fss.or.kr/ REST API (`fnlttSinglAcntAll`) |
| **pykrx** | 시가총액·PER·PBR·배당수익률 (일별 마켓 데이터) | `from pykrx import stock; stock.get_market_fundamental_by_date(...)` |

**PIT 정합성**:
- KIS: 실시간 마켓 데이터 기준 (공시 반영 시차 있음)
- OpenDART: `rcept_dt` (접수일) 를 announce_date 로 사용
- pykrx: 거래일 기준 마켓 팩터 (look-ahead 없음)

---

## 8. Out-of-scope

본 노트는 다음 주제를 **의도적으로 제외**한다:

- **DCF (현금흐름 할인)**: 할인율 추정(WACC) 및 영구성장률 가정 민감도 → 별도 이슈
- **잔여이익모형 (RIM / EBO)**: 회계 발생주의 조정 필요 → 별도 이슈
- **Real-options valuation**: 옵션 가격결정 모델 → 별도 이슈
- **FnGuide 상용 벤더**: 라이선스 미보유 → 별도 이슈

---

## 출처

1. Damodaran, A. (2024). *Valuation Multiples by Sector*. NYU Stern.
   - PER: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pedata.html
   - PBR: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/pbvdata.html
   - EV/EBITDA: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/vebitda.html
   - ROE/ROA: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/roe.html

2. KRX (한국거래소). *KRX 통계 + 밸류업 지수*. https://open.krx.co.kr/

3. OpenDART (금융감독원 전자공시). *XBRL 재무제표 API*. https://opendart.fss.or.kr/

4. Piotroski, J.D. (2000). "Value Investing: The Use of Historical Financial Statement
   Information to Separate Winners from Losers." *Journal of Accounting Research* 38(S1).
   https://www.jstor.org/stable/2672906

5. 금융위원회. (2024). *코리아 밸류업 프로그램 안내*. https://www.fsc.go.kr/

6. pykrx 라이브러리. https://pykrx.readthedocs.io/
