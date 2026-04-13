# 퀀텀 트레이딩이란 무엇인가 — 2026년 기준 현황

> 작성일: 2026-04-13 | 이슈: #3

---

## 1. 개요

퀀텀 트레이딩(Quantum Trading)은 양자 컴퓨팅(Quantum Computing) 기술을 금융 시장의 의사결정 문제에 적용하는 분야다. 고전 컴퓨터로는 지수 시간이 소요되는 조합 최적화·시뮬레이션·머신러닝 문제를 양자 하드웨어 또는 양자-고전 하이브리드 아키텍처로 가속하는 것을 목표로 한다.

핵심은 "속도"가 아니라 **문제 구조의 변환**이다. 포트폴리오 최적화처럼 변수 간 상관관계가 폭발적으로 증가하는 조합 문제, 옵션 가격 산정에 쓰이는 몬테카를로 시뮬레이션, 그리고 신용 리스크·시장 이상 탐지에 쓰이는 ML 모델 학습이 대표적인 적용 영역이다.

---

## 2. 퀀텀 트레이딩이 풀려는 문제

### 2.1 포트폴리오 최적화

포트폴리오 최적화는 $N$개 자산에 대해 수익-위험 트레이드오프를 최대화하는 가중치 벡터를 구하는 문제다. 고전적 접근은 Markowitz 평균-분산 모델이지만, 자산 수가 커지면 실현 불가능한 조합 탐색(NP-hard)이 된다.

마르코위츠 목적함수의 QUBO(Quadratic Unconstrained Binary Optimization) 표현:

$$\min_{x \in \{0,1\}^N} \left[ -\mu^\top x + \lambda \, x^\top \Sigma \, x \right]$$

- $x$: 자산 선택 이진 벡터
- $\mu$: 기대 수익률 벡터
- $\Sigma$: 공분산 행렬
- $\lambda$: 위험 회피 계수

이 QUBO 형식은 양자 어닐러(D-Wave)와 QAOA 모두 직접 처리할 수 있다.

**실험 결과**: 10개 자산 포트폴리오에서 QAOA는 고전 정확해의 97.3%에 해당하는 품질을 달성했다(Nature Scientific Reports, 2023). 양자 어닐러를 활용한 연구에서는 고전 휴리스틱 대비 10~15%의 성능 향상이 관찰되었다.

### 2.2 파생상품 가격 산정 및 리스크 시뮬레이션

몬테카를로(MC) 시뮬레이션은 옵션 가격, CVA(신용 가치 조정), VaR(리스크-앳-리스크) 계산의 핵심이다. 고전 MC의 표준 오차는 $O(1/\sqrt{M})$으로, 정밀도를 2배 높이려면 샘플 수를 4배 늘려야 한다.

**양자 진폭 추정(QAE)**은 이를 $O(1/M)$으로 개선해 이론적으로 이차적(quadratic) 속도향상을 제공한다:

$$\text{표준 오차} \propto \frac{1}{M} \quad \text{(QAE)} \quad \text{vs} \quad \frac{1}{\sqrt{M}} \quad \text{(고전 MC)}$$

JPMorgan은 유럽형 콜옵션 가격 산정 실험에서 같은 정밀도(베이시스 포인트 단위)를 달성하는 데 고전 CPU 기반 MC 대비 **100배 적은 양자 샘플**이 필요했음을 보고했다.

Goldman Sachs와 QC Ware는 IonQ 하드웨어에서 Monte Carlo 시뮬레이션의 양자 속도향상을 실증했으며, AWS와의 협업에서는 얕은 회로(shallow circuit) 구현에서 100배 속도향상을 달성했다.

### 2.3 머신러닝 가속

양자 ML(QML)은 금융 예측·이상 탐지·신용 평가에 응용된다. 주요 기법:

| 기법 | 용도 | 잠재 우위 |
|------|------|-----------|
| QSVM (양자 서포트 벡터 머신) | 사기 탐지, 신용 분류 | 커널 계산의 지수적 차원 확장 |
| VQC (변분 양자 분류기) | 시장 방향 예측 | NISQ 호환, 하이브리드 학습 |
| QPCA (양자 주성분 분석) | 리스크 팩터 추출 | 지수적 차원 압축 가능성 |

QML 시장 규모는 2024년 $11.2억에서 2025년 $15억으로 연 33.8% 성장하고 있다(IJSAT, 2025).

---

## 3. 핵심 알고리즘

### 3.1 QAOA (Quantum Approximate Optimization Algorithm)

QAOA는 조합 최적화 문제를 양자 회로로 풀기 위한 변분 알고리즘이다. 문제 해밀토니안 $H_C$와 믹서 해밀토니안 $H_B$를 교대로 적용하며, 깊이 $p$를 늘릴수록 정확도가 향상된다:

$$|\psi(\boldsymbol{\gamma}, \boldsymbol{\beta})\rangle = e^{-i\beta_p H_B} e^{-i\gamma_p H_C} \cdots e^{-i\beta_1 H_B} e^{-i\gamma_1 H_C} |+\rangle^{\otimes n}$$

- NISQ 친화적: 얕은 회로로도 근사해 탐색 가능
- Barclays: VQE·QAOA를 이용한 청산 알고리즘 개념 검증 완료
- 포트폴리오 최적화·트레이드 라우팅 등에 활발히 연구 중

### 3.2 VQE (Variational Quantum Eigensolver)

VQE는 변분 원리를 이용해 해밀토니안의 최솟값(바닥 상태 에너지)을 구한다. 원래 양자 화학용이지만 금융 최적화에 전용되고 있다:

$$E(\boldsymbol{\theta}) = \langle \psi(\boldsymbol{\theta}) | H | \psi(\boldsymbol{\theta}) \rangle \geq E_0$$

- QAOA보다 자유도가 높아 복잡한 제약 조건 표현에 유리
- VQE와 QAOA를 결합한 앙상블 기법이 위험 조정 수익률을 고전 대비 개선했다는 연구 결과 존재 (ResearchGate, 2024)
- 현재는 10~50 큐비트 수준의 소규모 문제에서만 검증됨

### 3.3 양자 몬테카를로 (QMC / QAE)

양자 진폭 추정(QAE)은 MC 샘플링의 이차적 속도향상을 제공한다. 파생상품 가격 산정(이국적 옵션, CDO 등)과 스트레스 테스트에 적용된다. 2024년 arXiv 논문은 QMC가 거시경제 딥러닝 스트레스 테스트 런타임을 단축할 수 있음을 보였다.

### 3.4 양자 어닐링 (Quantum Annealing)

D-Wave 시스템이 대표적이다. 에너지 경관에서 아디아바틱 진화로 최솟값을 찾는다:

$$H(t) = \left(1 - \frac{t}{T}\right) H_\text{초기} + \frac{t}{T} H_\text{문제}$$

- 4,000+ 큐비트 운용 (D-Wave Advantage)
- 게이트 기반 NISQ 대비 노이즈에 강하지만 범용성 제한
- 포트폴리오 선택, 트레이드 스케줄링 같은 QUBO 형식 문제에 직접 적용 가능
- 포드 오토산(Ford Otosan)은 D-Wave로 스케줄링 시간을 30분에서 5분 미만으로 단축해 실제 생산 배포에 성공한 사례 (금융 외 분야이나 하드웨어 실용성의 증거)

---

## 4. 하드웨어 지형도

### 4.1 아키텍처 비교

| 구분 | 대표 기업 | 큐비트 수 (2025) | 강점 | 약점 |
|------|-----------|----------------|------|------|
| 초전도 (게이트 기반) | IBM, Google, Rigetti | 127~16,632 (물리 큐비트) | 속도, 규모 확장성 | 단일 게이트 충실도 제한 |
| 이온 트랩 | IonQ, Quantinuum | 수십~수백 | 높은 충실도, 긴 결맞음 | 속도 느림 |
| 양자 어닐러 | D-Wave | 4,000+ | 최적화 문제 즉시 적용 | 범용성 낮음 |
| 위상 큐비트 | Microsoft | 실험 단계 | 오류 내성 가능성 | 미검증 |

### 4.2 NISQ 시대의 특징

NISQ(Noisy Intermediate-Scale Quantum)는 수십~수백 개 물리 큐비트를 보유하되 오류 정정이 불완전한 현 세대를 지칭한다(Preskill, 2018). 주요 제약:

- **게이트 충실도**: 현재 99~99.9% 수준. 오류 정정 없이 심층 회로 실행 시 누적 오류로 결과 신뢰 불가
- **결맞음 시간**: 마이크로초 단위. 복잡한 금융 계산에는 부족
- **큐비트 연결성**: 물리적 배선 제약으로 모든 큐비트 쌍이 직접 연결되지 않음

IBM은 2029년까지 오류 정정 양자 컴퓨터를 목표로 하며, IBM Quantum System Two는 최대 16,632 물리 큐비트를 지원한다. Google의 Willow 칩(2024)은 큐비트 증가 시 오류율이 감소하는 임계값 이하 오류 정정을 최초 시연했다.

---

## 5. 실험실 vs 실전 배포 현황

### 5.1 현황 요약 (2026년 4월 기준)

| 구분 | 상태 | 대표 사례 |
|------|------|-----------|
| 완전 생산 배포 | 미달성 | — |
| 파일럿 실증 | 달성 | HSBC-IBM 채권 트레이딩 (2025.09) |
| 개념 검증 | 다수 완료 | JPMorgan, Goldman Sachs, Barclays 등 |
| 연구 단계 | 활발 | 대부분의 금융사 |

**주요 데이터 포인트**:
- 전 세계 주요 금융사의 **80%**가 양자 컴퓨팅 관련 활동에 참여 (Quantum Zeitgeist, 2025)
- 생산 배포 예상 시점: 근거리 응용 3~5년, 복잡한 문제 5~10년 이상

### 5.2 실험실 수준

- 대부분의 금융 기관이 클라우드 기반 양자 프로세서 접근(IBM Quantum Network, Amazon Braket, Azure Quantum)으로 알고리즘 실험 중
- 10~50 큐비트 수준의 소규모 포트폴리오·옵션 문제에서 개념 검증 완료
- 양자 ML 모델 훈련은 소규모 데이터셋에서만 의미있는 결과

### 5.3 실전 접경 사례

파일럿 단계를 넘어 실전 데이터를 활용한 검증 사례가 2025년 등장했다.

---

## 6. 실전 사례

### 6.1 HSBC × IBM — 세계 최초 양자 지원 알고리즘 트레이딩 (2025.09)

가장 주목할 만한 이정표다.

- **과제**: 유럽 회사채 시장에서 RFQ(Request for Quote)의 체결 확률 예측
- **하드웨어**: IBM Heron 프로세서 (IBM 최신·최고 성능 양자 프로세서)
- **소프트웨어**: Qiskit
- **결과**: 업계 표준 고전 기법 대비 **체결 예측 정확도 34% 향상**
- **데이터**: 실제 생산 규모 채권 트레이딩 데이터 사용
- **의의**: "현재의 양자 컴퓨터가 실제 비즈니스 문제를 풀 수 있다는 실증적 증거" (Philip Intallura, HSBC 퀀텀 기술 총괄)

출처: [HSBC 공식 발표 (2025.09.25)](https://www.hsbc.com/news-and-views/news/media-releases/2025/hsbc-demonstrates-worlds-first-known-quantum-enabled-algorithmic-trading-with-ibm)

### 6.2 JPMorgan Chase — 포트폴리오 리밸런싱 및 네트워크

- IBM과 공동으로 포트폴리오 리밸런싱용 양자 루틴 개발
- Quantinuum과 $1억 투자 파트너십 체결
- Argonne·Oak Ridge 국립연구소와 QAOA 기반 양자 속도향상 실증 (2025.03)
- 데이터센터 간 **양자 보안 암호화 네트워크(Q-CAN)** 구축

출처: [The Quantum Insider — 15개 글로벌 은행 (2026.03)](https://thequantuminsider.com/2026/03/27/15-plus-global-banks-probing-the-wonderful-world-of-quantum-technologies/)

### 6.3 Goldman Sachs — 몬테카를로 및 QML

- QC Ware·IonQ와 협업, MC 알고리즘의 양자 가속 실증
- Amazon Web Services와 얕은 회로 MC에서 100배 속도향상 달성
- 파생상품 가격 산정 및 리스크 모델링에 집중
- Quantum Motion(영국)과 금융 서비스 양자 응용 탐색 (2024.11)

출처: [Goldman Sachs Quantum Navigator](https://entangledfuture.com/enterprise/goldman-sachs/)

### 6.4 D-Wave — 양자 어닐링 실용화

- 2025년 3분기 누적 매출 $2,180만 (전년 동기 $650만 대비 3.4배 성장)
- 2025년: 실용적 실제 문제에서 **세계 최초 양자 계산 우월성(quantum computational supremacy)** 발표
- 금융권 포트폴리오 최적화 외 물류·스케줄링에서 실제 생산 배포 사례 보유

출처: [The Motley Fool — D-Wave vs IBM (2025.12)](https://www.fool.com/investing/2025/12/04/better-quantum-computing-stock-d-wave-quantum-vs-i/)

### 6.5 기타 주요 금융사

| 기관 | 파트너 | 주요 활동 |
|------|--------|-----------|
| Barclays | IBM | VQE·QAOA 기반 청산 알고리즘 PoC (2017~) |
| BNP Paribas | Pasqal | 담보 최적화, 파생상품 가격 산정; C12에 €1,800만 투자 |
| Citigroup | QC Ware | QC Ware에 $2,500만 투자; Amazon Braket 활용 |
| Standard Chartered | Fujitsu | Qubitra Technologies JV 설립; 사기 탐지·파생상품 가격 |
| Wells Fargo | IBM | IBM Quantum Network 파트너; 10편 동료 심사 논문 게재 |
| Vanguard | IBM | 실제 제약 조건 하의 포트폴리오 구성 최적화 알고리즘 실험 |

---

## 7. 결론: 현재 수준의 정직한 평가

1. **아직 생산 배포는 없다.** 2026년 4월 현재, 완전한 운영 환경에서 양자 컴퓨터만으로 금융 의사결정을 내리는 사례는 존재하지 않는다.

2. **파일럿 단계에서 의미있는 실증이 나오고 있다.** HSBC-IBM의 34% 채권 체결 예측 개선은 실제 생산 데이터를 사용한 최초의 검증 사례다.

3. **하이브리드가 현실적 경로다.** 양자 서브루틴 + 고전 전처리/후처리 조합이 가장 빠른 실용화 경로로 수렴되고 있다.

4. **큐비트 수보다 오류율이 병목이다.** 100개 이상 자산의 실질적 포트폴리오 문제를 다루려면 논리 큐비트(오류 정정 완료)가 필요하며, 이는 2029년 이후로 예상된다.

5. **어닐러가 가장 앞서 있다.** D-Wave 어닐러는 좁은 문제 유형에서지만 실제 생산 사용 사례를 가장 많이 보유하고 있다.

---

## 출처

| 출처 | URL |
|------|-----|
| HSBC 공식 발표 (2025.09.25) | https://www.hsbc.com/news-and-views/news/media-releases/2025/hsbc-demonstrates-worlds-first-known-quantum-enabled-algorithmic-trading-with-ibm |
| IBM Quantum Blog — HSBC 채권 트레이딩 | https://www.ibm.com/quantum/blog/hsbc-algorithmic-bond-trading |
| The Quantum Insider — 15개 글로벌 은행 (2026.03) | https://thequantuminsider.com/2026/03/27/15-plus-global-banks-probing-the-wonderful-world-of-quantum-technologies/ |
| Goldman Sachs Quantum Navigator | https://entangledfuture.com/enterprise/goldman-sachs/ |
| Nature Scientific Reports — 포트폴리오 최적화 실험 | https://www.nature.com/articles/s41598-023-45392-w |
| arXiv 2604.08180 — 양자 금융 종합 리뷰 | https://arxiv.org/html/2604.08180 |
| arXiv 2407.19857 — PO-QA 프레임워크 | https://arxiv.org/html/2407.19857v1 |
| arXiv 2409.13909 — 양자 MC 경제 응용 | https://arxiv.org/html/2409.13909 |
| PatentPC — 은행 양자 기술 채택 통계 | https://patentpc.com/blog/quantum-computing-in-finance-how-banks-are-adopting-quantum-tech-latest-stats |
| The Motley Fool — D-Wave vs IBM (2025.12) | https://www.fool.com/investing/2025/12/04/better-quantum-computing-stock-d-wave-quantum-vs-i/ |
| Quantum Motion × Goldman Sachs (2024.11) | https://thequantuminsider.com/2024/11/04/quantum-motion-and-goldman-sachs-identify-quantum-applications-in-financial-services-project/ |
| IJSAT — QML 금융 예측 (2025) | https://www.ijsat.org/papers/2025/4/9033.pdf |
| The Quantum Insider — 하드웨어 지형도 (2026.02) | https://thequantuminsider.com/2026/02/23/understanding-the-quantum-computing-hardware-landscape/ |
