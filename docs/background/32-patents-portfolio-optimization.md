---
type: research
id: 32-patents-portfolio-optimization
name: "포트폴리오 최적화 특허 조사 — CVaR·리스크 패리티·계층적 클러스터링·공분산 추정"
sources:
  - https://patents.google.com/patent/US20210110479A1/en
  - https://patents.google.com/patent/US20140081888A1/en
  - https://patents.google.com/patent/US11562281B2/en
  - https://patents.google.com/patent/US10664914B2/en
  - https://patents.google.com/patent/KR101139626B1/en
---

# 포트폴리오 최적화 특허 조사 — CVaR·리스크 패리티·계층적 클러스터링·공분산 추정

> ⚠️ **법적 고지**: 본 노트는 학술·회피설계 목적 조사이며 변리사 리뷰가 아님.
> 상용 서비스 전 법무 검토 필수.
> 관련 노트: [[19-portfolio-risk]], [[20-position-sizing]] — 우리 시스템 강화 및 침해 리스크 제거 목적.

---

## 1. 조사 범위

본 노트는 [[19-portfolio-risk]] (#70 CVaR·ENB·공분산 추정)와 [[20-position-sizing]] (#69 Kelly·HRP·ERC)의 구현을 강화할 수 있는 포트폴리오 최적화 관련 특허 4~5건을 조사한다.

검색 키워드: CVaR, risk parity, equal risk contribution (ERC), hierarchical risk parity (HRP), covariance shrinkage, portfolio optimization, minimum variance.

검색 데이터베이스: Google Patents (patents.google.com), KIPRIS (kpat.kipris.or.kr).

조사 대상 특허 5건:
1. **US20210110479A1** — Axioma Inc, 계층적 CVaR 포트폴리오 최적화 (공개 2021, 등록 활성)
2. **US20140081888A1** — Goldman Sachs, 리스크 패리티 포트폴리오 구성 (공개 2014, 포기·특허 만료)
3. **US11562281B2** — IBM, 계층적 클러스터링 + 양자컴퓨터 포트폴리오 최적화 (등록 2023, 활성)
4. **US10664914B2** — AIG→Validus Holdings, CVaR 기반 포트폴리오 평가 도구 (등록 2020, 활성)
5. **KR101139626B1** — 우리투자증권, 운용 프로세스 기반 포트폴리오 리스크 평가 (등록 2012, 활성)

---

## 2. 특허 1 — US20210110479A1 (Axioma Inc)

### 2.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US20210110479A1 |
| 제목 | Methods and apparatus employing hierarchical conditional variance to minimize downside risk of a multi-asset class portfolio |
| 출원인 | Axioma Inc (현 Qontigo / MSCI 자회사) |
| 출원일 | 2020-12-23 |
| 공개일 | 2021-04-15 |
| 법적 상태 | 등록 (Active) |
| Google Patents | https://patents.google.com/patent/US20210110479A1/en |

### 2.2 청구항 핵심 요약

독립항 1은 세 구성요소로 구성된다:

- **(a) 시나리오 생성**: Monte Carlo 시뮬레이션으로 다자산 클래스 포트폴리오의 수익률 시나리오를 생성 (비정규·비대칭 분포 지원)
- **(b) 계층적 CVaR 최적화**: Rockafellar-Uryasev 볼록 프로그래밍을 확장하여 **복수의 신뢰수준(confidence level)을 우선순위 순서로 동시 최적화** — 단일 α-CVaR 제약이 아닌 계층(hierarchy) 구조
- **(c) 대화형 GUI**: 여러 수익률 분포를 겹침 없이 동적 표시하는 인터페이스 — 복수의 헤징 전략을 실시간 비교

핵심 차별점: 기존 CVaR 최소화가 단일 신뢰수준(예: α=97.5%)에서 꼬리손실을 제한하는 데 반해, 본 특허는 α₁>α₂>... 순서로 계층화된 CVaR 목표를 동시에 만족하는 포트폴리오를 도출한다.

### 2.3 💎 강화 제안 (Strengthening Proposal)

**제안 A: 다중 신뢰수준 CVaR 계층화**

- **적용 대상**: `src/risk/portfolio_orchestrator.py` (현재 `historical_cvar(α=0.975)` 단일 수준)
- **접목 방법**: `PortfolioOrchestrator.compute_risk_report()` 또는 동등 메서드에서 현재 단일 α=0.975 CVaR 계산을 `[(0.95, 'warn'), (0.975, 'reduce'), (0.99, 'halt')]` 형태의 계층 구조로 확장한다. 각 레벨마다 [[risk-rule-dsl]]의 `per_portfolio_risk` 블록에 대응하는 액션(warn→reduce→halt)을 매핑하면 손실 분포의 상이한 꼬리 구간을 단계적으로 제어할 수 있다. 구현은 기존 `historical_cvar` 루프를 α 리스트로 파라미터화하는 것으로 충분하며 외부 의존성 추가 없이 가능하다.
- **기대 효과**: 97.5% CVaR 임계치 도달 전 95% CVaR 수준에서 조기 경보를 발생시켜 급격한 할트 없이 점진적 리스크 감축이 가능해진다. 규제 보고 (Basel FRTB 97.5% ES + 내부 모니터링 95% ES 병행)와도 일치한다.
- **저비용 검증**: 기존 `tests/test_portfolio_orchestrator.py`에 α 리스트를 파라미터로 받는 테스트 케이스 1건 추가 후 threshold별 액션 분기 확인.

### 2.4 차용 아이디어 메모

- 계층적 CVaR 구조를 [[risk-rule-dsl]] YAML에 `cvar_levels` 배열로 노출하면 전략별로 독립적인 임계 계층을 설정 가능.
- Monte Carlo 시나리오 생성 부분은 현재 Historical 방식의 보완으로 v3 로드맵에 추가 고려 가능 (단, 구현 비용 높음).

### 2.5 회피 필요 영역

독립항의 **GUI 컴포넌트 (c)**는 본 프로젝트에 없으므로 해당 청구항 범위 밖. 계층적 CVaR 수식 자체는 수학적 공지기술(Rockafellar-Uryasev 2000 공개 논문)이라 특허 보호 범위가 새로운 구조적 결합에 한정된다. 우리는 GUI 없이 백엔드 계산만 구현하므로 청구항 전체 구성요소 미충족 → 비침해.

대체 설계: α 계층을 `risk_levels` 설정값으로 외부화하여 특정 신뢰수준 조합과의 동일성을 피한다.

### 2.6 우리 코드 연결고리

- `src/risk/portfolio_orchestrator.py` — CVaR 계산 모듈 (현재 α=0.975 단일값, #70 구현)
- [[19-portfolio-risk]] §4.3 CVaR 최적화 이론 근거 (Rockafellar-Uryasev LP)
- [[risk-rule-dsl]] `per_portfolio_risk.max_cvar_pct` 임계값 설정

---

## 3. 특허 2 — US20140081888A1 (Goldman Sachs, 포기됨)

### 3.1 서지 정보

| 항목 | 내용 |
|------|------|
| 공개번호 | US20140081888A1 |
| 제목 | Methods And Systems For Constructing Risk Parity Portfolios |
| 출원인 | Goldman Sachs and Co LLC |
| 출원일 | 2012-09-14 |
| 공개일 | 2014-03-20 |
| 법적 상태 | **포기 (Abandoned)** — 특허 없음, 자유 실시 가능 |
| Google Patents | https://patents.google.com/patent/US20140081888A1/en |

### 3.2 청구항 핵심 요약

독립항 1: 프로세서가 투자 선택과 비중 제약을 입력받아 **각 투자 구성요소가 대략 동등한 양의 금융 리스크를 기여하도록** 최적 배분을 결정하는 방법.

독립항 11: 경계 제약이 있는 볼록 최적화 문제를 최소화하여 "근사 리스크 패리티 포트폴리오"를 생성 — 전역 최적해를 보장하는 순볼록(strictly convex) 정식화.

독립항 16: 포트폴리오 구성 방법론을 실행하는 시스템 청구항.

핵심: ERC(Equal Risk Contribution) 조건 `w_i·(Σw)_i = w_j·(Σw)_j`를 볼록 최적화로 풀되, 비볼록 원문제(non-convex)를 볼록 근사(strictly convex surrogate)로 대체하여 수치 안정성을 확보하는 방법을 청구한다.

포기된 특허이므로 청구항 내용은 선행기술(prior art)로 자유 활용 가능.

### 3.3 💎 강화 제안 (Strengthening Proposal)

**제안 B: ERC 볼록 근사 수식 도입으로 position_sizer.py 수치 안정성 강화**

- **적용 대상**: `src/risk/position_sizer.py` (현재 `ERC` 또는 `HRPOpt` 계열 함수)
- **접목 방법**: [[20-position-sizing]] §4.2의 ERC 조건은 비볼록 방정식으로 수치 최적화에서 지역 최솟값에 빠질 수 있다. Goldman Sachs가 포기한 이 특허의 볼록 근사 접근법 — 원문제를 `min Σ_i (w_i·(Σw)_i - target)²` 형태의 순볼록 문제로 재정식화하는 방식 — 을 `position_sizer.py`의 ERC 계산 경로에 적용한다. PyPortfolioOpt의 `EfficientRisk` 또는 `cvxpy` 로 직접 구현 가능하며, 고유한 전역 해를 보장하므로 최적화 실패(`solver_error`) 빈도를 낮출 수 있다. 포기 특허이므로 법적 리스크 없음.
- **기대 효과**: 종목 수 증가(20→50종목) 시 ERC 수렴 실패율 감소, 포트폴리오 리밸런스 안정성 향상.
- **저비용 검증**: 현재 ERC 구현에 50종목 스트레스 테스트 추가, 볼록 근사 버전과 수렴 실패 횟수 비교.

### 3.4 차용 아이디어 메모

- 비중 하한·상한 제약(`weight bounds`)을 ERC 최적화에 통합하는 방식도 이 특허에서 차용 가능 — `per_position.max_weight_pct` YAML 제약과 직접 연결됨.

### 3.5 회피 필요 영역

포기된 특허이므로 침해 리스크 없음. 동일 방법론의 다른 특허(예: Bridgewater 관련)가 있을 수 있으므로 상용화 시 추가 확인 권장.

대체 설계: 이미 포기 특허이므로 대체 설계 불필요. 단, 수식을 그대로 복제하지 않고 `position_sizer.py`의 자체 정의로 독립적으로 구현.

### 3.6 우리 코드 연결고리

- `src/risk/position_sizer.py` — ERC 계산 경로 (#69 구현)
- [[20-position-sizing]] §4 Risk Parity / ERC 이론 근거
- [[19-portfolio-risk]] §2.2 Ledoit-Wolf 공분산 (ERC의 Σ 입력)

---

## 4. 특허 3 — US11562281B2 (IBM)

### 4.1 서지 정보

| 항목 | 내용 |
|------|------|
| 등록번호 | US11562281B2 |
| 제목 | Hierarchical portfolio optimization using clustering and near-term quantum computers |
| 출원인 | International Business Machines Corporation |
| 출원일 | 2019-10-31 |
| 공개일 | 2023-01-24 |
| 법적 상태 | 등록 (Active), 만료 예정 2041-04-04 |
| Google Patents | https://patents.google.com/patent/US11562281B2/en |

### 4.2 청구항 핵심 요약

독립항은 5 구성요소를 포함한다:

- **(a) 분석기**: 정수 제약(integer constraint) 또는 단주(odd-lot) 거래 제한이 있는 투자 유니버스를 검사
- **(b) 클러스터링**: M개 자산을 계층적 클러스터링으로 소규모 서브클러스터로 분할
- **(c) 배분기**: 퀀텀 프로세서로 실행 가능한 크기가 될 때까지 재귀적으로 자본을 서브클러스터에 분배
- **(d) 전송기**: 서브클러스터를 양자 프로세서로 전송
- **(e) 컴퓨팅**: 각 서브클러스터에 VQE/QAOA 알고리즘으로 이진/혼합정수 평균-분산 최적화 수행

핵심 차별점: 대형 포트폴리오를 클러스터 단위로 분해하여 qubit 수가 제한된 근거리 양자컴퓨터(NISQ)로 실행 가능하게 만드는 하이브리드 클래식-양자 접근법.

### 4.3 💎 강화 제안 (Strengthening Proposal)

**제안 C: 계층적 클러스터 분해를 활용한 대형 유니버스 포트폴리오 최적화 확장**

- **적용 대상**: `src/risk/position_sizer.py`의 HRP 관련 함수 (현재 `HRPOpt` 기반 구현), 그리고 [[20-position-sizing]] §5 에서 언급한 재귀적 이등분(Recursive Bisection) 알고리즘
- **접목 방법**: IBM 특허의 클러스터 분해 아이디어(양자 컴퓨팅 부분 제외)를 고전 최적화에 그대로 적용할 수 있다. 현재 HRP는 전체 공분산 행렬을 한 번에 처리하지만, 종목 수 N>100이 되면 계층적 클러스터링 → 서브클러스터별 평균-분산(또는 CVaR) 최적화 → 클러스터 간 HRP로 재귀 합산하는 **2단계 분해 구조**로 전환하면 계산 복잡도를 O(N³) → O(k·(N/k)³)으로 낮출 수 있다(k = 클러스터 수). 양자컴퓨터 전송 컴포넌트는 생략하고 클러스터별 `cvxpy` 최적화로 대체.
- **기대 효과**: KOSPI 전종목(N≈2500) 또는 팩터 알파 파이프라인이 확장될 경우 현재 단일 HRP 호출의 메모리·시간 비용을 선형 축소. 또한 서브클러스터가 섹터·팩터 경계와 일치하면 포트폴리오 해석 가능성(interpretability)이 높아진다.
- **저비용 검증**: N=200 모의 유니버스에서 단일 HRP vs 2단계 클러스터-HRP 샤프 비율 및 실행 시간 비교 (100회 롤링 백테스트).

### 4.4 차용 아이디어 메모

- 서브클러스터 크기 제한을 `position_sizer.py`의 설정값(`max_cluster_size`)으로 노출하면 향후 실제 양자 하드웨어 연동 시에도 동일 코드 사용 가능 (qubit 수 제한 대응).

### 4.5 회피 필요 영역

활성 특허이므로 **양자 프로세서 전송 컴포넌트 (d)와 VQE/QAOA 실행 컴포넌트 (e)**를 구현하면 침해 가능. 우리는 고전 최적화(cvxpy/PyPortfolioOpt)만 사용하므로 (d)(e) 구성요소가 없어 청구항 전체 구성요소 미충족 → 비침해.

대체 설계: 클러스터 분해 후 서브클러스터 최적화는 반드시 고전 솔버(SCS, ECOS 등)로 한정.

### 4.6 우리 코드 연결고리

- `src/risk/position_sizer.py` — HRP 구현 (#69)
- [[14-quantum-poc-design]] — QAOA PoC와의 경계 (양자 컴포넌트는 PoC에 격리)
- [[20-position-sizing]] §5 HRP 알고리즘 3단계

---

## 5. 특허 4 — US10664914B2 (AIG→Validus)

### 5.1 서지 정보

| 항목 | 내용 |
|------|------|
| 등록번호 | US10664914B2 |
| 제목 | Portfolio Optimization and Evaluation Tool |
| 출원인 | American International Group Inc (원출원), Validus Holdings Ltd (현 권리자) |
| 출원일 | 2014-07-21 |
| 등록일 | 2020-05-26 |
| 법적 상태 | 등록 (Active), 만료 예정 2035-02-12 |
| Google Patents | https://patents.google.com/patent/US10664914B2/en |

### 5.2 청구항 핵심 요약

독립항은 3 구성요소를 포함한다:

- **(a) 데이터 구조**: N-차원 행렬에 포트폴리오 시나리오별 금융 상품 가치 저장, 1차·2차 제약 포함
- **(b) 프로세서 연산**: 행렬 전치(transpose), 이익 최대화, CVaR 계산, 허용 가능 리스크 범위 결정
- **(c) 반복 정제**: 1차 해가 리스크 한도 초과 시 2차 제약을 추가 적용하여 재계산 — 수렴할 때까지 반복

핵심 차별점: CVaR을 목적함수 및 제약으로 동시에 사용하고, 해가 리스크 한도를 충족할 때까지 제약을 단계적으로 강화하는 **반복적 제약 강화(iterative constraint tightening)** 메커니즘.

### 5.3 💎 강화 제안 (Strengthening Proposal)

**제안 D: CVaR 위반 시 반복적 제약 강화 패턴 도입**

- **적용 대상**: `src/risk/portfolio_orchestrator.py` — 현재 CVaR 임계치 초과 시 즉시 `reduce`/`halt` 액션을 트리거하는 구조
- **접목 방법**: 이 특허의 반복 정제 패턴을 차용하여, CVaR 위반 발생 시 즉시 `halt` 대신 **점진적 포지션 축소 루프**를 도입한다. 예: `max_cvar_pct` 임계 초과 → 가장 큰 CVaR 기여 종목을 5% 축소 → CVaR 재계산 → 여전히 초과 → 추가 5% 축소 → 최대 N회 반복 후에도 미충족이면 `halt`. 이는 `portfolio_orchestrator.py`의 CVaR 체크 함수에 `reduce_loop(max_iter=10, step_pct=0.05)` 내부 루프로 구현 가능하다.
- **기대 효과**: 시장 충격 시 급격한 전량 청산 대신 점진적 포지션 감축이 가능해져 실현 슬리피지를 낮추고, 리스크 감축과 시장 충격 비용 간의 균형을 맞출 수 있다.
- **저비용 검증**: 단위 테스트에서 CVaR 임계 초과 시나리오를 시뮬레이션하고 루프 종료 조건(충족 vs. max_iter 도달) 분기 검증.

### 5.4 차용 아이디어 메모

- 행렬 전치를 통한 시나리오 집계 구조는 현재 Historical CVaR 계산(수익률 행렬 → 포트폴리오 손실 벡터 변환)과 구조적으로 유사하며, 최적화 시 메모리 접근 패턴을 개선하는 데 참고 가능.

### 5.5 회피 필요 영역

활성 특허이므로 **(a) N-차원 시나리오 행렬 + (b) CVaR 계산 + (c) 반복 제약 강화** 세 요소를 **하나의 시스템**으로 결합하면 침해 리스크. 우리 구현에서는 (c) 반복 루프를 `portfolio_orchestrator.py` 내부 로직이 아닌 [[risk-rule-dsl]]의 외부 정책으로 제어하는 구조를 유지하여 단일 시스템으로 묶이지 않도록 한다.

대체 설계: CVaR 계산(b)과 반복 정제(c)를 별도 모듈(`risk_reducer.py`)로 분리하여 단일 특허 청구항의 통합 시스템 요건과 차별화.

### 5.6 우리 코드 연결고리

- `src/risk/portfolio_orchestrator.py` — CVaR 체크 + 액션 디스패치 (#70)
- [[19-portfolio-risk]] §4.3 CVaR 최적화 (Rockafellar-Uryasev LP)
- [[risk-rule-dsl]] `per_portfolio_risk` → `reduce`/`halt` 액션

---

## 6. 특허 5 — KR101139626B1 (우리투자증권)

### 6.1 서지 정보

| 항목 | 내용 |
|------|------|
| 등록번호 | KR101139626B1 |
| 제목 | 운용 프로세스에 기반한 포트폴리오 리스크 평가 방법 |
| 출원인 | 우리투자증권 주식회사 |
| 출원일 | 2011-12-07 |
| 공개일 | 2012-04-27 |
| 법적 상태 | 등록 (Active), 만료 예정 2031-12-07 |
| KIPRIS / Google Patents | https://patents.google.com/patent/KR101139626B1/en |

### 6.2 청구항 핵심 요약

3단계 평가 프레임워크:

- **1단계 — 운용 분석**: 멀티팩터 모델로 포트폴리오 운용 프로세스 분석 (액티브 리스크 수준, 벤치마크 복제율 등)
- **2단계 — 성과 평가**: 과거 수익률에 대해 리스크 조정 성과(정보비율, 변동성) 측정
- **3단계 — 지수 합성**: 운용 프로세스 분석 결과와 성과 데이터를 종합하여 **Portfolio Risk Index (PRI)** 를 산출 — 운용 기간을 가중치로 반영하여 신뢰도 보정

### 6.3 💎 강화 제안 (Strengthening Proposal)

**제안 E: 전략별 복합 리스크 신뢰도 지수를 PortfolioOrchestrator 리포트에 통합**

- **적용 대상**: `src/risk/portfolio_orchestrator.py`의 리스크 리포트 생성 함수 (현재 CVaR·ENB·평균상관을 산출하는 `compute_risk_report()` 또는 동등 함수)
- **접목 방법**: 현재 리포트는 순수 정량 지표(CVaR 수치, ENB 비율, 평균 상관계수)만 산출한다. KR101139626B1의 PRI 개념을 차용하여 **전략별 운용 신뢰도 가중치**를 추가한다. 구체적으로, 각 전략의 수익률 시계열 길이(거래일 수), 정보비율의 통계적 유의성(t-통계량), 최근 롤링 CVaR 준수율을 입력으로 받아 `strategy_reliability_score: float` (0~1)를 산출하고 리포트에 포함한다. 신뢰도 낮은 신규 전략은 `per_portfolio_risk.min_enb_ratio` 계산 시 가중치를 낮춰 포트폴리오 ENB 계산에 영향을 줄인다.
- **기대 효과**: 데이터가 적은 신규 전략이 포트폴리오 ENB를 과도하게 낮추는 문제를 완화하고, 검증된 전략과 신규 전략 간 리스크 배분 비대칭을 명시적으로 다룰 수 있다.
- **저비용 검증**: `register_strategy_returns()` 호출 시 시리즈 길이를 기반으로 신뢰도 0.0~1.0 매핑 함수를 단위 테스트로 검증 (길이 20일 → 0.2, 250일 → 1.0 선형 구간).

### 6.4 차용 아이디어 메모

- 정보비율 t-통계량 기반 신뢰도 보정은 [[12-validation-protocol]] §5의 백테스트 검증 기준과 연계하여 "통과한 전략만 신뢰도 1.0 부여" 규칙으로 자연스럽게 확장 가능.

### 6.5 회피 필요 영역

활성 특허이나 한국 특허로 한국 영토 내 실시에 적용. 우리 구현은 PRI 지수 산출을 원특허의 3단계 구조(운용분석→성과평가→지수합성을 단일 블랙박스로)가 아닌 독립적인 신뢰도 가중치 스칼라로 단순화하므로 청구항 전체 구성요소 미충족.

대체 설계: 신뢰도 점수를 운용기간 단독 함수가 아닌 (운용기간 + 정보비율 유의성 + CVaR 준수율)의 복합 함수로 정의.

### 6.6 우리 코드 연결고리

- `src/risk/portfolio_orchestrator.py` — `register_strategy_returns()` 및 리스크 리포트 (#70)
- [[12-validation-protocol]] §5 백테스트 기준
- [[19-portfolio-risk]] §5 팩터 노출 관리 (유사 다차원 평가 맥락)

---

## 7. 종합 매트릭스

| 특허 | 법적 상태 | 💎 강화 제안 | 회피 포인트 | 연결 코드 경로 |
|------|-----------|-------------|-------------|----------------|
| US20210110479A1 (Axioma, 계층적 CVaR) | 등록·활성 | A: 다중 α CVaR 계층화 | GUI 컴포넌트 없이 백엔드만 구현 | `portfolio_orchestrator.py` |
| US20140081888A1 (Goldman, ERC) | 포기·자유 | B: ERC 볼록 근사 수치 안정화 | 자유 실시 가능 | `position_sizer.py` |
| US11562281B2 (IBM, 클러스터+양자) | 등록·활성 | C: 2단계 클러스터 HRP 분해 | 양자 프로세서 컴포넌트 생략 | `position_sizer.py` |
| US10664914B2 (AIG, CVaR 반복) | 등록·활성 | D: 점진적 포지션 축소 루프 | CVaR 계산·반복 루프 모듈 분리 | `portfolio_orchestrator.py` |
| KR101139626B1 (우리투자증권, PRI) | 등록·활성 | E: 전략 신뢰도 가중치 지수 | 3단계 통합 블랙박스 회피 | `portfolio_orchestrator.py` |

---

## 8. 우리 레포 강화 로드맵

| # | 강화 제안 | 우선순위 | 예상 난이도 | 의존 이슈 |
|---|-----------|---------|-------------|-----------|
| A | 다중 신뢰수준 CVaR 계층화 (`portfolio_orchestrator.py`) | High | 낮음 (파라미터 확장) | #70 완료 후 후속 |
| D | CVaR 위반 시 점진적 포지션 축소 루프 | High | 중간 (루프 + 슬리피지 고려) | #70 완료 후 후속 |
| B | ERC 볼록 근사 수치 안정화 (`position_sizer.py`) | Medium | 중간 (cvxpy 재정식화) | #69 완료 후 후속 |
| E | 전략 신뢰도 가중치 지수 (`portfolio_orchestrator.py`) | Medium | 낮음 (스칼라 추가) | #70 완료 후 후속 |
| C | 2단계 클러스터-HRP 분해 (대형 유니버스 대응) | Low | 높음 (재귀 분해 구조 변경) | 유니버스 N>100 확장 시 |

---

## 9. 후속 이슈 후보

**이슈 후보 I**: **다중 CVaR 계층 경보 시스템 구현** (#70 후속)
- 요약: `portfolio_orchestrator.py`에 `[(0.95,'warn'), (0.975,'reduce'), (0.99,'halt')]` 계층을 구현하고 [[risk-rule-dsl]] YAML에 `cvar_levels` 배열 노출
- 근거 특허: US20210110479A1 (강화 제안 A)
- 연결 노트: [[19-portfolio-risk]], [[risk-rule-dsl]]
- 예상 공수: 1~2일

**이슈 후보 II**: **CVaR 위반 시 점진적 포지션 감축 루프** (#70 후속)
- 요약: CVaR 임계 초과 시 즉시 halt 대신 단계적 5% 축소 루프 (`max_iter=10`)를 도입하여 시장 충격 비용 감소
- 근거 특허: US10664914B2 (강화 제안 D)
- 연결 노트: [[19-portfolio-risk]], [[kill-switch-runbook]]
- 예상 공수: 2~3일

---

## 출처

- Axioma Inc (2021). *Methods and apparatus employing hierarchical conditional variance to minimize downside risk of a multi-asset class portfolio.* US20210110479A1. https://patents.google.com/patent/US20210110479A1/en
- Goldman Sachs and Co LLC (2014). *Methods And Systems For Constructing Risk Parity Portfolios.* US20140081888A1 (포기). https://patents.google.com/patent/US20140081888A1/en
- International Business Machines Corporation (2023). *Hierarchical portfolio optimization using clustering and near-term quantum computers.* US11562281B2. https://patents.google.com/patent/US11562281B2/en
- American International Group Inc / Validus Holdings Ltd (2020). *Portfolio Optimization and Evaluation Tool.* US10664914B2. https://patents.google.com/patent/US10664914B2/en
- 우리투자증권 주식회사 (2012). *운용 프로세스에 기반한 포트폴리오 리스크 평가 방법.* KR101139626B1. https://patents.google.com/patent/KR101139626B1/en
