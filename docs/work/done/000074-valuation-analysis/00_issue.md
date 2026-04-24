# feat: 기업가치 분석 (밸류에이션) research + KIS API 재무 조회 연동

## 사용자 관점 목표
"이 기업은 매출 대비 주가가 낮다 / 투자할 만하다" 를 정량 평가할 수 있는 밸류에이션 지표·스크리닝 체계를 프로젝트에 도입한다. KIS API 로 실시간 재무 데이터를 조회해 전략의 종목 필터링·신호 보강에 활용한다.

## 배경
- `13-feature-alpha-catalog` §2 에 가치(Value) 팩터 (E/P, B/P, FCF/EV, S/P) 와 퀄리티(Quality) (ROE, ROIC, GrossProfit/Asset) 가 **한 줄씩** 만 언급
- 상세 공식·해석 기준·KRX 특수성·스크리닝 조합 방법론 없음
- KIS API (`10-broker-api-comparison` 선정) 는 재무비율·PER/PBR/EPS/BPS 조회 엔드포인트 제공 — 아직 미연동
- OpenDART API 로 분기 재무제표 원본 수집 가능 (`13-feature-alpha-catalog` §4 언급)

## 완료 기준

### A. 신규 research 노트
- [ ] `docs/background/31-valuation-analysis.md` — 밸류에이션 지표 전문 research:
  - 가격 지표 6종 (PER·PBR·PSR·EV/EBITDA·EV/Sales·PCR) 공식·해석·업종별 기준값
  - 수익성 지표 4종 (ROE·ROA·영업이익률·부채비율) 공식·해석
  - 성장성 지표 (매출 성장률·EPS 성장률·영업이익 성장률)
  - 배당 지표 (배당수익률·배당성향·배당 성장률)
  - 복합 스크리닝 조합 예시 3종 (저평가 우량주 / 성장 가치주 / 고배당 안전주)
  - KRX 특수성: 지주사 더블카운팅·재벌 내부거래 ROE 왜곡·밸류업 프로그램·별도재무↔연결재무
  - 데이터 소스: KIS API 재무비율 엔드포인트 + OpenDART API + pykrx
  - 출처 (투자론 교과서·KRX 공시 기준·Damodaran 등) 필수

### B. KIS API 재무 데이터 조회 연동
- [ ] KIS API 의 재무비율 조회 엔드포인트 (`/uapi/domestic-stock/v1/finance/financial-ratio`) 확인·테스트
- [ ] `src/data/` 또는 적절한 위치에 재무 데이터 수집 모듈 스텁 (PER·PBR·EPS·BPS·배당수익률)
- [ ] `data-lake-schema` 의 `factor` 테이블에 fundamental 팩터 적재 경로 설계

### C. 기존 노트 보강
- [ ] `13-feature-alpha-catalog` §2 가치·퀄리티 팩터 설명을 `[[31-valuation-analysis]]` 로 상세 링크
- [ ] `20-position-sizing` — 밸류에이션 기반 필터가 사이징 전 universe 축소에 활용되는 경로 명시

### D. 검증
- [ ] `scripts/check_invariants.py --strict` 통과
- [ ] 각 노트 하단 출처 명시

## 구현 플랜
1. 볼트 사전조회 ✅ (기존 커버: 13-feature-alpha-catalog §2 한 줄씩 — 상세 gap 확인)
2. `31-valuation-analysis.md` research 집필
3. KIS API 재무비율 엔드포인트 조사·테스트 스크립트
4. 기존 노트 백링크 보강
5. 불변식 통과 확인

## 개발 체크리스트
- [ ] 해당 디렉토리 .ai.md 최신화
- [ ] 불변식 위반 없음


## 작업 내역

