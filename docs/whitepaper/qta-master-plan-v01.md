---
type: whitepaper
id: qta-master-plan-v01
name: "QTA Master Plan v0.1"
version: "0.1"
owner: siwoo
created: 2026-04-26
---

# QTA Master Plan v0.1

> 한국 개인 투자자가 PC 한 대로 운영하는 규칙기반 + 소형 인공지능(메타라벨러) 자동매매 에이전트의 단일 source-of-truth 기획서.
> 기준 시점: 2026-04-26. Phase 0 백테스트 완료, Phase 1 그림자 운영 시작.
> 작성: CEO 통합 + 5인 다관점 워커(트레이더·개발자·마케터·브랜딩·VC 분석가).

---

## §0. 이 문서의 사용 안내

### §0-1. 이 문서가 답하는 다섯 질문

| # | 질문 | 핵심 답변 위치 |
|---|------|---------------|
| 1 | 누구를 위한 무엇인가 | §1-1, §1-4 |
| 2 | 무엇을 사고파는가 | §3 |
| 3 | 어떻게 결정하고 얼마나 베팅하는가 | §4, §5, §6 |
| 4 | 무엇이 잘못됐을 때 어떻게 멈추는가 | §7, §8, §10-4 |
| 5 | 마지막에 어떻게 끝나는가 | §10-2, §11 |

### §0-2. 독자별 읽는 순서

| 독자 | 권장 |
|------|------|
| 외부 투자자 / VC | §1 → §11 → §10 → 부록 A |
| 기술 검토자 | §3 → §4 → §5 → §6 → §7 → §8 → §9 |
| 정독 | §0 → §1 → ... → §11 → 부록 |

### §0-3. 약어·전문용어 표기 규칙

각 섹션 첫 등장에만 괄호로 영문·약어, 이후 평어.

| 평어 | 보조 |
|------|------|
| 운용 안정성 지표 | (Sharpe ratio) |
| 꼬리손실 평균 | (CVaR, Conditional Value at Risk) |
| 최대 누적 손실폭 | (MDD, Maximum Drawdown) |
| 유효 분산 종목 수 | (ENB, Effective Number of Bets) |
| 메타라벨러 | (Meta-Labeling, 2차 분류 필터) |
| 그림자 운영 | (Shadow Paper Trading) |
| 시간 균등 분할 | (TWAP) |
| 거래량 비례 분할 | (VWAP) |

### §0-4. 버전 정책

| 버전 | 시점 | 내용 |
|------|------|------|
| v0.1 | 2026-04-26 | Phase 0 완료 스냅샷. 결정 보류 항목은 `TBD` 명시 |
| v0.2 | 라이브 30 거래일 후 | 실거래 슬리피지 실측, 메타라벨러 성능 갱신, TBD 항목 확정 |
| v1.0 | Phase 4 정식 운영 진입 | EXE 패키징 완료, 운영 매뉴얼 통합 |

### §0-5. 본 문서 외 자료

| 디렉토리 | 내용 |
|----------|------|
| `docs/specs/` | 기능별 기술 설계 |
| `docs/background/` | 32개 리서치 노트 (§2 원본) |
| `docs/runbooks/` | 비상정지·복구 절차 |
| `docs/schemas/` | 노트 프론트매터 스키마 |
| `docs/ontology/` | RDF 도메인 온톨로지 |

위키링크(`[[id]]`)는 위 디렉토리 노트 id 와 1:1 대응.

### §0-6. 저작권

비공개 개인 프로젝트. 외부 인용·전재·파생은 사전 서면 동의 필요. 소유자: 성시우.

---

## §1. Executive Summary

### §1-1. 한 줄 정의

QTA(Quantum Trader Agent)는 한국 개인 투자자가 자기 PC 한 대로 운영하는 자동매매 에이전트다. 규칙기반 1차 신호 위에 소형 인공지능 모델(메타라벨러)을 2차 필터로 얹어 거짓 신호를 자동으로 거른다. 단일 EXE 한 번 실행으로 비트코인 선물(해외 Binance)과 한국 우량주(KRX) 두 시장을 동시에 다룬다.

### §1-2. 문제 — 한국 개인 투자자의 세 가지 페인

| 페인 | 결과 |
|------|------|
| 저금리 + 고변동성 + 정보 비대칭 (예금 연 2-3% vs 주식·코인 연 변동 15-50%) | 고위험 자산 과집중 또는 무리한 레버리지 |
| 무료 자동매매 도구 부재 (증권사 자동매매 기능 제한·해외 OSS 한국 미지원) | 직장인은 수동 매매로 한정, 거래 기회 상실 |
| 직장인의 시간 제약 (장 운영 09:00–15:30 모니터링 불가) | 감정 매매로 평균 -2~5%p 수익률 손실 |

### §1-3. 시장 규모 가설 (TBD — v0.2 검증)

| 지표 | 추정 |
|------|------|
| 한국 주식 활성 계좌 (TAM) | 약 5,400만 (2025 KRX) |
| 코인 투자 인구 | 약 600만 (5대 거래소) |
| 자동매매 도구에 관심 있는 기술 친화 투자자 (SAM) | 약 50–100만 |
| 초기 1년 확보 가능 사용자 (SOM) | 1,000–10,000 |

### §1-4. 타겟 페르소나

| 페르소나 | 특징 | QTA 가 해결 |
|---------|------|------------|
| 30대 직장인 | 월급 일부 분산 투자, 매매 시간 없음, 월 1–2% 안정 수익 희망 | 자동 매매로 하루 20분 절감, 감정 매매 제거 |
| 40대 자영업자 | 코인 경험 있으나 손절 못해 큰 손실 경험, 자동 리스크 관심 | 자동 한도·비상정지로 손실폭 제한 |

### §1-5. 해법 — 6단계 자동 파이프라인

```
실시간 시세 → 1차 신호 → 메타라벨러 → 사이징 → 리스크 검사 → 주문 실행
 (WebSocket)  (6종 지표)  (LightGBM)   (분수 켈리)   (자동 한도)   (시장가/지정가)
```

### §1-6. 기술 차별화 3가지

| # | 차별점 | 효과 |
|---|--------|------|
| 1 | 메타라벨러 — 1차 신호 위 LightGBM 2차 필터 | 거짓 신호 자동 제거 |
| 2 | 비트코인 + 한국 우량주 단일 시스템 | 24시간(BTC) + 정규장(KRX) 분산 |
| 3 | 단일 EXE + 보안 키 저장(DPAPI) | 평범한 윈도우 사용자도 다운로드·더블클릭 |

### §1-7. LLM 역할 경계 (불변식 6)

LLM 은 문서 보조·검색·드래프트만. **주문 실행과 리스크 결정에는 절대 개입하지 않는다.** 코드 레벨로 강제된다.

### §1-8. 트랙션 (2026-04-26 기준)

| 영역 | 현황 |
|------|------|
| 이슈 처리 | 64개 중 52개 머지 (81%), 12개 진행/백로그 |
| 인프라 | Obsidian 볼트 + RDF 온톨로지 + LLM 가드레일 완성 |
| 신호·전략 | 6종 기본 지표 + 3종 결정 신호 + 5종 전략 카탈로그 |
| 메타라벨러 | LightGBM 프로덕션 활성화, 주간 자동 재학습 가동 |
| 리스크·사이징 | YAML DSL 6 카테고리 + 다중 임계 꼬리손실 |
| 데이터 | KIS 분봉, Binance USDT-M 실시간/히스토리 |
| 특허 차용 | 18건 분석 → 시스템 강화 3건 적용 |
| Phase 1 | PaperBroker(#80) + Binance 데이터 로더(#106) 머지, 그림자 운영 시작 |

### §1-9. 1인 운영 리스크 완화

| 리스크 | 완화 |
|--------|------|
| 키맨 의존 | AI 에이전트 보조 (Obsidian MCP·Doc Agent·CI 자동 검증) |
| 버스 팩터 1 | 모든 결정·근거 노트화 (32 리서치 + 12 스펙 + 본 백서) |
| 외부 자문 미확보 | v0.2 에서 확보 계획 |

### §1-10. 목표 수익성

| 시나리오 | 목표 | 비고 |
|---------|------|------|
| **사용자 지정 최종 목표** | **월 10% (연환산 ~213% 복리)** | 매우 공격적. 업계 최상위 펀드(Renaissance Medallion 등) 수준 |
| 보수 시나리오 (참고용 v0.1 초기) | 연 12–18% | 보수 정책(레버리지 1.0x, MDD 5% halt) 기준 |

| 단계별 검증 지표 | 보수 정책 | 공격 정책 (월 10% 목표) |
|----------------|----------|----------------------|
| 백테스트 안정성 (Sharpe) | ≥ 1.0 | ≥ 3.0 |
| 그림자 운영 안정성 | ≥ 0.7 | ≥ 2.0 |
| 실자금 시범 안정성 | ≥ 0.5 | ≥ 1.5 |
| 최대 누적 손실폭 한계 | ≤ 8% | ≤ 20% (재검토 필요) |

> 월 10% 목표 달성에 필요할 가능성: (1) 레버리지 확대 (현 1.0x → 3–5x), (2) Sharpe ≥ 3.0 전략, (3) MDD 한계 완화 (5% halt → 15–20%), (4) 거래 빈도 상승 (LFT → MFT). v0.1 의 보수 정책은 이 목표에 부적합 — 신규 이슈 **"월 10% 수익률 목표 가능성 평가"** 에서 전략·리스크·사이징 재설계를 검토한다.

> 현재 momo-btc-v2 백테스트 안정성은 메타라벨러 OFF 기준 0.18. 보수 목표(≥ 1.0) 도 미달. 월 10% 목표 달성은 현 카탈로그로 불가능 — 신규 전략 발굴·기존 강화·메타라벨러 ON 재검증 후 v0.2 에서 갱신.

### §1-11. 로드맵 한눈에

| Phase | 단계 | 진입 | 완료 | 기간 |
|-------|------|------|------|------|
| 0 | 백테스트 | 전략+룰 | 안정성 ≥ 1.0, MDD ≤ 8% | 완료 |
| 1 | 그림자 운영 | PaperBroker | 안정성 ≥ 0.7, 30거래일 | 진행 중 |
| 2 | 모의계좌 | KIS 모의+AsyncOrderRouter (#105) | 30거래일 + slip reconcile | 미착수 |
| 3 | 실자금 시범 | KillSwitch 검증 (#107) | 60거래일, 안정성 ≥ 0.5 | 미착수 |
| 4 | 정식 운영 | Phase 3 + 패키징 | 단일 EXE + 운영 매뉴얼 | 미착수 |

---

## §2. 리서치 근거

`docs/background/` 32개 노트를 11개 그룹으로 매핑. 각 그룹은 답하는 질문 + 위키링크 + 본 시스템 결정.

### §2-1. 기초 정의·포지셔닝

[[01-research-plan]] [[02-terms-quant-vs-quantum]] [[04-what-is-algo-trading]] [[05-positioning]]

| 항목 | 결정 |
|------|------|
| 정체성 | 자동매매 + 퀀트 교집합. 양자는 Phase 4 옵션 트랙으로 분리 |
| 빈도 | 저빈도–중빈도 (분봉–일봉). 고빈도 매매(HFT) 제외 |
| 파이프라인 | 데이터 → 신호 → 주문 → 리스크 → 모니터링 5단계 |

### §2-2. 시장 메커니즘·브로커

[[07-market-microstructure-basics]] [[09-system-components]] [[10-broker-api-comparison]]

| 항목 | 결정 |
|------|------|
| 1차 브로커 | KIS — REST + WebSocket, 초당 20건, 공식 SDK |
| 2차 브로커 | Binance USDT-M Futures — BTC 전용 |
| 호가창 | KRX 10단계, 동시호가 15분, 상하한 30% |
| 아키텍처 | `MarketDataFeed → Signal → RiskGate → BrokerAdapter` 4계층 |

### §2-3. 전략 패러다임

[[08-strategy-paradigms]]

| 패러다임 | 채택 | 이유 |
|----------|------|------|
| 규칙기반 | 1차 신호 | 투명·감사, 데이터 요구 낮음 |
| 통계·팩터 | 사이징·필터 | 변동성 타겟팅, 팩터 노출 |
| ML (지도학습) | 2차 필터만 | 메타라벨러로 거짓 신호 제거 |
| 양자 | Phase 4 옵션 | 2026 NISQ 한계, 실증 미흡 |

### §2-4. 백테스트·검증

[[11-backtest-engine-selection]] [[12-validation-protocol]] [[26-point-in-time-data]] [[27-corporate-actions]]

| 항목 | 결정 |
|------|------|
| 엔진 | 자체 구현 (Zipline-reloaded 참조, KRX 분봉 직접 지원) |
| 검증 | Rolling walk-forward + purged K-fold (누수 차단) |
| 데이터 무결성 | Point-in-Time — 결정 시점에 알 수 있는 데이터만 |
| 종목 이벤트 | 분할·배당·상장폐지 8종 조정 (생존편향 방어) |

### §2-5. 피처·알파

[[13-feature-alpha-catalog]] [[31-valuation-analysis]] [[35-meta-labeling-lopez-de-prado]]

| 항목 | 결정 |
|------|------|
| 라이브러리 | 프로토타입 `pandas-ta`, 프로덕션 `TA-Lib`/Numba |
| 1차 신호 | RSI 괴리·이동평균 교차·볼린저 돌파 |
| 가치 팩터 | PER·PBR 저평가 필터 (KRX 유니버스) |
| 2차 필터 | Triple-Barrier 라벨 + LightGBM 메타라벨러 |

### §2-6. 포트폴리오·사이징·체제

[[19-portfolio-risk]] [[20-position-sizing]] [[30-market-regime-detection]]

| 항목 | 결정 |
|------|------|
| 사이징 | 변동성 타겟팅 (ATR 기반) + 분수 켈리 |
| 포트폴리오 | 꼬리손실 평균·유효 분산·평균 상관 상시 모니터링 |
| 체제 탐지 | 변동성·추세 분류 → 전략 on/off |
| 공분산 | Ledoit-Wolf 축소 추정 |

### §2-7. 운영 전환 프로토콜

[[29-paper-to-live-protocol]] — 4단계: 백테스트 → 그림자 운영 → 모의계좌 → 소액 실자금. 진입 조건은 통계 기준 (안정성 ≥ 1.0 × 30 거래일 등). 롤백 트리거는 일간 손실 2% 또는 연속 5회 손실.

### §2-8. LLM·에이전트 가드레일

[[15-llm-agent-layer]] [[23-graphrag-for-trading-vault]] [[24-llm-agent-safety-finance]]

| 항목 | 결정 |
|------|------|
| 역할 | 문서 보조·볼트 검색·드래프트만 (불변식 6) |
| 컨텍스트 | GraphRAG — 벡터 + RDF 다중 홉 |
| 안전 | 구조화 출력 강제, 감사 로그, 오정렬 사례 반영 |

### §2-9. 양자 (Phase 4 옵션)

[[03-what-is-quantum-trading]] [[06-why-quantum-now]] [[14-quantum-poc-design]] — 2026 NISQ 수십 큐비트, 실증 미검증. Phase 4 QAOA 50종목 PoC 를 오프라인 실험 모듈로만 진행. 실시간 결정 경로 미포함.

### §2-10. 데이터·온톨로지

[[25-fibo-alignment]] [[ref-snowflake-alt-data-trading-demo]]

| 항목 | 결정 |
|------|------|
| 온톨로지 | 자체 경량 `trading.ttl` (FIBO FND·SEC 일부 매핑) |
| 대체데이터 | 참고 사례. 현 단계는 OHLCV + 가치 지표 한정 |
| 볼트 | Obsidian 노트 68+ + RDF → GraphRAG → LLM |

### §2-11. 특허 — 시스템 강화 차용

> 법적 고지: 학술·시스템 강화 목적이며 변리사 리뷰가 아니다. 상용 서비스 전 법무 검토 필수.

[[31-uprich-patent-analysis]] [[32-patents-portfolio-optimization]] [[33-patents-factor-models]] [[34-patents-execution-algos]]

| 특허 | 차용 |
|------|------|
| 업리치 AI 자동매매 (KR) | 다중 알파 집계·극단 공포 차단 → 리스크 룰 |
| CVaR 계층 최적화 (US20210110479A1) | 꼬리손실 다중 임계 → §8 다층 차단 |
| 팩터 IC 가중 (US20210224700A1) | 정보계수 기반 동적 가중 → 메타라벨러 피처 |
| ML 실행 최적화 (US11164248B2) | 시장 충격 모델 → TWAP/VWAP 분할 |

---

## §3. 시장·종목 universe — 무엇을 사고파는가

### §3-1. 이원화 전략

비트코인 선물(해외)과 한국 우량주(국내) 두 시장을 함께 다룬다. 24/7 vs 정규장, 달러 vs 원화의 성격 차이로 포트폴리오 손실 집중도를 낮춘다. 두 시장 상관은 평상시 낮고 위기 구간 급등이 전형이다 — §8 의 극단 공포 차단 장치가 이 구간을 막는다.

### §3-2. 자산군별 명세

| 항목 | BTC USDT-M 무기한 선물 | KRX 우량주 |
|------|----------------------|-----------|
| 거래소 / API | Binance USDⓈ-M | KIS Open API |
| 심볼 | `BTCUSDT` ([[BTCUSDT]]) | `005930` 등 종목코드 |
| 최소 주문 | 0.001 BTC, 명목 ≥ 10 USDT | 1주 |
| 데이터 주기 | 15분 / 4시간 / 일봉 | 15분 / 일봉 |
| 매매 가능 시간 | 24/7 | 평일 09:00–15:30 KST |
| 데이터 스키마 | [[data-lake-schema]] | [[data-lake-schema]] |

### §3-3. KRX 후보 종목 필터

| 필터 | 기준 |
|------|------|
| 시가총액 | ≥ 5,000억 원 |
| 일평균 거래대금 (20일) | ≥ 5억 원 |
| 안정성 등급 | D 이상 ([[universe-stability-grade]]) |

매월 1일 장 시작 전 자동 재평가.

### §3-4. 자동 제외 조건

| 조건 | 출처 |
|------|------|
| 공매도 금지구간 | 금융위 공시 API |
| 거래 정지 | KIS API `is_tradable` |
| 관리종목 / 단기과열 | KRX 공시 / 단기과열 리스트 |

### §3-5. 특이상황 처리

- **단일가 구간** (08:30–09:00, 15:20–15:30): 지정가 금지, 시장가만. 신호가 걸치면 다음 정규 봉으로 미룸.
- **가격제한폭 ±27%+**: 신규 진입 차단.
- **거래세 0.25‱**: KRX 매도 시 백테스트·페이퍼 동일 반영.
- **기업 이벤트** ([[27-corporate-actions]]): 배당락·증자·병합 전일 마감–익일 시작 신규 진입 차단.

### §3-6. 규제 경계 (TBD — 변리사·법무 검토 대상)

| 영역 | 현 위치 | 검토 항목 |
|------|---------|----------|
| 투자자문업 등록 | 본인 매매 한정 시 미해당 (자본시장법 §8) | 외부 사용자 배포 시 재검토 |
| 가상자산사업자(VASP) | 본인 운용 한정 시 미해당 (특금법) | B2C 구독 모델 시 재검토 |
| KRX 시장 교란 | 일일 5,000건·초당 2건 throttle 코드 강제 | 코드 레벨 enforcement |
| 세금 신고 | 자동 양식 생성 (#28) | 법적 효력은 사용자 본인 책임 |

> v0.1 은 본인 운용 한정. 외부 배포·구독화는 v0.2 이후 법무 검토 결과 반영.

---

## §4. 피처·신호 명세

### §4-1. 구현 완료 기본 지표 6종

| 지표 | 파라미터 | 한 줄 설명 |
|------|---------|-----------|
| RSI | period=14 | 최근 14봉 상승폭 비율. 과매수(70+)·과매도(30-) 수치화 |
| SMA | window=20, 60 | 단기·장기 이동평균 교차로 추세 전환 |
| ATR | period=14 | 변동폭 평균. 시장 변동성 절대 크기 |
| MACD | 12/26/9 | 단·장기 지수이동평균 차이와 신호선 교차 |
| Bollinger %B | 20봉, 2σ | 가격이 ±2σ 밴드 어디 위치하는지 0–1 |
| 실현 변동성 | 20봉 | 로그수익률 표준편차 |

### §4-2. 결정용 신호 3종

| 신호 | 동작 | 사용 전략 |
|------|------|----------|
| [[rsi-divergence]] | 가격 신고점·저점 vs RSI 반대 방향 | momo-btc-v2, momo-kis-v1 |
| [[sma-cross]] | 단·장기 이동평균 교차 시점 | (보조) |
| [[bollinger-breakout]] | %B 1 초과(상단) / 0 미만(하단) | (보조) |

### §4-3. 신호 인터페이스 6필드

[[signal-interface]]

| 필드 | 의미 |
|------|------|
| `action` | "buy" / "sell" / "hold" |
| `size` | 0.0–1.0 운용 자본 대비 목표 비중 |
| `reason` | 신호 발생 이유 (감사용) |
| `expected_return` | 전략 추정 기대수익률 (또는 None) |
| `win_probability` | 전략 추정 승률 (또는 None) |
| `confidence` | 복합 확신도 (또는 None) |

> `expected_return`·`win_probability`·`confidence` 는 결정적 코드 수식 결과만. LLM 출력 직접 할당 금지 (불변식 6).

### §4-4. Point-in-Time 보장

[[26-point-in-time-data]] — 미래 데이터 유입 차단. t 시점 신호는 t-1 종가까지만 사용 (`.shift(1)` 강제). 백테스트 엔진은 "t봉 신호 → t+1봉 시가 체결" 순서를 강제.

### §4-5. 메타라벨러 2차 필터

```
1차 신호 (rsi-divergence / sma-cross / bollinger-breakout)
    ↓
입력 생성: 신호 파라미터 · 시장 변동성 · 거래비용 · 트리플 배리어 라벨
    ↓
LightGBM 모델 → 0 또는 1
    ↓
1: 실제 주문 생성 / 0: 신호 폐기
```

[[35-meta-labeling-lopez-de-prado]] — 메타라벨러는 주문 생성 여부만 판단. 가격·수량 결정은 규칙기반 사이저. 매주 일요일 자정 자동 재학습. 검증셋 성능이 기존 모델 -2%p 이상 하락 시 자동 롤백.

> 재학습 윈도우 7일치(BTC 1분봉 ~10,000개) 표본 적정성은 v0.2 검증 후 갱신.

---

## §5. 전략 포트폴리오

### §5-1. 현재 카탈로그 5종

| 전략 | 자산 | 시간단위 | 신호 | 상태 | 백테스트 안정성 |
|------|------|---------|------|------|----------------|
| [[momo-btc-v2]] | BTCUSDT | 15분 | [[rsi-divergence]] | paper | 0.18 (메타라벨러 OFF) — ON 재검증 진행 중 |
| [[momo-kis-v1]] | KRX (005930) | 15분 | [[rsi-divergence]] | backtest | KRX 메타라벨러 교차검증 (#97) |
| [[breakout-donchian]] | KOSPI200 | 일봉 | 돈치안+ATR | draft | — |
| [[meanrev-pairs]] | ETHBTC | 1시간 | z-score | backtest | — |
| [[momo-vol-filtered]] | BTCUSDT | 4시간 | MACD+변동성 | backtest | — |

> 0.18 은 솔직히 낮다. 메타라벨러 적용 후 목표(≥ 1.0) 달성 가능성은 Phase 1 그림자 30거래일 데이터로 검증 중. v0.2 갱신.

### §5-2. 라이브 후보 우선순위

1. **[[momo-btc-v2]] (1순위)**: 24/7 시장 — 시스템 전체 검증할 안전한 첫 후보. 30거래일 무사고가 Phase 2 진입.
2. **[[momo-kis-v1]] (2순위)**: KRX 메타라벨러 (#97) 검증 후 추가. BTC 전략과 상관 낮아 분산 기여.

### §5-3. 검증 단계별 목표

| 단계 | 안정성 (Sharpe) | MDD | 기간 |
|------|----------------|------|------|
| 백테스트 통과 | ≥ 1.0 | ≤ 8% | 3년+ |
| 그림자 통과 | ≥ 0.7 | ≤ 5% | 30거래일 |
| 실자금 시범 통과 | ≥ 0.5 | ≤ 5% | 60거래일 |

### §5-4. 비동기 오케스트레이터 연동

[[orchestrator-interface]] — 전략별 일수익률 시계열을 §8 포트폴리오 리스크 평가기에 공급. 신규 전략 추가 시 두 줄 누락하면 포트폴리오 감시 침묵.

```python
orchestrator.register_strategy(strategy_id, strategy)
orchestrator.register_strategy_returns(strategy_id, daily_returns_series)
```

### §5-5. 신뢰도 점수와 동적 가중치

[[reliability-score]] — 거래 이력 충분도 × 정보비율 t값 × 꼬리손실 위반 게이트 곱셈 점수.

| 인자 | 임계 |
|------|------|
| 거래 이력 충분도 | 30회+ 체결 시 0.5+ |
| 정보비율 t값 | t ≥ 2.0 |
| 꼬리손실 위반 게이트 | 최근 22거래일 1회 이상 → 가중치 0 |

### §5-6. 추가 후보

[[momo-vol-filtered]] 외 단타 카탈로그 (#99 — VWMA100 단타) 가 research+factorial 설계 단계. **자산은 크립토(BTC/ETH on Binance + 알트코인)**, 시간 단위 1분/5분/15분. 상세 — §5-7.

### §5-7. 월 10% 목표 검증의 핵심 입력 — VWMA 단타 카탈로그 (#99)

> 사용자 지정 최종 목표 월 10% (§1-10) 의 첫 번째 구체 증거 자료. 단 1인 사례·survivor bias 한계 — 사전 등록 factorial 실험으로 통계 보정 후 #119 평가에 입력.

#### §5-7-1. 배경 — 단일 트레이더 사례

전업 단타 트레이더 "이랑이" (유튜브 "새로운 부자TV") 의 누적 70-80억 수익 사례. **VWMA100 적용 후 월 수익 3억 → 13억 (4배 증가)** 본인 주장. 1인 표본의 한계는 §5-7-5 에 명시.

| 자산군 | 시장 | 시간 단위 |
|--------|------|----------|
| BTC / ETH / 알트코인 | Binance + 업비트 | 1분 / 5분 / 15분 |

#### §5-7-2. 8가지 기법 카탈로그

| # | 기법 | 핵심 |
|---|------|------|
| 1 | VWMA100 (거래량 가중 이동평균) | 종가 아닌 체결량 가중. 영상 주장 "제1비법" |
| 2 | 멀티TF 프랙탈 | 1시간/일봉 정배열 + 5분/15분 매수 |
| 3 | 이평선 자석 (Mean Reversion) | z-score 기반 카운터 — #79 mean-rev 차별 |
| 4 | EMA 경로 예측 | slope·curvature·ETA-to-cross 자동화 features |
| 5 | Turning-Point Only (R:R 1:6) | 기대 +7% / 손절 -1% — P(win) > 14% 필수 |
| 6 | 상대강도 (UBAI 대비) | Jegadeesh & Titman (1993) cross-sectional momentum |
| 7 | 시간대 게이트 | 10:30-11:00 매수 금지·주말 회피 |
| 8 | POC + 호가창 OBI/OFI | Order Book / Order Flow Imbalance + Microprice |

#### §5-7-3. 사전 등록 8-variant Factorial 실험 (HARKing 차단)

전부 같은 Purged K-Fold (k=5, embargo=24h) CV split 에서 일괄 실행. **사후 추가 금지** — 첫 백테스트 실행 전 확정.

| ID | 구성 |
|----|------|
| A | VWMA100 cross 단독 (baseline) |
| B | A + EMA slope > 0 |
| C | A + 멀티TF alignment |
| D | A + 시간대 게이트 |
| E | A + UBAI 상대강도 |
| F | A + POC 거리 |
| G | A + 호가 OBI/OFI/microprice |
| H | A + B + C + D + E + F + G (full stack) |

#### §5-7-4. 다중 검정 보정 — Deflated Sharpe Ratio

8 variant 동시 검정의 false discovery 위험 차단. López de Prado (2014) Deflated Sharpe Ratio (DSR) 적용 — 시행 횟수 보정된 Sharpe.

| 항목 | 기준 |
|------|------|
| 편입 조건 | DSR > 1.0 && OOS MDD < 25% |
| 기각 시 | research 노트에 negative result 증거 보존 (HARKing 차단) |
| 데이터 | BTC/ETH 2020-01 ~ 2025-12, Binance 1m OHLCV + L2 tick |

#### §5-7-5. #119 (월 10% 평가) 와의 관계

| 항목 | #99 의 역할 |
|------|-----------|
| 월 10% 가능성 직접 증거 | 영상 주장 "월 4배" 의 통계 보정 검증 결과 — #119 평가 핵심 입력 |
| 카탈로그 다양화 | 현재 5종 + vwma-cross-v1 = 6종 |
| Microstructure features | OBI/OFI/microprice 는 §4 신규 — 다른 전략에도 활용 |
| Phase 0 → Phase 1 다리 | 백테스트 결과 양호 시 메타라벨러 ON 즉시 검증 |

> **한계 명시**: 1인 표본 + 영상 자료 + 본인 주장 — 객관 검증 미완료. survivor bias·표본 편향 가능성. 통계 보정·OOS 검증·메타라벨러 ON 후에만 실거래 후보 진입.

#### §5-7-6. 신규 feature 모듈 7종 (#99 AC)

| 모듈 | 내용 |
|------|------|
| `src/features/vwma.py` | VWMA 계산 (주기·윈도우 파라미터화) |
| `src/features/ma_projection.py` | EMA slope·curvature·projection |
| `src/features/multi_tf.py` | 상위 TF alignment check |
| `src/features/time_of_day.py` | 시간대 게이트 |
| `src/features/cross_sectional_rs.py` | UBAI 대비 RS |
| `src/features/poc.py` | Point of Control 거리 |
| `src/features/orderbook_flow.py` | OBI / OFI / microprice / depth decay / Hawkes intensity |

---

## §6. 포지션 사이징 — 한 번에 얼마나 베팅하는가

### §6-1. 핵심 원리

[[position-sizing]] — 사이저는 "운용 자본의 몇 퍼센트를 이 매매에 쓸지" 0–1 숫자만 산출. 이후 [[risk-rule-dsl]] 한도 검사 통과해 주문 수량으로 환산.

| 원칙 | 의미 |
|------|------|
| 수학적 결정성 | 동일 입력 → 동일 출력. LLM 호출 없음 (불변식 6) |
| 닫힌 실패 | 추정 불확실 시 0 반환 → 진입 차단 |
| 단일 책임 | raw 크기만 계산. 정책 상한은 리스크 룰 평가기 |

### §6-2. 사이징 모드 3가지

| 모드 | 적용 조건 | 입력 | 산출 | 상한 |
|------|----------|------|------|------|
| 분수 켈리 | 신호에 승률·기대수익 둘 다 존재 | p, R, k=0.25 | 수학적 최적의 25% | 5% |
| 변동성 타겟팅 | 메타 없음, 변동성 추정 가능 | σ, σ_target | σ_target / σ | 5% |
| 고정 비율 | 위 둘 불가 시 비상시 | — | 1% | 1% |

| 변동성 목표 | 한국 주식 | 암호화폐 |
|------------|----------|----------|
| 연환산 σ_target | 10% | 20% |
| EWMA λ | 0.94 | 0.94 |

### §6-3. Signal-wins 우선 라우팅

```
신호 수신
  ↓
expected_return + win_probability 둘 다 있음?
  → YES → 분수 켈리
  → NO  → 변동성 추정 가능?
            → YES → 변동성 타겟팅
            → NO  → 고정 1%
```

`None`(미계산) ≠ `0.0`(계산된 제로). 0.0 → 엣지 없음 → 0 반환.

### §6-4. 운용 자본 1,000만 원 예시

| 항목 | 값 |
|------|---|
| 단일 매매 최대 노출 | 5% = **50만 원** |
| 일일 합산 권고 (3건 동시) | ~15% = 150만 원 |

BTC EWMA 변동성 연 60% 면 변동성 타겟팅 출력 33% 지만 `max_weight_pct=10%` 클램프, `per_trade.max_notional_krw=500,000` 한 번 더 제한.

---

## §7. 리스크 룰 — 자동 한도와 비상정지

### §7-1. 구조

[[risk-rule-dsl]] — YAML 한 장이 매 주문 직전 평가. first-violation-wins.

```
per_trade → per_day → per_portfolio → per_portfolio_risk
         → per_position → sector_limits → drawdown
```

> 정책 변경은 코드와 동일하게 PR 리뷰 필수. 라이브 중 무단 변경은 감사 로그에 기록.

### §7-2. 보수 정책 YAML (Phase 1-2 디폴트)

```yaml
policy_version: 1
name: conservative-default
description: "QTA 기본 보수적 정책 (Phase 1-2)"

per_trade:
  max_notional_krw: 500_000        # 단일 매매 50만 원 (자본 1,000만 원 기준 5%)
  max_qty: 100
  allowed_sides: [buy, sell]

per_day:
  max_orders: 30
  max_loss_krw: 300_000            # 일일 실현 손실 한도 3%
  max_turnover_krw: 10_000_000

per_portfolio:
  max_gross_exposure_krw: 10_000_000
  max_net_exposure_krw: 8_000_000
  max_leverage: 1.0                # 레버리지 미사용

per_portfolio_risk:
  max_cvar_pct: 0.08
  max_corr_avg: 0.80
  min_enb_ratio: 0.3
  alpha: 0.975
  cvar_levels:
    - [0.95, warn]
    - [0.975, reduce]
    - [0.99, halt]
  extreme_fear_block: true
  extreme_fear_threshold: 0.2
  on_cvar_breach: reduce
  on_corr_breach: block
  on_enb_breach: halt

per_position:
  max_weight_pct: 10.0             # 단일 종목 10%
  max_qty: 1000

sector_limits:
  - {sector: tech, max_weight_pct: 25.0}
  - {sector: finance, max_weight_pct: 25.0}

drawdown:
  max_intraday_dd_pct: 2.0
  max_running_dd_pct: 5.0          # 누적 5% 도달 시 halt
  on_breach: halt
```

### §7-3. 위반 → 결정 흐름

```
주문 의도 → evaluate(policy, snapshot)
                ↓
        Decision { ALLOW | REDUCE | BLOCK | HALT }
                ↓
        Kill Switch 게이트
                ↓
        브로커 어댑터
```

ALLOW 가 아닌 모든 결정은 Prometheus 메트릭 `qta_risk_breach_total{rule_id=...}` 기록.

### §7-4. 비상정지 자동 4트리거

[[kill-switch-dr]] [[kill-switch-runbook]] [[max-drawdown-5pct]]

| 트리거 | 임계 | 동작 |
|--------|------|------|
| 누적 손실 5% | drawdown.on_breach=halt | 신규 주문 전면 차단 |
| 브로커 API 연속 오류 | 5회 | 신규 차단 + 재연결 시도 |
| API 오류율 슬라이딩 | 임계 초과 | 신규 차단 + 알림 |
| 1초당 동일 종목 5건 체결 | 비정상 루프 | 즉시 정지 + 전수 로그 덤프 |

> 비상정지 후 복구는 사람: 원인 분석 → Dry-run 1회 → 페이퍼 1세션 → 운영자 2인 승인 → `release` 명령. 자동 재개 없음.

---

## §8. 포트폴리오 리스크

### §8-1. 개요

[[19-portfolio-risk]] — "여러 전략이 같은 날 같은 방향으로 망할 위험"의 2층 방어선. 주기 평가기(10분)가 지표 측정, [[risk-rule-dsl]] `per_portfolio_risk` 가 결정 반영.

### §8-2. 측정 지표 4종 + 임계

| 지표 | 의미 | 임계 |
|------|------|------|
| 평균 종목간 상관 (ρ) | 동시 손실 위험 | ≥ 0.80 → BLOCK |
| 꼬리손실 평균 — α=0.95 | 운 나쁜 날 평균 손실 | 임계 초과 → WARN |
| 꼬리손실 평균 — α=0.975 | (보수) | 임계 초과 → REDUCE |
| 꼬리손실 평균 — α=0.99 | (극단) | 임계 초과 → HALT |
| 유효 분산 종목 수 (ENB) | 실질 독립 베팅 수 | ENB/N ≤ 0.30 → HALT |
| 극단 공포 지수 | 시장 공포 | < 0.20 → BLOCK |

다중 임계 꼬리손실은 #87 특허 차용. 단일 임계보다 시장 악화 조기 감지·단계적 대응.

> ENB 는 최소 3 전략 일수익률 필요. 라이브 < 3 전략 단계에서는 "데이터 부족 — 측정 불가" + halt 트리거 비활성.

### §8-3. 시장 리스크 시나리오

| 시나리오 | 시스템 동작 |
|---------|-----------|
| BTC 10% 플래시 크래시 | 일중 MDD 2% 트리거 → BTC 신규 차단. 5% → 전체 halt |
| KRX 서킷브레이커 / VI | KIS 자식 주문 대기 큐 → 정규 재개 시 정책별 처리 (WAIT/PARTICIPATE/CANCEL) |
| Binance 접속 불가 1시간 | API 연속 오류 5회 → 신규 차단, 재연결 시도. KRX 정상 |
| BTC + KRX 동시 급락 | 평균 ρ ≥ 0.80 → 신규 차단. 극단 공포 < 0.2 → 추가 차단 |

### §8-4. 자동 액션 흐름

```
주기 평가기 (10분) → 지표 계산 → 평가기 → Decision → Kill Switch
                                              ↓ (HALT 시)
                                     사용자 승인 후 청산
```

> HALT 후 청산 자동 실행 안 함. 사람이 결정.

### §8-5. 신뢰도 연동

포트폴리오 임계 위반 시 위반 기여 전략의 신뢰도가 0 으로 리셋. 30회+ 새 거래 이력 누적까지 자동 최소 가중치.

---

## §9. 실행·브로커

### §9-1. 이원화 어댑터

[[broker-adapter]] [[broker-adapter-async]] [[10-broker-api-comparison]] — KIS(1차) + Binance USDT-M Futures. 상위 레이어는 어느 브로커인지 모른다.

| 단계 | 인터페이스 |
|------|----------|
| Phase 0 | sync `BrokerAdapter` |
| Phase 1+ | async `AsyncBrokerAdapter` (#73 머지) — 멀티심볼 비동기, sync 대비 11.8배 |

모든 주문은 `place_order()` 진입 전 `KillSwitch.assert_allow_order()` 통과 필수. 비상정지 상태에서 청산 주문만 화이트리스트.

### §9-2. 주문 타입 매트릭스

[[execution-algorithms]]

| 알고리즘 | Binance | KIS | 선택 규칙 |
|---------|---------|-----|----------|
| 시장가 | ✓ | ✓ | Kill Switch 청산, 빠른 진입 |
| 지정가 | ✓ | ✓ | **디폴트** — 수수료·슬리피지 절감 |
| 시간 균등 분할 | 자체 split | 자체 split | 자본 5% 초과 주문 |
| 거래량 비례 분할 | 자체 + 거래량 (#111) | 자체 + KRX 거래량 (#111) | 일중 평균가 추종 |

KRX 단일가·VI·서킷브레이커 자식 주문 대기 큐 정책: `WAIT` / `PARTICIPATE_AT_REFERENCE` / `CANCEL`.

### §9-3. 거래 비용 가정

| 시장 | 수수료 | 슬리피지 (Phase 1) |
|------|--------|------------------|
| Binance Futures USDT-M | taker 0.05% / maker 0.02% | 0% (mock) |
| KIS 매수 | 약 0.015% | 0% (mock) |
| KIS 매도 | 약 0.265% (거래세 0.25‱ 포함) | 0% (mock) |

> 슬리피지 0% 는 Phase 1 한정. SquareRootImpact 모델 (#109) **Phase 2 진입 전 캘리브레이션 필수.**

### §9-4. Phase 별 진화

| Phase | 모드 | 체결 |
|-------|------|------|
| 1 그림자 | PaperBroker (즉시 100%, 0-slip) | mock taker 수수료 |
| 2 모의계좌 | KIS 모의 + AsyncOrderRouter (#105) | 실제 주문 흐름, 모의 체결 |
| 3 실자금 5% (#107) | 실제 슬리피지 + Reconcile | 소액 실거래 |
| 4 정식 | 실자금 100% + 부분 체결 (#110) + IS/TCA (#114) | 정식 운영 |

### §9-5. 열린 이슈

| 이슈 | 내용 |
|------|------|
| #109 | 슬리피지 모델 활성화 |
| #110 | 부분 체결 시뮬레이션 |
| #111 | VWAP 거래량 프로파일 blend |
| #112 | 다중 브로커 자동 선택 |
| #113 | TWAP 변동성 적응 |
| #114 | 거래비용 정량화 (IS/TCA) |

> Phase 4 부분 체결(#110)·거래비용 분석(#114) 미구현. KRX 저유동성 큰 주문 포지션 불일치 위험 — Phase 3 진입 전 #110 최소 구현이 블로커.

---

## §10. 배포·운영

### §10-1. 패키징 도구 비교

| 도구 | 장점 | 단점 | 결정 |
|------|------|------|------|
| **PyInstaller (단일 .exe)** | Python 미설치 환경 실행, 단일 파일, 윈도우 네이티브 | 50–150MB, 최초 실행 3–8초, 백신 오탐 가능 | **선택** |
| Nuitka (AOT 컴파일) | 30–50% 작은 바이너리, 1–2초 시작 | 빌드 시간 5–10배, NumPy/Pandas 호환 이슈 | 미선택 (Phase 5+ 재검토) |
| Docker | 의존성 격리, 재현성 100% | 윈도우 진입장벽 (Docker Desktop+WSL2+4GB) | 개발 환경 전용 |

> PyInstaller 백신 오탐은 EV Code Signing 인증서(연 $300–500)로 완화. Phase 4 진입 전 인증서 예산·절차 확정.

### §10-2. 시스템 구조 (사용자 PC)

```
┌────────────────────────────────────────────────┐
│  qta.exe  (윈도우 10/11)                       │
│                                                │
│  CLI Daemon (asyncio):                         │
│    ├─ BinancePublicFeed (WS)                   │
│    ├─ KISMarketFeed (REST 1분)                 │
│    ├─ AsyncStrategyOrchestrator                │
│    ├─ MetaLabeler (LightGBM 2차 필터)          │
│    ├─ Risk Evaluator (YAML DSL)                │
│    ├─ KillSwitchGate                           │
│    └─ AsyncBrokerAdapter (Binance + KIS)       │
│                                                │
│  FastAPI 로컬 서버 (localhost:3000):            │
│    ├─ /         (4사분면 대시보드)             │
│    ├─ /metrics  (Prometheus)                   │
│    └─ /api/*    (제어용 REST)                  │
│                                                │
│  %APPDATA%/qta/                                │
│    ├─ wal.jsonl, data/*.parquet, logs/         │
│    ├─ secrets/  (DPAPI 암호화)                 │
│    ├─ reports/  (월간 보고서)                  │
│    └─ backup/   (직전 버전)                    │
└────────────────────────────────────────────────┘
        │                          │
        ▼                          ▼
   Binance Futures           KIS Open API
```

> 단일 프로세스는 PC 절전·네트워크 단절 시 매매 중단. Phase 3+ Binance 서버측 OCO 사전 등록 + KIS 조건부 주문으로 PC 다운 시 포지션 보호.

#### §10-2-1. production.yaml 위치 (#177)

| 경로 | 역할 |
|------|------|
| `configs/orchestrator/production.yaml` | 5전략 카탈로그 + 메타라벨러(opt-in) 등록. EXE 빌드 시 `qta.spec` 의 `datas=[("configs", "configs")]` 로 번들에 동봉됨 |
| `qta.exe --production-yaml /path/to/custom.yaml` | 사용자가 외부 YAML 로 override (실험·릴리즈 비교용) |
| `src.live.loop._load_orchestrator` | `on_metalabeler_missing="skip"` 로 호출 — 모델 아티팩트 부재 시 해당 entry 만 skip + warning, 나머지 5전략은 정상 등록 |
| `src.portfolio.config_loader.load_orchestrator_from_yaml` | YAML→ AsyncStrategyOrchestrator 빌더. async strategy 는 직접 등록, sync 는 `_StrategyAdapter` 경유 |

**메타라벨러(#85) 활성화 절차** (운영자 전용):
```bash
python scripts/train_metalabeler_btc.py --output-dir models/momo-btc-v2
python scripts/promote_metalabeler.py --strategy momo-btc-v2 --version <ts>
# production.yaml 의 `momo-btc-v2-meta` 블록 uncomment 후 EXE 재시작
```

**리스크 임계** 는 본 파일이 아니라 별도 정책 YAML 에서 로드된다 (`docs/specs/risk-rule-dsl.md`, §7-2 보수 정책). production.yaml 은 전략 카탈로그만 담는다.

### §10-3. API 키 저장

| 항목 | 내용 |
|------|------|
| 기본 | 윈도우 DPAPI |
| 위치 | `%APPDATA%/qta/secrets/` 암호화 blob |
| 바인딩 | 사용자 SID — 다른 계정 복호화 불가 |
| 평문 | 디스크·환경변수 미저장. 메모리 사용 후 즉시 제로화 |
| 회전 | 대시보드 "키 갱신" → 새 blob + 구 blob 삭제 |

> DPAPI 는 같은 계정 악성코드에 무력. 온보딩 강제: (1) Binance API IP 허용 목록 + 출금 권한 제외, (2) KIS API 주문 전용 권한.

### §10-4. 모니터링 대시보드

`localhost:3000` 4사분면:

| 위치 | 내용 | 메트릭 |
|------|------|--------|
| 좌상 | 손익 그래프 | `qta_pnl_current` |
| 우상 | 포지션 + 6종 한도 사용률 | `qta_position_qty`, `qta_risk_breach_total` |
| 좌하 | 신호 → 메타라벨러 → 주문 → 체결 타임라인 | `qta_orders_total`, `qta_fills_total` |
| 우하 | 비상정지 4 트리거 상태 + 수동 버튼 | `qta_kill_switch_state` |

[[observability]] — localhost 전용은 모바일 불가. 텔레그램 봇 양방향 (`/kill`, `/release`, `/status`) 으로 보완.

### §10-5. 자동 업데이트

| 항목 | 내용 |
|------|------|
| 체크 | 시작 시 + 매주 일요일 자정 |
| 소스 | GitHub Release |
| 검증 | SHA256 + GitHub 서명 |
| 디폴트 | 수동 — 알림 → 사용자 승인 → 다운로드 → 재시작 |
| 롤백 | `%APPDATA%/qta/backup/` 직전 버전 |

> 자동 업데이트는 공급망 공격 벡터. Phase 4 이전 재현 가능 빌드 + 다중 서명 정책 설계 필요.

### §10-6. 운영 자동화

| 항목 | 주기 | 내용 | 이슈 |
|------|------|------|------|
| 메타라벨러 재학습 | 매주 일요일 자정 | 7일 데이터 재학습. 드리프트 알림. 실패 시 직전 모델 유지 | #95 |
| 월간 보고서 | 매월 1일 | 손익 + 매매내역 CSV. 한국 세금 신고용 양식 ([[tax-automation]]) | #28 |
| 알림 | 실시간 | 텔레그램 봇. 비상정지·이상 체결·재학습·일일 PnL |
| 데이터 정리 | 매월 1일 | 90일 이상 분봉 캐시 삭제 |

### §10-7. 온보딩 (첫 실행 → 운영)

| 단계 | 사용자 | 시스템 | 시간 |
|------|--------|--------|------|
| 1 | qta.exe 다운로드 | — | 1분 |
| 2 | 더블클릭 | 초기 설정 마법사 | 5초 |
| 3 | Binance API 키 입력 | DPAPI 암호화 + 잔고 조회 | 30초 |
| 4 | KIS API 키 입력 | DPAPI 암호화 + 연결 테스트 | 30초 |
| 5 | 리스크 정책 확인 | YAML 정책 생성 | 1분 |
| 6 | 텔레그램 봇 (선택) | 봇 토큰 + 테스트 메시지 | 1분 |
| 7 | "시작" 클릭 | 페이퍼 모드 자동 시작 (최초 30일 실거래 차단) | 즉시 |

> API 키 발급 자체가 거래소 사이트에서 10–30분 별도 작업. 온보딩 가이드에 거래소별 발급 스크린샷·권한 체크리스트 필수.

### §10-8. 시스템 요구사항

| 항목 | 최소 | 권장 |
|------|------|------|
| OS | Windows 10 (1903+) | Windows 11 |
| CPU / RAM | 2코어 / 4GB | 4코어+ / 8GB+ |
| 디스크 | 500MB | 2GB+ |
| 네트워크 | 1Mbps | 10Mbps+ |

---

## §11. 단계별 로드맵 + 구현 진척도

### §11-1. 한 줄 요약

**Phase 0 완료, Phase 1 그림자 운영 시작 — 인프라 90% 완성, 향후 12–18개월 단계적 실거래 전환.**

### §11-2. Paper-to-Live 5단계

[[29-paper-to-live-protocol]]

| Phase | 단계 | 가능한가 | 진입 조건 | 완료 기준 | 기간 |
|-------|------|---------|----------|----------|------|
| 0 | 백테스트 | 과거 데이터 검증 | 전략 + 룰 | 안정성 ≥ 1.0, MDD ≤ 8% | **완료** |
| 1 | 그림자 운영 | 실시간 + 가짜 돈 | PaperBroker (#80) | 안정성 ≥ 0.7, 30거래일 | **진행 중 (1–3개월)** |
| 2 | 모의계좌 | 증권사 모의 실제 주문 | KIS 모의 + AsyncOrderRouter (#105) | 30거래일 + slip reconcile | 3–6개월 |
| 3 | 실자금 5% | 5% 한도 실거래 | KillSwitch 검증 (#107) | 60거래일, 안정성 ≥ 0.5 | 6–9개월 |
| 4 | 정식 운영 | 단일 EXE 24/7 | Phase 3 + 패키징 | EXE 배포 + 운영 매뉴얼 | 9–18개월 |

### §11-3. 86개 이슈 진척도 (백서 v0.1 발행 후 신규 21건 추가 반영)

<!-- progress-table:start -->
| # | 상태 | 라벨 | 제목 |
|---|------|------|------|
| #238 | ✅ |  | fix: smoke MVP 통로 검증 — KIS 한도 폭주 + Binance testnet endpoint + 8개 결함 |
| #236 | ✅ |  | feat: 대시보드 실거래 가시화 + smoke-dual 통로 검증 |
| #231 | ✅ | enhancement | feat: 운영 인프라 통합 리팩토링 — 11전략 실가동 + AC0 검증 게이트 + Lake 누적 (#105/#218/#227 후속) |
| #230 | ✅ |  | chore: HTS 검색식 3종 (5분대기/단타/스윙) 채택 평가 — 1주 분봉 백테스트 |
| #229 | ✅ | enhancement | eval: HTS 검색식 3종 (5분대기/단타/스윙) 채택 평가 — 1주 분봉 백테스트 |
| #227 | ✅ |  | feat: Live Universe Scanner 패러다임 — 장중 실시간 검색식 자동매매 (검색식 + 손익비 청산) |
| #225 | ✅ |  | feat: universe-scan paper rebal cron — 매주 금/일 자동 발주 (#218 후속) |
| #221 | ✅ |  | feat: telegram_control 거래 현황 명령어 (/today /positions /fills /account) + configs/policy.yaml 운영 정책 작성 (#126/#216 후속) |
| #218 | ✅ |  | feat: universe-scan 패턴 전면 전환 — 풀 스캔 매매 + 통합 카탈로그 + 대시보드 토글·전략별 페이지 + 데몬·Telegram·Docker·daily_check 일괄 리팩토링 |
| #217 | ✅ |  | fix: KIS Pydantic 스키마 case-insensitive — paper 응답 소문자 키로 잔고 카드 깨짐 |
| #216 | ✅ |  | bug: live_run paper KIS warmup→WS→tick→WAL 흐름 미작동 — --schedule=krx 미구현 + 마감 후 warmup 무한 retry (#133/#152 후속) |
| #215 | 🔄 |  | chore: qta.exe 자동 배포 (Releases latest 롤링) + --check-bundle 검증 단계 |
| #206 | ✅ |  | feat: #185 후속 — bench multi-asset 확장 + multi_tf/turning_point/metalabeler 실 연결 |
| #199 | ✅ |  | feat: R6 (R4 의 1시간봉 변형) — backtest + paper 30일 병렬 운영 (#143 후속) |
| #198 | ✅ |  | feat: 대시보드 "Shadow Runs" 뷰어 — Binance/KIS WAL read-only 통합 표시 (#143/#133 후속) |
| #194 | ✅ |  | feat: DashboardState 라이브 PnL 와이어링 (전체·일간·전략별, KST 09:00 일일 리셋) |
| #193 | 🔄 |  | feat: 전략별 체결 이력 필터 — REST + 상세 페이지 라이브 타임라인 |
| #192 | ✅ |  | feat: 전략별 포지션 추적 (strategy_id 태깅) + position_provider 라이브 와이어링 |
| #191 | 🔄 |  | feat: 전략 상세 페이지 (/strategies/{id}) — 종목·실시간 가격·summary·토글 |
| #185 | ✅ |  | feat: Iranyi 12룰 풀 구현 + 5m TF 다중자산 백테스트 — VWMA 진입 시점 강화 (#147 후속) |
| #182 | ✅ |  | feat: qta.exe 첫 실행 UX — 자동 브라우저 열기 + 콘솔창 유지 |
| #181 | ✅ |  | feat: 매매 타임라인 실시간 스트리밍 (WebSocket) — 신호→메타라벨러→주문→체결 |
| #180 | ✅ |  | feat: 전략 ON/OFF 토글 UI + REST API (runtime orchestrator 제어) |
| #179 | 🔄 |  | feat: 전략 상세 페이지 (마크다운 렌더링 + 신호·리스크룰 인라인) |
| #178 | ✅ |  | feat: FastAPI 대시보드 — 전략 카탈로그 페이지 + 백테스트 수익률 표시 |
| #177 | ✅ |  | chore: configs/orchestrator/production.yaml 작성 + EXE 재빌드 (전략 5종 등록 활성화) |
| #175 | ✅ |  | feat: 1-month paper trading shadow run for S2c (vol-target Donchian, #172 후속) |
| #174 | ✅ |  | feat: Hourly funding carry + multi-exchange arbitrage (S4 mhr 보강, #172 후속) |
| #173 | ✅ |  | feat: HMM regime detection + S2c/S4 strategy switching (#172 후속) |
| #155 | ✅ |  | fix: cross_asset_compare.py manifest path / format 정합성 (#97 후속) |
| #154 | ✅ |  | feat: bench_metalabeler_kis.py 에 equity-curve 기반 Sharpe/MDD/DSR 출력 (#97 후속) |
| #153 | 🔄 |  | feat: KIS 90일 누적 후 momo-kis-v1-pooled 가설 본판정 (#97 Phase B) |
| #152 | ✅ |  | chore: KIS 1분봉 cron 운영 시작 + 누적 데이터 모니터링 (#97 후속) |
| #147 | ✅ |  | feat: VWMA + 추세 필터 + stop-loss/take-profit 통합 backtest (#99 후속) |
| #145 | ✅ |  | research: 오더플로우·ICT 시그널 카탈로그 평가 (sleeve B 알파 보강) |
| #143 | ✅ |  | chore: Phase 1 Shadow Paper 데몬 실가동 + 30거래일 누적 운영 |
| #142 | 🔄 |  | chore: Whitepaper v0.1.1 — 지난 30거래일 백테스트 fast-forward 발행 (#138 보다 빠른 임시판) |
| #140 | 🔄 |  | chore: 외부 자문 1-2명 확보 (퀀트 트레이딩·핀테크 법무) |
| #139 | ✅ |  | chore: 진척도 자동 갱신 스크립트 (gh issue list → 백서 §11-3 재생성) |
| #138 | 🔄 |  | chore: Whitepaper v0.2 발행 (Phase 1 30거래일 누적 후) |
| #137 | 🔄 |  | feat: 대체 데이터 소스 1개 이상 확보 (Binance/KIS 의존 완화) |
| #136 | 🔄 |  | research: Moat 분석 + 경쟁 우위 지속성 논거 |
| #135 | 🔄 |  | research: 수익 모델 단위 경제학 (B2C 구독 / B2B 라이선싱 / 자기자본 운용) |
| #134 | 🔄 |  | chore: EV Code Signing 인증서 발급·예산 확정 |
| #133 | ✅ |  | chore: Phase 2 KIS 모의계좌 4주 실측 운영 (#105 Stage 7b 후속) |
| #132 | ✅ |  | chore: 테스트 커버리지 지표 + 3계층 전략 (단위·통합·백테스트) |
| #131 | 🔄 |  | research: 시장 규모(TAM/SAM/SOM) + 경쟁자 비교 + 페르소나 인터뷰 검증 |
| #130 | 🔄 |  | chore: 법무 검토 — 자본시장법·특금법·KRX 시장교란 외부 배포 시 |
| #129 | 🔄 |  | feat: 온보딩 마법사 + 거래소 API 키 발급 가이드 + 권한 체크리스트 |
| #128 | ✅ |  | chore: 자동 업데이트 채널 (GitHub Release + SHA256 + 다중 서명 + 롤백) |
| #127 | ✅ |  | feat: Binance OCO + KIS 조건부 주문 사전 등록 (PC 다운 포지션 보호) |
| #126 | ✅ |  | feat: 텔레그램 봇 양방향 제어 (/kill /release /status /policy) |
| #125 | ✅ |  | feat: FastAPI 로컬 대시보드 (4사분면 + Prometheus 메트릭 endpoint) |
| #124 | ✅ |  | feat: DPAPI 기반 API 키 저장소 + 키 회전 UI |
| #123 | ✅ |  | feat: EXE 패키징 PoC (PyInstaller 단일 .exe 빌드 파이프라인) |
| #122 | ✅ |  | feat: 메타라벨러 재학습 윈도우 + 자동 롤백 조건 검증 |
| #121 | ✅ |  | chore: extreme_fear_threshold 가격 기반 프록시 백테스트 검증 |
| #120 | ✅ |  | chore: per_portfolio_risk 주기 평가기 watchdog + 알림 |
| #119 | ✅ |  | research: 월 10% 수익률 목표 가능성 평가 + 전략·리스크·사이징 재설계 |
| #114 | ✅ | enhancement | feat: Implementation Shortfall 사전 추정 + TCA 메트릭 (특허 #84-4 차용) |
| #113 | ✅ | enhancement | feat: TWAP 볼라틸리티 레짐 적응 + KRX VI 게이트 (특허 #84-3 차용) |
| #112 | ✅ | enhancement | feat: OrderRouter 비용 기반 동적 라우팅 (특허 #84-2 차용) |
| #111 | ✅ | enhancement | feat: VWAP 볼륨 프로파일 실시간 blend (특허 #84-1 차용) |
| #110 | ✅ | enhancement | feat: Partial fill 지원 (MockMatchingEngine partial_fill_enabled=True) |
| #109 | ✅ | enhancement | feat: 슬리피지 모델 활성화 (SquareRootImpact) — MockMatchingEngine Phase 2+ 확장 |
| #108 | ✅ | enhancement | chore: KillSwitch threading.Lock → asyncio.Lock 전환 (Phase 3+ 멀티스레드 대비) |
| #107 | 🔄 | enhancement | feat: 라이브 실행 프레임워크 Phase 3 Live Pilot — 실자금 5% (#105 후속) |
| #106 | ✅ | enhancement | chore: Binance Futures historical data loader (#80 Phase E Sharpe 비교 의존) |
| #105 | ✅ | enhancement | feat: 라이브 실행 프레임워크 Phase 2 — KIS 모의계좌 + AsyncOrderRouter (#80 후속) |
| #99 | ✅ | enhancement | feat: 신규 전략 — 이랑이 VWMA100 단타 기법 카탈로그 (research + vwma-cross-v1) |
| #97 | ✅ |  | feat: 메타라벨러 × KIS 교차 검증 — BTC/KRX 두 자산군 DSR·PR 비교 |
| #96 | ✅ |  | feat: KIS 분봉 시세 fetcher + momo_kis_v1 전략 (KRX 메타라벨러 선행) |
| #95 | ✅ |  | feat: 메타라벨러 월별 자동 재학습 + 드리프트 감지 파이프라인 |
| #94 | ✅ |  | feat: 메타라벨러 프로덕션 활성화 (오케스트레이터 주입 + A/B 등록) |
| #87 | ✅ | enhancement | chore: 특허 조사(#84) 차용 리팩토링 일괄 — 리스크·유니버스 강화 |
| #86 | ✅ |  | chore: 엔드투엔드 프로젝트 기획서 작성 (docs/whitepaper/qta-master-plan-v01.md) |
| #85 | ✅ | enhancement | feat: 메타라벨링 레이어 (LightGBM 2차 필터 + purged CV + walk-forward) |
| #84 | ✅ | documentation | chore: 타 AI/자동매매 특허 리서치 — 시스템 강화 + 회피설계 근거 |
| #81 | ✅ |  | chore: 팩터 점증 계산 + momo-btc-v2 훅 마이그레이션 |
| #80 | ✅ | enhancement | feat: 라이브 실행 프레임워크 (PaperBroker + Phase 1 Shadow Paper) |
| #79 | ✅ | enhancement | feat: 전략 카탈로그 확장 (Mean Reversion + Channel Breakout + Vol-filtered Momentum) |
| #78 | ✅ | enhancement | feat: 멀티 전략 비동기 실행 오케스트레이터 (전략 스케줄링 + 리스크/사이저 배선) |
| #76 | ✅ | enhancement | feat: Signal 인터페이스 확장 — 전략 확신도·기대수익·승률을 sizer로 전달 |
| #74 | ✅ |  | feat: 기업가치 분석 (밸류에이션) research + KIS API 재무 조회 연동 |
| #73 | ✅ | enhancement | feat: 브로커 어댑터 async 마이그레이션 (#68 후행) |
| #71 | ✅ | enhancement | feat: 알파 팩터 파이프라인 (피처 엔지니어링 프레임워크) |
| #70 | ✅ | enhancement | feat: 포트폴리오 리스크 관리 (CVaR + 상관 매트릭스) |
| #69 | ✅ | enhancement | feat: 포지션 사이징 구현 (Kelly + vol targeting) |
| #68 | ✅ | enhancement | feat: 브로커 API 커넥터 (Binance Futures / KIS) |
| #67 | ✅ | enhancement | feat: 마켓 데이터 수집 + Zipline 백테스트 + momo-btc-v2 실행 |
| #62 | ✅ |  | chore: 리서치 스프린트 3 — PIT · Corporate Actions · Paper-to-Live · Market Regime |
| #61 | ✅ |  | chore: 리서치 스프린트 2 — GraphRAG · LLM 실무 가드레일 · FIBO 대조 |
| #60 | ✅ |  | chore: 리서치 스프린트 1 — 볼트 위키링크 백필 + 진짜 퀀트 갭 (position sizing · portfolio risk) |
| #55 | ✅ | enhancement | feat: Protégé + GraphDB 연동 (온톨로지 GUI 편집·SPARQL 서버) |
| #54 | ✅ | enhancement | feat: SHACL 제약 기반 고급 검증 (CI fail 모드) |
| #53 | ✅ | enhancement | feat: LLM 에이전트 자동 노트 생성 (백테스트·인시던트·포스트모템) |
| #52 | ✅ | documentation,enhancement | chore: docs 전체 프론트매터 일괄 마이그레이션 + CI strict 전환 |
| #51 | ✅ | enhancement | feat: Obsidian 볼트 MCP 서버 노출 (LLM 에이전트 연동) |
| #48 | ✅ |  | [chore] 누락 .ai.md 2건 추가 (docs/runbooks, grafana/dashboards) |
| #47 | ✅ | documentation,enhancement | feat: Obsidian 지식볼트 + 트레이딩 온톨로지 구현 |
| #45 | ✅ |  | [chore] AGENTS.md 레포 구조 트리 최신화 |
| #31 | ✅ |  | [research] Snowflake UGM 2026 대체데이터 트레이딩 데모 참고 자료 기록 |
| #30 | ✅ |  | [research] LLM 에이전트 레이어 탐색 (Agentic Trading) |
| #29 | ✅ |  | [research] 양자 PoC 설계 (Phase 4 옵션, QAOA 포트폴리오 최적화) |
| #28 | ✅ |  | [feat] 세금·회계 자동화 (KR 개인 양도세·연말 신고) |
| #27 | ✅ |  | [feat] Kill Switch & DR 런북 |
| #26 | ✅ |  | [feat] 관측성 스택 (Prometheus/Grafana/Loki/알림) |
| #25 | ✅ |  | [feat] 실행 알고리즘 (TWAP/VWAP/지정가·KRX 단일가 구간) |
| #24 | ✅ |  | [feat] 리스크 룰 DSL 설계 (YAML 기반 한도 정책) |
| #23 | ✅ |  | [research] 피처·알파 소스 카탈로그 |
| #22 | ✅ |  | [research] 백테스트 검증 프로토콜 (walk-forward·purged K-fold) |
| #21 | ✅ |  | [research] 백테스트 엔진 선택·비교 (Zipline/Backtrader/LEAN/Nautilus) |
| #20 | ✅ |  | [feat] 데이터 레이크 스키마 설계 (OHLCV·호가·체결·팩터) |
| #19 | ✅ |  | [research] 브로커 Open API 비교·선정 (KIS / 키움 / LS) |
| #9 | ✅ |  | 자동매매 시스템 구성요소 개괄 (데이터→신호→주문→리스크→모니터링) |
| #8 | ✅ |  | 트레이딩 전략 패러다임 개괄 (규칙기반/통계/ML/양자) |
| #7 | ✅ |  | 주식 시장 구조 기초 리서치 (호가·체결·유동성·KRX 특성) |
| #6 | ✅ |  | 왜 지금 퀀텀 트레이딩인가 — 필요성·한계·현실성 (2026) |
| #5 | ✅ |  | 자동매매 vs 퀀트 vs 퀀텀 — 차이와 관계 정리 |
| #4 | ✅ |  | 주식 자동매매(Algorithmic Trading)란 무엇인가 — 정의·분류·개인 투자자 관점 |
| #3 | ✅ |  | 퀀텀 트레이딩(Quantum Trading)이란 무엇인가 — 현재 수준과 풀려는 문제 |
| #2 | ✅ |  | 용어 정의 리서치: 퀀트(Quant) vs 퀀텀(Quantum) 트레이딩 |
| #1 | ✅ |  | 자동매매 프로그램 구현을 위한 선행 리서치 + 구현 플랜 초안 |

**진척도: 107/123 완료 (87.0%)**
<!-- progress-table:end -->

> v0.1 발행 후 부록 B Known Concerns 와 VC 검증 체크리스트에서 21건 신규 백로그 (#119-#140, #133 제외) 도출. 진행/백로그 12건 → 34건. 진행률은 81% 에서 60% 로 재계산 (분모 확장).

### §11-4. 시간축 마일스톤

```
2025-Q4         2026-Q1         2026-Q2 (현시점)   2026-Q3      2026-Q4      2027-Q1-Q2
인프라·리서치   엔진·신호·       메타라벨러+        Phase 2     Phase 3      Phase 4
(#1-31, #45-55) 리스크 (#67-81) 특허+Phase 1      (#105)      (#107)        (EXE)
                              (#85-#106)
✓ 완료         ✓ 완료          ✓ 완료 / 진행 중   ⏳ 미착수    ⏳ 미착수     TBD
```

### §11-5. Phase 1 그림자 운영 가동 실적

| 항목 | 값 |
|------|---|
| 시작 시점 | TBD (사용자 입력 대기) |
| 누적 거래일 | TBD |
| 목표 | 30 거래일 무사고 |
| 다음 마일스톤 | 안정성 ≥ 0.7 → Phase 2 진입 결정 |

> v0.2 발행 시 실제 시작일·누적일 기재.

### §11-6. 수익 모델 (TBD — Use of Funds 가정용)

| 옵션 | 단위 경제학 | 적합 시점 |
|------|------------|----------|
| B2C 구독 | 월 N만 원, 전략·자산군 티어 | Phase 4 정식 운영 후 |
| B2B 라이선싱 | 증권사·운용사 엔진 라이선스 | Phase 4 안정 운영 1년+ |
| 자기자본 운용 수익 | 자체 매매 수익 | Phase 3+ |

### §11-7. Exit 시나리오 (TBD)

| 후보 | 시간 프레임 |
|------|------------|
| 핀테크 M&A (증권사 인수) | Phase 4 + 1–2년 |
| 글로벌 트레이딩 플랫폼 인수 | Phase 4 + 2–3년 |
| B2B SaaS IPO | Phase 4 + 3–5년 |

### §11-8. 마일스톤별 KPI

| Phase | 핵심 KPI |
|-------|---------|
| 1 | 30 거래일 무사고 누적 |
| 2 | 모의계좌 실주문 100건 성공, 슬리피지 reconcile |
| 3 | 60 거래일 안정성 ≥ 0.5, 세금 신고 양식 검증 |
| 4 | EXE 배포 + 외부 사용자 10명 + 코드 서명 인증 |

### §11-9. 의존성·블로커

| 상태 | 항목 |
|------|------|
| ✅ 해결 | #85, #80, #94, #87, #106 |
| ⏳ 진행 | #105 (Phase 2 기초), #107 (Phase 3 기초), #108 (asyncio.Lock) |
| 🔴 P0 블로커 | **#119 월 10% 가능성 평가** — 모든 후속 전략·리스크·사이징 결정 선행. #120 (per_portfolio_risk watchdog), #121 (extreme_fear 검증), #122 (메타라벨러 재학습 윈도우) — Phase 2 진입 전 필수 |
| ⚠️ Phase 별 블로커 | #109 슬리피지 모델 (Phase 2 전), #110 부분 체결 (Phase 3 전), #127 OCO/조건부 (Phase 3 전), #130 법무 검토 (Phase 4 외부 배포 전), #138 Whitepaper v0.2 (Phase 1 종료 시) |

### §11-10. 리스크 및 완화

| 리스크 | 완화 |
|--------|------|
| 시장 검증 부재 | Phase 1–2 30거래일 × 2단계, Phase 3 5% 한도 |
| 1인 운영 리스크 | AI 에이전트 보조, 노트화·CI 자동검증, 외부 자문 v0.2 |
| 규제 불확실성 | KRX throttle 코드 강제, B2C 진입 시 법무 재검토 |
| 해외 거래소 정규화 | 국내 선물거래소 어댑터 확장 (Phase 4) |
| API 장애 | Kill Switch + 텔레그램 + Phase 3+ 서버측 OCO |

---

## 부록 A — VC 검증 체크리스트 자체 평가

| 영역 | # | 질문 | 위치 | 충분도 |
|------|---|------|------|--------|
| 시장 | M1 | TAM/SAM/SOM | §1-3 | 가설 — v0.2 검증 |
| | M2 | 페르소나 | §1-4 | 충분 (2명) |
| | M3 | 경쟁자 분석 | (TBD v0.2) | 미비 |
| 제품 | P1 | 사용자 여정 | §10-7 | 충분 |
| | P2 | 기술 차별화 | §1-6 | 충분 |
| | P3 | LLM 역할 경계 | §1-7, §4-3, §6-1 | 충분 (불변식 6) |
| 트랙션 | T1 | 진척도 정량 | §11-3 | 충분 (52/64) |
| | T2 | 백테스트 실측 | §5-1 | 솔직 명시, v0.2 |
| | T3 | 그림자 가동일 | §11-5 | TBD — 사용자 입력 |
| | T4 | 코드 품질 지표 | (TBD v0.2) | 미비 |
| 팀 | K1 | 1인 운영 완화 | §1-9 | 충분 |
| | K2 | 도메인 전문성 | §1-9 | 부분, v0.2 보강 |
| | K3 | 외부 자문 | §1-9 | v0.2 확보 |
| 리스크 | R1 | 규제 | §3-6 | 충분 |
| | R2 | 기술 | §7, §8 | 충분 |
| | R3 | 시장 시나리오 | §8-3 | 충분 (4) |
| | R4 | 유동성 | §3-5, §9-2 | 충분 |
| | R5 | 운영 (PC·네트워크) | §10-2 | 부분, Phase 3+ |
| 자금 | F1 | 자금 사용 | §11-6 | 옵션 검토 (TBD) |
| | F2 | KPI | §11-8 | 충분 |
| 수익·Exit | E1 | 수익 모델 | §11-6 | 옵션 (TBD) |
| | E2 | Exit | §11-7 | 후보 (TBD) |
| | E3 | Moat | (TBD v0.2) | 미비 |
| 기술 실사 | D1 | 확장성 | §10-2 | 부분 |
| | D2 | 데이터 의존성 | §3-2, §9 | 부분 |
| | D3 | 테스트 전략 | (TBD v0.2) | 미비 |

| 영역 | 점수 (5) |
|------|---------|
| 시장 이해 | 3.0 |
| 제품 완성도 | 4.0 |
| 트랙션 | 3.0 |
| 팀 | 2.5 |
| 리스크 관리 | 4.0 |
| 수익·Exit | 2.0 |
| 기술 실사 | 3.0 |
| **종합** | **3.07/5** |

> 기술 백서로 우수, 사업 계획서로 v0.2 확정 항목 다수.

---

## 부록 B — Known Concerns (정직한 약점)

### B-1. 트레이더

- BTC vs KRX 상관 위기 구간 급등 — 분산 효과 제한적. §8 fear/greed 차단으로 부분 완화.
- momo-btc-v2 백테스트 0.18 — 메타라벨러 ON 후 재검증 결과는 Phase 1 30거래일 데이터로 확정 (v0.2).
- ENB 측정 최소 3 전략 필요. 라이브 1 전략 단계에서 측정 불가 → halt 트리거 비활성.

### B-2. 개발자

- 분수 켈리 rolling fallback 모멘텀 win_rate 급락 (실데이터 64.7%→41.7%) — Signal-wins 인터페이스 (#76) 가 해소책. 구형 전략 미사용.
- `extreme_fear_threshold=0.2` 가격 기반 프록시 — 실제 공포·탐욕 지수 상관 미검증. Phase 2 전 백테스트 검증.
- `per_portfolio_risk` 주기 평가기 실패·지연 시 no-op 사일런스 위험 — watchdog + 알림 추가 필요.
- 슬리피지 0% Phase 1 한정. Phase 2 전 SquareRootImpact 모델 (#109) 활성화 필수.
- 부분 체결 (#110) Phase 3 전 최소 구현 필요 — KRX 저유동성 종목 큰 주문 포지션 불일치 위험.

### B-3. VC

- PyInstaller 백신 오탐 — 코드 서명 인증서 (EV) 예산·절차 Phase 4 전 확정.
- 단일 프로세스 PC 절전·네트워크 단절 취약 — Phase 3+ Binance OCO + KIS 조건부 사전 등록.
- DPAPI 같은 계정 악성코드 무력 — IP whitelist + 출금 권한 제외 온보딩 강제.
- localhost 대시보드 모바일 불가 — 텔레그램 봇 양방향 보완.
- 자동 업데이트 공급망 공격 — Phase 4 전 재현 빌드 + 다중 서명 설계.
- 주간 재학습 7일 표본 적음 — 윈도우 크기·자동 롤백 조건 v0.2 검증.
- 온보딩 API 키 발급 별도 작업 — 거래소별 가이드 + 권한 체크리스트 필요.
- 시장 규모(TAM/SAM/SOM)·경쟁자·Moat 가설 수준 — v0.2 사용자 인터뷰 검증.

### B-4. 마케터

- Phase 1 그림자 운영 가동일·누적 거래일 미명시 — 사용자 입력 후 §11-5 채움.
- 연 12–18% 수익률 가정 백테스트 기반 — 실거래 (Phase 3+) 데이터로 v0.2+ 갱신.

### B-5. 브랜드

- §0-6 저작권 단락 짧음 — 오픈소스 전환 가능성 시 v0.2 갱신.
- §2-11 특허 차용 법적 고지 본문 한 줄 추가 (반영 완료).

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|------|------|------|
| 0.1 | 2026-04-26 | 초판. Phase 0 완료 + Phase 1 그림자 운영 시작 시점. 5인 다관점 워커 + CEO 통합 |
