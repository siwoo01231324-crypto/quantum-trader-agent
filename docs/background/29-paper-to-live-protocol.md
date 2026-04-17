---
type: research
id: 29-paper-to-live-protocol
name: "Paper → Live 전환 프로토콜 — Shadow Trading · 단계적 실자금 투입"
sources:
  - https://www.investopedia.com/terms/p/papertrade.asp
  - https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
  - https://www.cftc.gov/PressRoom/PressReleases
  - https://www.finra.org/rules-guidance/rulebooks/finra-rules/4512
  - https://nautilustrader.io/docs/latest/concepts/backtest_live_consistency.html
  - https://www.bis.org/publ/bcbs128.htm
---

# Paper → Live 전환 프로토콜 — Shadow Trading · 단계적 실자금 투입

> [[12-validation-protocol]] §3.8 은 "최소 2개월 paper-trading → 실자금 소액 pilot (portfolio의 5%) → 단계적 스케일업" 을 한 줄로 명시한다. [[kill-switch-runbook]] §4 는 "페이퍼 트레이딩 1세션 무결함" 을 복구 체크리스트에 포함한다. 그러나 **어떤 조건에서 다음 단계로 갈지** 는 문서화 안 됨. 본 노트는 (1) 4단계 전환 프레임워크, (2) 각 단계의 통계 검증 기준, (3) 롤백 트리거, (4) [[kill-switch-dr]] 연계를 구체화한다.

---

## 1. 왜 별도 프로토콜이 필요한가

백테스트 만으로는 다음 리스크가 드러나지 않는다:

1. **실시간 데이터·주문 경로 결함** — WebSocket 끊김, 호가 데이터 지연, idempotency-key 관리 버그
2. **실거래 슬리피지** — 백테스트 모델 (Almgren-Chriss, constant-bps) 과 실제 차이
3. **심리적·운영적 요인** — 포지션 확인 빈도, 알림 설정, 장 운영시간 공백
4. **부분 체결·주문 취소 경쟁** — 백테스트는 100% 체결 가정하기 쉬움

전환 프로토콜은 이 리스크들을 **순차적으로 검출·완화** 하는 파이프라인이다.

---

## 2. 4단계 프레임워크

```
[Phase 0] 백테스트 승인
     ↓
[Phase 1] Shadow Paper   (가상 주문, 실 시세 수신)
     ↓
[Phase 2] Live Paper     (브로커 모의계좌 실주문)
     ↓
[Phase 3] Live Pilot     (실자금 소액 = equity × 5%)
     ↓
[Phase 4] Full Production (단계적 스케일업: 5% → 25% → 50% → 100%)
```

각 단계는 **종료 기준 (exit criteria)** 과 **롤백 트리거 (regression criteria)** 를 가진다. 단계 승격은 사람 승인 2인 + [[risk-rule-dsl]] 정책 업데이트 필수.

---

## 3. Phase 1 — Shadow Paper (가상 주문)

### 3.1 정의
- 실 시세 WebSocket 구독 ([[data-lake-schema]] ingest 경로)
- 주문은 **브로커에 전송 안 함** — `PaperBroker` 어댑터가 가상 체결 시뮬
- 체결 시뮬은 호가창 스냅샷 기반 slippage 모델 ([[execution-algorithms]] §3.1)

### 3.2 기간
- **최소 10 거래일** (2 거래주)
- 거래 발생 주문이 N ≥ 30 건 이상이어야 통계 의미

### 3.3 Exit Criteria
- [ ] WebSocket 단절 자동 재연결 정상 (≥ 1회 검증)
- [ ] 시세 lag > 500ms 발생률 < 5%
- [ ] 모든 체결이 PaperBroker 로그에 남음 (누락 0)
- [ ] 백테스트 Sharpe vs Shadow Paper Sharpe 차이 ≤ 0.3 (비용 포함)
- [ ] [[kill-switch-dr]] 자동 트리거 3종 (DD 한도·API 오류·이상 체결) 테스트 통과

### 3.4 롤백 트리거
- WebSocket 재연결 실패 2회 이상
- 체결 누락 1건 이상 (log vs PaperBroker state 불일치)
- Sharpe 괴리 > 0.5 → 슬리피지 모델 재검증 필요

---

## 4. Phase 2 — Live Paper (브로커 모의계좌)

### 4.1 정의
- 실제 증권사 API ([[10-broker-api-comparison]]) 의 **모의계좌** 사용 (KIS·키움 모두 제공)
- 주문·체결·포지션 조회 모두 실 API 경로, 자금만 가상
- **API rate limit·오류·지연** 을 실측

### 4.2 기간
- **최소 4 거래주 (20 거래일)**
- 주문 건수 N ≥ 60

### 4.3 Exit Criteria
- [ ] 주문 성공률 ≥ 99% (거부·rate limit 포함)
- [ ] 주문 발주 → ACK 지연 p95 < 2초
- [ ] 체결 통보 (WebSocket) 누락 0건
- [ ] PnL 계산이 모의계좌 잔고 변화와 일치 (오차 0.1% 이내)
- [ ] 모든 [[kill-switch-runbook]] 절차 1회씩 실행 (트리거·수동·청산·복구)
- [ ] [[observability]] 10종 메트릭 모두 정상 송출
- [ ] Shadow 대비 Sharpe 유지 (차이 ≤ 0.2)

### 4.4 롤백 트리거
- 주문 성공률 < 95%
- API 오류 로그에서 **미인지 예외** (handler 에 없는 에러 코드)
- 모의계좌 잔고 불일치 (포지션 관리 버그)

---

## 5. Phase 3 — Live Pilot (실자금 5%)

### 5.1 정의
- **실자금 계좌** 연결, 포지션 크기를 `equity × 5%` 로 상한 설정
- [[risk-rule-dsl]] 에 `per_portfolio.max_gross_exposure_krw = equity * 0.05` 추가
- 주문 경로·체결·결제 모두 실제

### 5.2 기간
- **최소 8 거래주 (40 거래일)**
- 주문 건수 N ≥ 100
- 주요 시장 상황 2가지 이상 포함 권장 (상승·하락·변동 확대 구간)

### 5.3 Exit Criteria — 통계·운영 이중 체크

**통계 기준 (3개 모두 만족)**:
- [ ] Realized Sharpe (비용 포함) ≥ backtest × 0.7
- [ ] MDD ≤ backtest × 1.5
- [ ] Tracking Error vs Shadow Paper Sharpe ≤ 0.5

**운영 기준 (모두 만족)**:
- [ ] 세금·수수료 계산 결과가 [[tax-automation]] 모듈 출력과 실 증권사 명세와 일치 (오차 10원 이내)
- [ ] 모든 [[27-corporate-actions]] 이벤트 처리 (배당락·권리락 1회 이상 실제 경험)
- [ ] [[kill-switch-dr]] 자동 트리거 실제 발동 사례 0건 (혹은 발동 시 runbook 대로 100% 복구)
- [ ] [[19-portfolio-risk]] ENB ≥ 0.3 × N 유지
- [ ] [[20-position-sizing]] Half Kelly 상한 위반 0건

### 5.4 롤백 트리거 (자동 halt + 사람 검토)

- Realized Sharpe < 0 (2개월 이동)
- MDD > backtest MDD × 2
- 일간 실제 손실이 [[risk-rule-dsl]] `drawdown.max_intraday_dd_pct` 초과
- Tracking Error > 1.0 (모의 vs 실 간 큰 괴리)
- API 장애로 인한 청산 지연 > 5분
- **이상 체결 패턴** (1초 내 동일 종목 5건 이상) — [[kill-switch-dr]] §4 연계

---

## 6. Phase 4 — Full Production (단계적 스케일업)

### 6.1 스케일업 트랙

| 마일스톤 | 허용 자본 비중 | 최소 기간 | 추가 검증 |
|---------|---------------|-----------|-----------|
| M1 | 5% → 10% | 4주 | Phase 3 통계 기준 유지 |
| M2 | 10% → 25% | 6주 | 시장 체제 ([[30-market-regime-detection]]) 2종 이상 통과 |
| M3 | 25% → 50% | 8주 | 유동성 스트레스 테스트 (공매도 금지·서킷브레이커 유사 상황) |
| M4 | 50% → 75% | 12주 | 외부 감사·회계 검증 1회 통과 |
| M5 | 75% → 100% | 결정 회의 | PnL·리스크 12개월 전체 리뷰 |

### 6.2 각 단계 Exit Criteria

- 이전 단계 통계 기준 유지 + 자본 증가로 인한 **시장 충격 검증**
- 주문량 증가 → 평균 체결가 변화 (실측)
- 레이턴시 변화 없음 ([[observability]] p95)
- [[risk-rule-dsl]] 의 `per_portfolio.max_gross_exposure_krw` 값을 각 단계마다 업데이트

### 6.3 "언제 중단할 것인가" (Kill-switch 연계)

M1~M5 중 어느 단계에서든 아래 시 **즉시 Phase 3 수준 (5%) 으로 롤백**:
- 실거래 Sharpe vs 백테스트 Sharpe 차이 > 1.5 (6개월 rolling) — [[12-validation-protocol]] §4
- [[kill-switch-dr]] P0 발동 1회 이상
- 규제·세법 변경으로 [[tax-automation]] 재설계 필요

---

## 7. 통계 검증 상세 — Realized Sharpe vs Backtest

### 7.1 공정 비교 조건

- 같은 [[data-lake-schema]] 스냅샷 기준 (PIT 보존)
- 같은 [[execution-algorithms]] 슬리피지 모델
- 같은 거래세·수수료 처리 ([[tax-automation]])
- 같은 [[20-position-sizing]] 규칙

### 7.2 DSR 역치 하향 조정

[[12-validation-protocol]] 는 백테스트 DSR ≥ 0.95 요구. Realized 는 표본 크기 작으므로:

```
기간              최소 주문수    DSR 역치
Phase 1 (Shadow) N ≥ 30        DSR ≥ 0.75 (weak)
Phase 2 (Paper)  N ≥ 60        DSR ≥ 0.80
Phase 3 (Pilot)  N ≥ 100       DSR ≥ 0.85
Phase 4 (Prod)   N ≥ 250       DSR ≥ 0.90
```

표본이 작을수록 DSR 불확실성 크므로 관대하게 운영, 단 rolling window 로 지속 모니터링.

### 7.3 Tracking Error 공식

```
TE = stddev(r_realized − r_backtest_parallel)
```

`r_backtest_parallel` = 실 거래와 **동일 기간** 백테스트 재실행 결과 (샘플 밖 bias 제거).

### 7.4 로그 저장

모든 단계의 PnL·포지션·주문 기록은 `docs/work/done/live-transition/{phase}/` 에 week-단위 리포트로 보존. 이는 사람 검토 + [[services/doc_agent]] 의 draft 입력.

---

## 8. 운영 체크리스트

### 8.1 단계 승격 체크리스트 (공통)

- [ ] 이전 단계 Exit Criteria 전체 통과
- [ ] 운영자 2인 이상 승인 (slack thread 보존)
- [ ] [[risk-rule-dsl]] 정책 파일 업데이트 PR (리뷰·머지 완료)
- [ ] [[kill-switch-runbook]] 최신 버전 숙지 확인
- [ ] 비상 연락망 업데이트
- [ ] [[observability]] 대시보드에 새 단계 태그 추가

### 8.2 주간 리뷰 의제

- 누적 PnL·Sharpe·MDD
- Tracking Error 추세
- API 오류·WebSocket 단절 발생률
- [[kill-switch-dr]] 자동 트리거 발생 이력
- [[27-corporate-actions]] 이벤트 처리 결과
- 시장 체제 변화 ([[30-market-regime-detection]]) 추적

### 8.3 Phase 3+ 사람 승인 게이트

LLM 에이전트 ([[15-llm-agent-layer]]) 는 **실자금 단계 전환 의사결정에 직접 개입 금지.** LLM 은 통계 요약·이상 감지 보조만. 최종 승인은 항상 사람.

---

## 9. 자주 발생하는 실수 모음

1. **"Sharpe 가 백테스트보다 높다 → 성공"** 착시 — 작은 표본 과신. DSR·PBO 재검 필수
2. **단계 건너뛰기** — Phase 2 (브로커 모의) 를 생략하고 바로 Pilot. API 오류 패턴 미검증 → 대참사 위험
3. **세금·수수료 지연 반영** — 백테스트는 월 집계, 실거래는 매 건. 월말 정산 시 불일치 발견
4. **시장 체제 한 가지에서 검증** — 상승장만 거친 전략이 하락장에서 무너짐. Phase 3 에서 최소 2체제 통과 요구
5. **LLM 보조 자동화 과신** — 이상 감지 알림 자동 무시·batching 후 인간 검토 생략 → [[24-llm-agent-safety-finance]] §3.2 "Tool misuse" 에 해당
6. **롤백 기준 모호** — "뭔가 이상하면 멈춘다" 는 작동 안 함. 수치 기반 자동 트리거 필수

---

## 10. 로드맵 (본 프로젝트 타임라인)

- **Phase 0 (즉시)**: 본 노트의 Exit Criteria 를 [[risk-rule-dsl]] 의 `transition_phase` YAML 필드로 표현
- **Phase 1 (2주)**: PaperBroker 어댑터 완성, shadow 실행
- **Phase 2 (4주)**: KIS 모의계좌 연동 → 4주간 실측
- **Phase 3 (8주)**: 실자금 5% pilot — [[10-broker-api-comparison]] 선정된 브로커
- **Phase 4 (6개월~)**: 단계적 스케일업 M1~M5

---

## 관련 노트

- [[12-validation-protocol]] — §3.8 라이브 전환 요약을 본 노트가 상세화
- [[kill-switch-dr]] — 각 단계 롤백 트리거가 본 스펙의 `trip()` 과 연동
- [[kill-switch-runbook]] — Phase 2+ 에서 반드시 1회 이상 실제 실행
- [[risk-rule-dsl]] — 각 단계의 per_portfolio 한도·drawdown 기준 적용
- [[observability]] — 통계·운영 메트릭 수집
- [[execution-algorithms]] — 슬리피지 모델 백테스트 vs 실거래 일치성
- [[10-broker-api-comparison]] — Phase 2·3 브로커 선정
- [[20-position-sizing]] — 단계별 사이징 정책 적용
- [[30-market-regime-detection]] — Phase 4 M2 이상에서 체제 다양성 요구
- [[26-point-in-time-data]] — PIT 기반 backtest_parallel 재실행
- [[27-corporate-actions]] — Phase 3 에서 실제 이벤트 1회 이상 경험 요구
- [[tax-automation]] — 세금·수수료 일치 검증 연계

---

## 출처

- López de Prado, M. (2018). *Advances in Financial Machine Learning*. Ch. 15 (Backtesting on Synthetic Data) + Ch. 16 (ML asset allocation). 백테스트 → 실거래 괴리 이론
- Bailey, D.H. & López de Prado, M. (2014). *Deflated Sharpe Ratio*. <https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551>
- Harvey, C. R. & Liu, Y. (2015). *Backtesting*. Journal of Portfolio Management 41(1).
- NautilusTrader — *Backtest-Live Consistency*. <https://nautilustrader.io/docs/latest/concepts/backtest_live_consistency.html>
- Investopedia — *Paper Trade*. <https://www.investopedia.com/terms/p/papertrade.asp>
- CFTC — *Automated Trading Systems: Risk Controls* (Reg AT proposals). <https://www.cftc.gov/PressRoom/PressReleases>
- FINRA Rule 4512 — *Customer Account Information* (실계좌 운영 요구사항). <https://www.finra.org/rules-guidance/rulebooks/finra-rules/4512>
- Basel Committee on Banking Supervision — *Minimum Capital Requirements for Market Risk (FRTB)*. <https://www.bis.org/publ/bcbs128.htm>
- SEC — *Knight Capital Group Order Routing* (2012 사고 — 미검증 라이브 배포 교훈). <https://www.sec.gov/litigation/admin/2013/34-70694.pdf>
- 금융위원회·KRX — *알고리즘 거래 시장 영향 분석 보고서* (국내 운영 맥락)
- Thorp, E. O. (1997). *The Kelly Criterion*. Phase 3 의 5% 룰과 Kelly 가드의 관계 참조
